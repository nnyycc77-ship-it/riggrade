"""Sample audit: totals for a published sample of rented GPU boxes
(docs/sample-audit-method.md). Reads the run sheet (one row per box tried)
and the summary line each box printed, and writes the per-sample counts.
Counts only; no host, address or card identifier is read or written.

    python3 -m runner.sample_audit --sheet sheet.csv --lines lines.txt [--out audit.json]

sheet.csv columns: box, network, listed_model, listed_region, usd_per_hour,
outcome (ready | ready_no_report | deploy_failed | not_reachable |
gone_before_check | not_attempted), minutes_to_ready, minutes_billed; optional listed_gpus.
A pick the spend cap left untried is "not_attempted": counted apart and
never as tried.
lines.txt: the lines each box printed, "RGAUDIT <box> {json}", in any order.
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

OUTCOMES = ("ready", "ready_no_report", "deploy_failed", "not_reachable", "gone_before_check", "not_attempted")
LINE = re.compile(r"^RGAUDIT\s+(\S+)\s+(\{.*\})\s*$")
LOAD_CHECKS = ("clocks_under_load", "slowdown_reasons", "temperature", "memory_bandwidth")


# Name parts (Coordinator Message 332 conditions a and b): a match compares
# the model's core parts, never a substring remainder. Vendor and brand
# words are dropped; form descriptors may differ; memory, where the listing
# names it, is checked against the measured memory; a performance variant
# (Ti, Super, D, ...) on either side that the other lacks refuses a match.
VENDOR = {"NVIDIA", "GEFORCE", "TESLA", "QUADRO", "RTX", "GTX", "GENERATION"}
FORM = re.compile(r"^(SXM\d*|PCIE\d*|PCI|E|HBM\d*E?|NVL|NVLINK|OAM|MXM|SFF|BLACKWELL|HOPPER|AMPERE)$")
MEMORY = re.compile(r"^(\d+)(GB|G|GIB)$")
VARIANT = {"TI", "SUPER", "D", "MAX", "Q", "LAPTOP", "MOBILE", "LE", "XT"}


def name_parts(name):
    """(core parts, variants, memory in GB or None) of a model name."""
    core, variants, memory = [], set(), None
    for tok in re.sub(r"[_\-/(),]+", " ", (name or "").upper()).split():
        if tok in VENDOR or re.match(r"^X\d+$", tok):   # "x8" is the GPU count, not the model
            continue
        m = MEMORY.match(tok)
        if m:
            memory = int(m.group(1))
            continue
        if FORM.match(tok):
            continue
        for part in re.findall(r"[A-Z]+|\d+", tok):
            if part in VENDOR or FORM.match(part):
                continue
            (variants.add(part) if part in VARIANT else core.append(part))
    return core, variants, memory


def model_matches(listed, reported, memory_total_mb=None):
    """The card is the listed model: the same core parts (A100 = A100-SXM4-80GB,
    A6000 = RTX A6000, H100 = H100 80GB HBM3), no variant on one side only
    (4090 is not 4090 Ti, 4090 D or 4080 Super), and, where the listing names
    a memory size, the reported or measured memory agrees within 10%."""
    lc, lv, lm = name_parts(listed)
    rc, rv, rm = name_parts(reported)
    if not lc or lc != rc or lv != rv:
        return False
    if lm is not None:
        seen = rm if rm is not None else (round(memory_total_mb / 1024) if memory_total_mb else None)
        if seen is None or abs(seen - lm) > 0.1 * lm:
            return False
    return True


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
    outcomes, counts = Counter(), Counter()
    compute_measured = 0
    for row in sheet_rows:
        outcome = (row.get("outcome") or "").strip()
        if outcome not in OUTCOMES:
            raise SystemExit(f"box {row.get('box')}: outcome must be one of {', '.join(OUTCOMES)}")
        outcomes[outcome] += 1
        if outcome == "not_attempted":
            boxes.append({"box": row.get("box"), "network": row.get("network"),
                          "listed_model": row.get("listed_model"), "outcome": outcome})
            continue
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
            match = model_matches(row.get("listed_model"), doc.get("gpu_name"), doc.get("memory_total_mb"))
            listed_gpus = (row.get("listed_gpus") or "").strip()
            if listed_gpus.isdigit():
                rec["gpu_count_matches_listing"] = int(listed_gpus) == int(doc.get("gpus") or 0)
                counts["match" if rec["gpu_count_matches_listing"] else "mismatch"] += 1
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
    tried = sum(1 for b in boxes if b["outcome"] != "not_attempted")
    checked = sum(verdicts.values())
    return {"method": "docs/sample-audit-method.md", "boxes_picked": len(boxes), "boxes_tried": tried,
            "outcomes": dict(outcomes), "boxes_checked": checked,
            "verdicts": dict(verdicts), "model_matches_listing": dict(matched),
            "gpu_count_matches_listing": dict(counts),
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
