"""Pure grading rules (spec sections 4, 5, 8.3, 12 and 13).

Grades are integers, 1 best and 3 lowest. A pillar that cannot be measured
is INSUFFICIENT. The only I/O is ReferenceBook saving itself when given
a path.
"""

import json
import os
import statistics
from pathlib import Path

INSUFFICIENT = "insufficient"
NOT_MEASURED = "not measured (point grade)"
UNRATED = "unrated"

PILLAR_ORDER = ("speed", "consistency", "reliability")

# Speed, spec 5.1: award bar and keep bar for each grade.
SPEED_AWARD = {1: 0.95, 2: 0.85}
SPEED_KEEP = {1: 0.92, 2: 0.82}

# Consistency, spec 5.2: (Grade 1 limit, Grade 2 limit); lower is better.
CONSISTENCY_LIMITS = {
    "cv": (0.03, 0.07),
    "decay": (0.05, 0.10),
    "p99_p50": (2.0, 3.0),
    "isolation_drop": (0.05, 0.12),
}

# Reliability, spec 5.3: allowed downtime hours per 30 days.
DOWNTIME_LIMITS = (3.6, 14.4)
RELIABILITY_MIN_COVERAGE_DAYS = 25

# Spec 13: provisional references need this many distinct rigs.
MIN_REFERENCE_RIGS = 5
# Operator concentration (spec 13; numbers accepted as v0).
CAP_PER_OPERATOR = 2
MIN_OPERATORS = 3
MAX_OPERATOR_SHARE = 0.40

# Spec 5.5: re-check matching tolerance on the speed ratio.
RECHECK_SPEED_TOLERANCE = 0.03

# Spec 8.3: identity mismatch threshold against the class envelope.
IDENTITY_TOLERANCE = 0.25

# Spec 12: promise words never allowed in label, badge or listing text.
PROMISE_WORDS = ("guaranteed", "guarantee", "certified", "certification",
                 "warranty", "safe")

GATE_CHECKS = {
    "inference": ("memory", "memory_integrity", "gemm", "memory_bandwidth",
                  "host_link"),
    "training": ("memory", "memory_integrity", "gemm", "memory_bandwidth",
                 "host_link", "card_link"),
}
# Free memory the rulers need. Spec v0 said 24 GB for inference, which no
# 24 GB card can show free after driver load; corrected in spec v0.1.
GATE_MIN_FREE_GB = {"inference": 20, "training": 12}


def gate(workload, gate_results, free_memory_gb, card_count):
    """Spec 4. Returns (passed, [failed check names])."""
    failed = []
    if free_memory_gb is None or free_memory_gb < GATE_MIN_FREE_GB[workload]:
        failed.append("memory")
    for check in GATE_CHECKS[workload]:
        if check == "memory":
            continue
        if check == "card_link" and card_count <= 1:
            continue
        if gate_results.get(check) is not True:
            failed.append(check)
    return not failed, failed


def speed_grade(ratio, held=None, on_watch=False):
    """Spec 5.1 and plan Decision 10. Returns (grade, below_keep).

    `held` is the grade the rig already holds, if any. A held grade is kept
    while the ratio stays at or above its keep bar. The first week below the
    keep bar keeps the held grade and puts it on watch (below_keep True).
    Only a second consecutive week below (`on_watch` already True) lands the
    grade on what the ratio earns.
    """
    if ratio is None:
        return INSUFFICIENT, False
    earned = 3
    for grade in (1, 2):
        if ratio >= SPEED_AWARD[grade]:
            earned = grade
            break
    if held in (1, 2) and earned > held:
        if ratio >= SPEED_KEEP[held]:
            return held, False
        return (earned if on_watch else held), True
    return earned, False


def _limit_grade(value, limits):
    g1, g2 = limits
    if value <= g1:
        return 1
    if value <= g2:
        return 2
    return 3


def coefficient_of_variation(values):
    if len(values) < 3:
        return None
    mean = statistics.fmean(values)
    if mean <= 0:
        return None
    return statistics.pstdev(values) / mean


def consistency_grade(throughput_passes, decay, p99_p50=None,
                      isolation_drop=None):
    """Spec 5.2 and 5.4. The worst of the measures present; INSUFFICIENT
    without at least 3 passes or without the sustained-run decay."""
    cv = coefficient_of_variation(throughput_passes or [])
    if cv is None or decay is None:
        return INSUFFICIENT, {}
    measures = {"cv": cv, "decay": decay}
    if p99_p50 is not None:
        measures["p99_p50"] = p99_p50
    if isolation_drop is not None:
        measures["isolation_drop"] = isolation_drop
    grades = {name: _limit_grade(value, CONSISTENCY_LIMITS[name])
              for name, value in measures.items()}
    return max(grades.values()), measures


def heartbeat_downtime(accepted_ts, window_start, window_end):
    """Spec 7. Seconds of downtime and of coverage in the window.

    A gap over 600 s between accepted heartbeats counts as down for its
    length minus 300 s. Time before the first and after the last accepted
    heartbeat in the window counts as down.
    """
    ts = sorted(t for t in accepted_ts if window_start <= t <= window_end)
    if not ts:
        return window_end - window_start, 0
    down = (ts[0] - window_start) + (window_end - ts[-1])
    for a, b in zip(ts, ts[1:]):
        gap = b - a
        if gap > 600:
            down += gap - 300
    return down, ts[-1] - ts[0]


def reliability_grade(accepted_ts, window_end, window_days=30):
    """Spec 5.3 and 5.4. Returns (grade or INSUFFICIENT, downtime hours)."""
    window_start = window_end - window_days * 86400
    down, coverage = heartbeat_downtime(accepted_ts, window_start, window_end)
    if coverage < RELIABILITY_MIN_COVERAGE_DAYS * 86400:
        return INSUFFICIENT, down / 3600
    hours = down / 3600
    return _limit_grade(hours, DOWNTIME_LIMITS), hours


def overall(pillars, form):
    """Spec 5: the weakest measured pillar caps the grade.

    Speed and consistency are required for any grade. A full grade also
    needs reliability; without it the result falls back to a point grade.
    Returns (grade or INSUFFICIENT, capped_by, form).
    """
    for name in ("speed", "consistency"):
        if pillars.get(name) == INSUFFICIENT:
            return INSUFFICIENT, name, form
    if form == "full" and pillars.get("reliability") in (None, INSUFFICIENT):
        form = "point"
    used = ["speed", "consistency"] + (["reliability"] if form == "full" else [])
    worst = max(pillars[name] for name in used)
    # Nothing caps a grade whose pillars are all at the top.
    capped_by = None if worst == 1 else next(name for name in used if pillars[name] == worst)
    return worst, capped_by, form


def top_grade_rule(grade, capped_by, attested, recheck_matched):
    """Spec 5.5: Grade 1 needs attested evidence or a matching re-check."""
    if grade == 1 and not (attested or recheck_matched):
        return 2, "evidence"
    return grade, capped_by


def recheck_matches(submitted_ratio, submitted_consistency, recheck_ratio,
                    recheck_consistency):
    if None in (submitted_ratio, recheck_ratio):
        return False
    return (abs(submitted_ratio - recheck_ratio) <= RECHECK_SPEED_TOLERANCE
            and submitted_consistency == recheck_consistency)


def identity_mismatch(class_id, measured, envelope):
    """Spec 8.3. `envelope` maps measure name to the class reference value;
    a measure is checked only where both a reference and a measurement
    exist. Returns the list of measures more than 25% off."""
    off = []
    for name, ref in (envelope or {}).items():
        value = measured.get(name)
        if ref and value is not None and abs(value - ref) > IDENTITY_TOLERANCE * ref:
            off.append(name)
    return off


class ReferenceBook:
    """Spec 13: provisional class references from each rig's first session,
    free or paid.

    Only the first session per rig identity counts, so one host cannot steer
    a reference with repeat sessions. Operator concentration (spec 13,
    spec 13): at most CAP_PER_OPERATOR rigs from one operator
    count toward a reference, and speed is not graded until the counted
    rigs come from at least MIN_OPERATORS distinct verified operators. Rigs
    with no verified operator count as rigs but never as operators, so an
    anonymous crowd cannot form the spread. Attested sessions take
    priority: once 5 attested rigs exist, the median uses them alone.
    Controlled reference runs, once they exist, replace the provisional
    median.
    """

    def __init__(self, path=None):
        """With a path, the book is loaded from and saved to that JSON file
        on every change, so references survive a server restart."""
        self._samples = {}
        self._controlled = {}
        self.path = Path(path) if path else None
        if self.path is not None and self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for row in data.get("samples", []):
                c, w, rig, value, attested = row[:5]
                operator = row[5] if len(row) > 5 else None
                self._samples.setdefault((c, w), {})[rig] = (value, bool(attested), operator)
            for c, w, value in data.get("controlled", []):
                self._controlled[(c, w)] = value

    def _save(self):
        if self.path is None:
            return
        data = {"samples": [[c, w, rig, v, a, op]
                            for (c, w), bucket in sorted(self._samples.items())
                            for rig, (v, a, op) in sorted(bucket.items())],
                "controlled": [[c, w, v] for (c, w), v in sorted(self._controlled.items())]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, class_id, workload, rig_id, throughput, attested=False, operator=None):
        """Record a rig's first session. `operator` is the verified wallet
        behind the rig (lower-cased), or None when unclaimed."""
        bucket = self._samples.setdefault((class_id, workload), {})
        if rig_id not in bucket:
            bucket[rig_id] = (throughput, bool(attested), operator.lower() if operator else None)
            self._save()

    def has_rig(self, class_id, workload, rig_id):
        """True when this rig's first session already counts for this class."""
        return rig_id in self._samples.get((class_id, workload), {})

    def set_controlled(self, class_id, workload, throughput):
        self._controlled[(class_id, workload)] = throughput
        self._save()

    def _counted(self, class_id, workload):
        """The rigs that count toward the reference, in arrival order: at
        most CAP_PER_OPERATOR per verified operator; unclaimed rigs all
        count. Returns [(rig_id, throughput, attested, operator)]."""
        per_operator, out = {}, []
        for rig, (v, a, op) in self._samples.get((class_id, workload), {}).items():
            if op is not None:
                if per_operator.get(op, 0) >= CAP_PER_OPERATOR:
                    continue
                per_operator[op] = per_operator.get(op, 0) + 1
            out.append((rig, v, a, op))
        return out

    def concentration(self, class_id, workload):
        """Operator counts for publication (spec 13)."""
        counted = self._counted(class_id, workload)
        by_op = {}
        for _rig, _v, _a, op in counted:
            if op is not None:
                by_op[op] = by_op.get(op, 0) + 1
        largest = max(by_op.values()) if by_op else 0
        return {"rigs": len(counted),
                "operators": len(by_op),
                "unclaimed_rigs": sum(1 for r in counted if r[3] is None),
                "largest_operator_share": (largest / len(counted)) if counted else 0.0,
                "min_rigs": MIN_REFERENCE_RIGS, "min_operators": MIN_OPERATORS,
                "cap_per_operator": CAP_PER_OPERATOR,
                "max_payer_share": MAX_OPERATOR_SHARE}

    def operator_share(self, class_id, workload, operator):
        """Share of the counted rigs behind one operator (0 when unknown)."""
        if not operator:
            return 0.0
        counted = self._counted(class_id, workload)
        if not counted:
            return 0.0
        mine = sum(1 for r in counted if r[3] == operator.lower())
        return mine / len(counted)

    def _median_basis(self, class_id, workload):
        """(median or None, basis text) from the counted rigs."""
        counted = self._counted(class_id, workload)
        operators = {r[3] for r in counted if r[3] is not None}
        attested = [v for _r, v, a, _op in counted if a]
        if len(counted) < MIN_REFERENCE_RIGS:
            return None, "reference-building: speed not graded yet"
        if len(operators) < MIN_OPERATORS:
            return None, ("reference-building: needs rigs from %d operators (has %d)"
                          % (MIN_OPERATORS, len(operators)))
        if len(attested) >= MIN_REFERENCE_RIGS:
            return (statistics.median(attested),
                    "median of first sessions, attested rigs only, one per rig, "
                    "at most %d per operator" % CAP_PER_OPERATOR)
        return (statistics.median(v for _r, v, _a, _op in counted),
                "median of first sessions, one per rig, at most %d per operator"
                % CAP_PER_OPERATOR)

    def basis(self, class_id, workload):
        """What the reference rests on, for publication (spec 13)."""
        key = (class_id, workload)
        if key in self._controlled:
            return {"basis": "controlled reference run", "provisional": False}
        counted = self._counted(class_id, workload)
        info = {"provisional": True,
                "attested_rigs": sum(1 for r in counted if r[2])}
        info.update(self.concentration(class_id, workload))
        info["basis"] = self._median_basis(class_id, workload)[1]
        return info

    def reference(self, class_id, workload):
        """Returns (reference throughput or None, provisional flag)."""
        key = (class_id, workload)
        if key in self._controlled:
            return self._controlled[key], False
        return self._median_basis(class_id, workload)[0], True


def promise_words_in(text):
    tokens = {tok.strip(".,;:!?()\"'") for tok in (text or "").lower().split()}
    return [w for w in PROMISE_WORDS if w in tokens]
