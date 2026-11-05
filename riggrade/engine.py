"""Turns an accepted result bundle into labels and an evidence pack.

Bundle shape (spec section 11), as produced by the container:

    {"session_id", "nonce", "bench_version", "image_digest", "params",
     "identity": {"device_name", "memory_gb", "card_count", "driver", "cuda",
                  "rig_id"},
     "gate": {workload: {"results": {check: bool}, "free_memory_gb": float}},
     "micro": {"memory_gb", "mem_bw_gbs", "gemm_tflops"},
     "metrics": {workload: {"throughput_passes": [...], "decay": float,
                            "p99_p50": float | None,
                            "isolation_drop": float | None}},
     "slice": {...} | None, "attestation": {...} | None,
     "outputs": {name: {"content": str, "sha256": str}}}
"""

import statistics

from . import grading
from .classes import identify
from .label import build_label, evidence_tag


REFERENCE_BUILDING_NOTICE = "reference-building session, speed not graded"


def preview(bundle, workloads, references=None):
    """Free preview: gate result per class and the number of pillars that
    will be measured. Never a grade (spec 6 step 5). Where a class has no
    reference yet, the preview says so before payment (spec 13)."""
    identity = bundle.get("identity") or {}
    class_id = identify(identity.get("device_name"), identity.get("memory_gb"))
    out = {"peer_class": class_id, "classes": {}}
    for workload in workloads:
        g = (bundle.get("gate") or {}).get(workload) or {}
        passed, failed = grading.gate(workload, g.get("results") or {},
                                      g.get("free_memory_gb"),
                                      identity.get("card_count") or 1)
        entry = {"gate_passed": passed and class_id is not None,
                 "gate_failed": failed,
                 "pillars_measured": 2 if passed else 0}
        if class_id is None:
            # Nothing is gradable without a class, so a gate list here would read
            # as a capability failure. Show only the reason.
            entry = {"gate_passed": False, "reason": "no v0 peer class for this device"}
        if class_id is not None and references is not None:
            entry["reference"] = references.basis(class_id, workload)
            if references.reference(class_id, workload)[0] is None:
                entry["notice"] = REFERENCE_BUILDING_NOTICE
        out["classes"][workload] = entry
    return out


def grade_session(session, references, envelopes=None, attested=False,
                  held=None, recheck=None, heartbeat=None, now=None,
                  host_paid=False, record_reference=True, operator=None):
    """Grade every workload class in the session.

    references: grading.ReferenceBook. envelopes: {class_id: {measure: ref}}.
    attested: True only when the endpoint's attestation verifier accepted
    the evidence. operator: the verified wallet behind this session's rig
    (None when unclaimed); it is recorded with the rig, and a payer who
    stands behind more than MAX_OPERATOR_SHARE of a class reference gets
    no speed grade from it (spec 13). held: {workload: grade, or {"grade": g, "on_watch": bool}} the rig
    already holds. recheck:
    {workload: (speed_ratio, consistency_grade)} from our own re-check in
    the window. heartbeat: list of accepted heartbeat timestamps, for a full
    grade. Returns (labels, pack).
    """
    bundle = session["bundle"]
    identity = bundle.get("identity") or {}
    rig_id = identity.get("rig_id") or "rg_unknown"
    class_id = identify(identity.get("device_name"), identity.get("memory_gb"))
    window = {"from": session["started_at"], "to": now}
    quick = session.get("mode") == "quick"
    if quick:
        # A quick check never sets or moves a class reference: references come
        # from full runs only.
        record_reference = False
    labels, pack_classes = [], {}

    if class_id is None:
        for workload in session["workloads"]:
            labels.append(_unrated(session, rig_id, None, workload, window,
                                   attested, host_paid, "unknown_class"))
        return labels, _pack(session, None, pack_classes, attested, host_paid)

    mismatch = grading.identity_mismatch(class_id, bundle.get("micro") or {},
                                         (envelopes or {}).get(class_id))
    for workload in session["workloads"]:
        g = (bundle.get("gate") or {}).get(workload) or {}
        passed, failed = grading.gate(workload, g.get("results") or {},
                                      g.get("free_memory_gb"),
                                      identity.get("card_count") or 1)
        if mismatch:
            label = _unrated(session, rig_id, class_id, workload, window,
                             attested, host_paid, "identity_mismatch")
            label["status"] = "withdrawn"
            label["grade"] = grading.UNRATED
            labels.append(label)
            pack_classes[workload] = {"withdrawn": "identity_mismatch",
                                      "off_measures": mismatch}
            continue
        if not passed:
            labels.append(_unrated(session, rig_id, class_id, workload, window,
                                   attested, host_paid, "gate_failed:" + ",".join(failed)))
            pack_classes[workload] = {"gate_failed": failed}
            continue

        m = (bundle.get("metrics") or {}).get(workload) or {}
        passes = m.get("throughput_passes") or []
        measured = statistics.median(passes) if passes else None
        reference, provisional = references.reference(class_id, workload)
        ratio = (measured / reference) if (measured and reference) else None
        share = references.operator_share(class_id, workload, operator)
        speed_withheld = None
        if ratio is not None and share > grading.MAX_OPERATOR_SHARE:
            speed_withheld = ("payer stands behind %.0f%% of this reference (limit %.0f%%); "
                              "speed not graded against it"
                              % (share * 100, grading.MAX_OPERATOR_SHARE * 100))
            ratio = None
        held_w = (held or {}).get(workload)
        if isinstance(held_w, dict):
            held_grade, was_on_watch = held_w.get("grade"), bool(held_w.get("on_watch"))
        else:
            held_grade, was_on_watch = held_w, False
        speed, below_keep = grading.speed_grade(ratio, held_grade, was_on_watch)
        if quick:
            consistency, cmeasures = grading.quick_consistency_grade(passes, m.get("p99_p50"))
        else:
            consistency, cmeasures = grading.consistency_grade(
                passes, m.get("decay"), m.get("p99_p50"), m.get("isolation_drop"))
        pillars = {"speed": speed, "consistency": consistency}
        form = "quick" if quick else "point"
        hours = None
        if heartbeat is not None and not quick:
            reliability, hours = grading.reliability_grade(heartbeat, now)
            pillars["reliability"] = reliability
            form = "full"
        grade, capped_by, form = grading.overall(pillars, form)
        rc = (recheck or {}).get(workload)
        matched = bool(rc) and grading.recheck_matches(ratio, consistency, rc[0], rc[1])
        if grade != grading.INSUFFICIENT:
            grade, capped_by = grading.top_grade_rule(grade, capped_by, attested, matched)
        if below_keep:
            status = "demoted" if was_on_watch else "on_watch"
        else:
            status = "affirmed" if held_grade else "assigned"
        tag = evidence_tag(session["declared_runner"], attested, host_paid, provisional)
        labels.append(build_label(rig_id, class_id, workload, grade, form,
                                  capped_by, pillars, status, tag, window,
                                  bundle.get("slice")))
        pack_classes[workload] = {"measured_throughput": measured,
                                  "reference": reference,
                                  "reference_basis": references.basis(class_id, workload),
                                  "speed_ratio": ratio,
                                  "speed_withheld": speed_withheld,
                                  "operator": operator,
                                  "consistency_measures": cmeasures,
                                  "downtime_hours": hours,
                                  "recheck_matched": matched}
        if record_reference and measured:
            references.add(class_id, workload, rig_id, measured, attested,
                           operator=operator)
    return labels, _pack(session, class_id, pack_classes, attested, host_paid)


def _unrated(session, rig_id, class_id, workload, window, attested, host_paid,
             reason):
    tag = evidence_tag(session["declared_runner"], attested, host_paid)
    label = build_label(rig_id, class_id, workload, grading.UNRATED, "point",
                        None, {}, "assigned", tag, window,
                        (session.get("bundle") or {}).get("slice"))
    label["reason"] = reason
    return label


def _pack(session, class_id, classes, attested, host_paid):
    bundle = session["bundle"]
    return {"session_id": session["session_id"],
            "mode": session.get("mode", "full"),
            "bench_version": bundle.get("bench_version"),
            "image_digest": bundle.get("image_digest"),
            "params": session["params"],
            "peer_class": class_id,
            "declared_runner": session["declared_runner"],
            "attested": attested, "host_paid": host_paid,
            "identity": bundle.get("identity"),
            "micro": bundle.get("micro"),
            "outputs_sha256": {k: v.get("sha256") for k, v in
                               sorted((bundle.get("outputs") or {}).items())},
            "classes": classes}
