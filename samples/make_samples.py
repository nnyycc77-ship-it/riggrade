"""Builds the labelled sample grading of three fictional rigs (plan path
step 1). Every number is fictional and expressed in "sample units" against
a fixed sample reference of 1000, so nothing here is a claim about real
hardware. The grades come from the real engine, so the sample shows exactly
how the rules behave.

    python3 -m samples.make_samples        # rewrites the two output files
"""

import json
from pathlib import Path

from riggrade import BENCH_VERSION
from riggrade.engine import grade_session
from riggrade.grading import ReferenceBook
from riggrade.label import badge_text, lint

HERE = Path(__file__).resolve().parent
JSON_OUT = HERE / "sample-grades.json"
DOC_OUT = HERE.parent / "docs" / "sample-grading.md"
SAMPLE_MARK = "SAMPLE: fictional rig, fictional numbers, not a live grade"
NOW = 1_800_000_000
DAY = 86400

GATE_OK = {"memory_integrity": True, "gemm": True, "memory_bandwidth": True,
           "host_link": True, "card_link": True}

RIGS = [
    {"key": "A", "title": "Fictional rig A: H100 SXM class, inference, full grade",
     "device": "NVIDIA H100 80GB HBM3", "memory": 79.6, "cards": 8,
     "workload": "inference", "runner": "host", "attested": True,
     "passes": [978, 981, 975], "decay": 0.02, "p99_p50": 1.6,
     "outage_hours": 1.0, "held": None,
     "story": "Attested evidence, steady passes and a clean 30-day heartbeat: "
              "every pillar earns Grade 1, and attestation lets the overall "
              "grade stay at Grade 1."},
    {"key": "B", "title": "Fictional rig B: RTX 4090 class, inference, point grade",
     "device": "NVIDIA GeForce RTX 4090", "memory": 23.6, "cards": 1,
     "workload": "inference", "runner": "buyer", "attested": False,
     "passes": [962, 958, 966], "decay": 0.03, "p99_p50": 2.6,
     "outage_hours": None, "held": None,
     "story": "Speed earns Grade 1, but the latency spread (p99/p50 of 2.6) "
              "earns Grade 2 on consistency, and the weakest pillar caps the "
              "grade. It is a single paid session, so reliability is not "
              "measured."},
    {"key": "C", "title": "Fictional rig C: A100 SXM class, training, full grade on watch",
     "device": "NVIDIA A100-SXM4-80GB", "memory": 79.2, "cards": 4,
     "workload": "training", "runner": "host", "attested": True,
     "passes": [905, 912, 909], "decay": 0.03, "p99_p50": None,
     "outage_hours": 2.0, "held": 1,
     "story": "The rig held Grade 1. This week its speed ratio is about 0.91, "
              "below the 0.92 keep bar. Under watch-before-demotion it keeps "
              "Grade 1 and goes on watch, and the badge says so. A second "
              "consecutive week below the keep bar would land the grade on "
              "what the numbers earn, Grade 2."},
]


def heartbeat(outage_hours):
    start = NOW - 30 * DAY
    ts = list(range(start, NOW + 1, 300))
    if outage_hours:
        a = NOW - 10 * DAY
        ts = [t for t in ts if not a < t < a + outage_hours * 3600]
    return ts


def build():
    book = ReferenceBook()
    for class_id in ("h100-sxm-80", "rtx4090-24", "a100-sxm-80"):
        for workload in ("inference", "training"):
            book.set_controlled(class_id, workload, 1000.0)
    samples = []
    for rig in RIGS:
        session = {"session_id": "sample-" + rig["key"], "nonce": "sample",
                   "workloads": [rig["workload"]],
                   "declared_runner": rig["runner"],
                   "started_at": NOW - 7200, "params": {"sample": True},
                   "bundle": {"bench_version": BENCH_VERSION,
                              "image_digest": "sample",
                              "identity": {"device_name": rig["device"],
                                           "memory_gb": rig["memory"],
                                           "card_count": rig["cards"],
                                           "rig_id": "sample-rig-" + rig["key"]},
                              "gate": {rig["workload"]: {"results": GATE_OK,
                                                         "free_memory_gb": rig["memory"] - 2}},
                              "micro": {},
                              "metrics": {rig["workload"]: {
                                  "throughput_passes": rig["passes"],
                                  "decay": rig["decay"], "p99_p50": rig["p99_p50"]}},
                              "slice": None, "outputs": {}}}
        hb = heartbeat(rig["outage_hours"]) if rig["outage_hours"] is not None else None
        held = {rig["workload"]: rig["held"]} if rig["held"] else None
        labels, pack = grade_session(session, book, attested=rig["attested"],
                                     held=held, heartbeat=hb, now=NOW,
                                     record_reference=False)
        label = labels[0]
        label["sample"] = SAMPLE_MARK
        samples.append({"rig": rig["key"], "title": rig["title"], "story": rig["story"],
                        "label": label,
                        "badge": badge_text(label, "sample"),
                        "speed_ratio": round(pack["classes"][rig["workload"]]["speed_ratio"], 3)})
    return samples


def render(samples):
    lines = ["# RigGrade sample grading", "",
             "> " + SAMPLE_MARK + ". The three rigs below do not exist. Their numbers "
             "are invented, in sample units against a sample reference of 1000, and "
             "chosen only to show how the grading rules behave. The grades were "
             "produced by the real grading engine (`python3 -m samples.make_samples`).",
             ""]
    for s in samples:
        lab = s["label"]
        lines += ["## " + s["title"], "",
                  "**Badge:** " + s["badge"], "",
                  s["story"], "",
                  "| | |", "|---|---|",
                  "| Grade | %s |" % lab["grade"],
                  "| Form | %s (%s) |" % (lab["form"], lab["evidence_basis"]),
                  "| Capped by | %s |" % (lab["capped_by"] or "nothing (all pillars at Grade 1)"),
                  "| Pillars | %s |" % ", ".join("%s %s" % kv for kv in sorted(lab["pillars"].items())),
                  "| Speed ratio | %s |" % s["speed_ratio"],
                  "| Status | %s |" % lab["status"],
                  "| Evidence tag | `%s` |" % lab["evidence_tag"], ""]
    lines += ["All three labels carry the statement: \"%s\"" % samples[0]["label"]["statement"], ""]
    return "\n".join(lines)


def main():
    samples = build()
    doc = render(samples)
    found = lint([doc] + [s["badge"] for s in samples])
    if found:
        raise SystemExit("promise words in sample text: %s" % found)
    JSON_OUT.write_text(json.dumps(samples, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    DOC_OUT.write_text(doc, encoding="utf-8")
    for s in samples:
        print(s["badge"])


if __name__ == "__main__":
    main()
