"""Label and badge text (spec section 12)."""

from . import BENCH_VERSION
from .classes import label_name
from .grading import NOT_MEASURED, INSUFFICIENT, promise_words_in

STATEMENT = ("Measured, not promised. A label of measured quality under the "
             "named test and window; it makes no promise about future delivery.")

WORKLOAD_NAMES = {"inference": "Inference", "training": "Training"}
PILLAR_NAMES = {"speed": "Speed", "consistency": "Consistency",
                "reliability": "Reliability", "evidence": "Evidence"}


def evidence_tag(declared_runner, attested, host_paid=False, provisional=False):
    parts = ["declared:" + declared_runner,
             "attestation:" + ("attested" if attested else "unattested")]
    if host_paid:
        parts.append("host-paid")
    if provisional:
        parts.append("provisional")
    return " ".join(parts)


def build_label(rig_id, class_id, workload, grade, form, capped_by, pillars,
                status, tag, window, slice_info=None):
    pillars = dict(pillars)
    if form in ("point", "quick"):
        pillars["reliability"] = NOT_MEASURED
    label = {
        "product": "RigGrade",
        "bench_version": BENCH_VERSION,
        "rig_id": rig_id,
        "peer_class": class_id,
        "workload_class": workload,
        "grade": grade,
        "form": form,
        "evidence_basis": {"point": "single session",
                           "quick": "single session, quick check: no 60-minute heat test"}.get(
                               form, "trailing windows"),
        "capped_by": capped_by,
        "pillars": pillars,
        "status": status,
        "evidence_tag": tag,
        "slice": slice_info,
        "window": window,
        "statement": STATEMENT,
    }
    return label


def badge_text(label, date_text):
    tag = label["evidence_tag"].split()
    runner = tag[0].split(":", 1)[1]
    attested = tag[1].split(":", 1)[1]
    grade = label["grade"]
    head = ("Insufficient data" if grade == INSUFFICIENT else "Grade %s" % grade)
    parts = [head,
             "%s class" % label_name(label["peer_class"]),
             WORKLOAD_NAMES[label["workload_class"]],
             {"point": "point grade, single session",
              "quick": "quick check, no heat test"}.get(label["form"], "full grade")]
    if label.get("capped_by") and grade != INSUFFICIENT:
        parts.append("capped by %s" % PILLAR_NAMES[label["capped_by"]])
    if label.get("shared_during_run"):
        parts.append("card shared during run")
    if label["status"] in ("on_watch", "demoted"):
        parts.append(label["status"].replace("_", " "))
    parts.append("declared %s run, %s" % (runner, attested))
    if "provisional" in tag:
        parts.append("provisional bars")
    if date_text:
        parts.append(date_text)
    return " · ".join(parts)


def lint(texts):
    """Promise words found in any of the given texts (spec 12)."""
    found = []
    for text in texts:
        found.extend(promise_words_in(text))
    return sorted(set(found))
