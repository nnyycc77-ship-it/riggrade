"""Sample audit: totals for a published sample of rented GPU boxes
(docs/sample-audit-method.md). Reads the run sheet (one row per box tried)
and the summary line each box printed, and writes the per-sample counts.
Counts only; no host, address or card identifier is read or written.

    python3 -m runner.sample_audit --sheet sheet.csv --lines lines.txt [--out audit.json]

sheet.csv columns: box, network, listed_model, listed_region, usd_per_hour,
outcome (ready | deploy_failed | not_reachable | gone_before_check),
minutes_to_ready, minutes_billed.
lines.txt: the lines each box printed, "RGAUDIT <box> {json}", in any order.
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

OUTCOMES = ("ready", "deploy_failed", "not_reachable", "gone_before_check")
LINE = re.compile(r"^RGAUDIT\s+(\S+)\s+(\{.*\})\s*$")
LOAD_CHECKS = ("clocks_under_load", "slowdown_reasons", "temperature", "memory_bandwidth")


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", re.sub(r"(?i)\b(nvidia|geforce|tesla)\b", "", name or "").lower())


def model_matches(listed, reported):
    """The card reports the listed model: the listed model's letters and
    digits appear in what the driver reports (RTX 4090 in "NVIDIA GeForce
    RTX 4090"), and no larger number sits in their place (4090 vs 4090 D is
    a mismatch only when the listing says plain 4090 and the card says D)."""
    a, b = _norm(listed), _norm(reported)
    if not a or not b or a not in b:
        return False
    rest = b.replace(a, "", 1)
    return not re.match(r"^[0-9]", rest) and not (rest.startswith("d") and not a.endswith("d"))


def read_lines(text):
    out = {}
    for raw in (text or "").splitlines():
        m = LINE.match(raw.strip())
        if not m:
            continue
        try:
            doc = json.loads(m.group(2))
        except ValueError:
            continue
        if isinstance(doc, dict):
            out[m.group(1)] = doc
    return out


def total(sheet_rows, lines):
    boxes, cost, verdicts, failed_checks, matched = [], 0.0, Counter(), Counter(), Counter()
    outcomes = Counter()
    compute_measured = 0
    for row in sheet_rows:
        outcome = (row.get("outcome") or "").strip()
        if outcome not in OUTCOMES:
            raise SystemExit(f"box {row.get('box')}: outcome must be one of {', '.join(OUTCOMES)}")
        outcomes[outcome] += 1
        cost += float(row.get("usd_per_hour") or 0) * float(row.get("minutes_billed") or 0) / 60
        doc = lines.get(row.get("box", "")) if outcome == "ready" else None
        rec = {"box": row.get("box"), "network": row.get("network"), "listed_model": row.get("listed_model"),
               "listed_region": row.get("listed_region"), "outcome": outcome,
               "minutes_to_ready": row.get("minutes_to_ready") or None}
        if outcome == "ready" and doc is None:
            rec["outcome"] = "ready_no_report"
            outcomes["ready"] -= 1
            outcomes["ready_no_report"] += 1
        if doc:
            v = doc.get("verdict") if doc.get("verdict") in ("pass", "warn", "fail") else "unknown"
            verdicts[v] += 1
            match = model_matches(row.get("listed_model"), doc.get("gpu_name"))
            matched["match" if match else "mismatch"] += 1
            statuses = doc.get("statuses") or {}
            for check, status in statuses.items():
                if status == "fail":
                    failed_checks[check.split(".", 1)[-1] if check.startswith("gpu") else check] += 1
            measured = any(check.split(".", 1)[-1] in LOAD_CHECKS and s in ("pass", "warn", "fail")
                           for check, s in statuses.items())
            compute_measured += measured
            rec.update({"reported_model": doc.get("gpu_name"), "gpus": doc.get("gpus"), "verdict": v,
                        "model_matches_listing": match, "compute_measured": measured})
        boxes.append(rec)
    tried = len(boxes)
    checked = sum(verdicts.values())
    return {"method": "docs/sample-audit-method.md", "boxes_tried": tried,
            "outcomes": dict(outcomes), "boxes_checked": checked,
            "verdicts": dict(verdicts), "model_matches_listing": dict(matched),
            "compute_measured": compute_measured,
            "failed_checks": dict(failed_checks.most_common()),
            "cost_usd": round(cost, 2), "boxes": boxes,
            "note": ("Counts of a small sample, measured once at rental time; not a grade of any host or "
                     "network, and not an estimate of the whole network.")}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sample_audit", description=__doc__.split("\n\n")[0])
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--lines", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    with open(a.sheet, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = total(rows, read_lines(Path(a.lines).read_text(encoding="utf-8")))
    text = json.dumps(out, indent=1)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
