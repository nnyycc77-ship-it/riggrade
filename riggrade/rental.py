"""The rental check loop, v0 (docs/rental-check-design.md, accepted with
conditions in board Message 308).

    POST /riggrade/v1/rental/plan   before renting: what to require for the
                                    workload, which preflight check confirms
                                    each requirement, the keep rules
    POST /riggrade/v1/rental/check  in the first minutes after renting: keep,
                                    keep with warnings, or cancel, from the
                                    preflight report, with each card's history

Free in v0: no payment, no wallet, nothing stored. It names no marketplace, no
host and no price, and it ranks no offers. The site context is not offered
here: the site layer stays internal until the owner launches it.
"""

from . import fixit, record

VERSION = "rental-check-v0"
WORKLOADS = ("inference", "training")
HISTORY_DAYS = 30

# Checked on every box: the box behaves like the card it reports.
ALWAYS = {
    "foreign_memory": "no memory already taken by someone else",
    "foreign_compute": "no compute already in use by someone else",
    "clocks_under_load": "clocks held up under load, not throttled",
    "slowdown_reasons": "no thermal or hardware slowdown",
    "temperature": "temperature in range under load",
    "driver_vs_cuda": "a driver new enough for your CUDA build",
}
# What a workload adds by default, and what an agent may ask for by name.
WORKLOAD_NEEDS = {
    "inference": ("download",),
    "training": ("pcie", "host", "memory_bandwidth"),
}
NEEDS = {
    "ecc": (("ecc_errors",), "ECC on and no uncorrected errors", "filter listings for ECC enabled"),
    "pcie": (("pcie_link",), "the full PCIe width and generation the card supports",
             "prefer listings that state PCIe gen and width"),
    "host": (("host.memory", "host.swap", "host.cpu_load"), "a host with free RAM, no swapping and idle CPUs",
             "prefer listings with more host RAM per GPU"),
    "disk": (("disk_write",), "fast local disk writes", "filter listings by disk speed or local NVMe"),
    "download": (("download_rate",), "a fast enough download to pull models",
                 "filter listings by measured internet download speed"),
    "memory_bandwidth": (("memory_bandwidth",), "GPU memory bandwidth in range for the card", ""),
}
KEEP_RULES = [
    "cancel: a check the plan named as a requirement failed",
    "cancel: the card is not the peer class the plan asked for",
    ("cancel: a card whose record shows the same check failing within the last 30 days, now failing again "
     "(the record entry's evidence tag and attestation are printed beside it)"),
    "keep with warnings: any warn, or a fail the plan did not name as a requirement",
    "keep: everything else",
]
ADVICE = ("Advice measured at that moment on this box, not a judgement of the host. Cancelling and any refund sit "
          "under the marketplace's own terms; check its cancellation window yourself.")


class BadRequest(ValueError):
    pass


def _needs(workload, asked):
    names = list(WORKLOAD_NEEDS.get(workload, ()))
    for n in asked or ():
        if n not in NEEDS:
            raise BadRequest(f"unknown need {n!r}; known: {', '.join(sorted(NEEDS))}")
        if n not in names:
            names.append(n)
    return names


def plan(body):
    if not isinstance(body, dict):
        raise BadRequest("send a JSON object")
    workload = body.get("workload")
    if workload not in WORKLOADS:
        raise BadRequest("workload must be one of: " + ", ".join(WORKLOADS))
    needs = body.get("needs") or []
    if not isinstance(needs, list):
        raise BadRequest("needs must be a list of names")
    names = _needs(workload, needs)
    filters = [{"need": k, "why": why, "confirmed_by": [k]} for k, why in ALWAYS.items()]
    for n in names:
        checks, why, hint = NEEDS[n]
        f = {"need": n, "why": why, "confirmed_by": list(checks)}
        if hint:
            f["listing_hint"] = hint
        filters.append(f)
    gpus = body.get("gpus") if isinstance(body.get("gpus"), dict) else {}
    return {"version": VERSION, "workload": workload, "peer_class": gpus.get("peer_class"),
            "requirements": [f["need"] for f in filters], "filters": filters,
            "preflight": {"install": "pip install riggrade", "run": "riggrade-preflight",
                          "expected_minutes": 2,
                          "then": "POST the preflight-report.json it writes to /riggrade/v1/rental/check with this plan"},
            "keep_rules": KEEP_RULES,
            "site": "not offered: site context is not public yet",
            "note": "This plan names no marketplace, host or price and ranks no offers. Free in v0."}


def _status_by_kind(report):
    """{check kind: [(full check name, status, detail)]} from the report's findings."""
    out = {}
    for f in report.get("findings") or []:
        if not isinstance(f, dict) or not isinstance(f.get("check"), str):
            continue
        kind = f["check"].split(".", 1)[-1] if f["check"].startswith("gpu") else f["check"]
        out.setdefault(kind, []).append((f["check"], f.get("status"), str(f.get("detail", ""))[:300]))
    return out


def _required_kinds(the_plan):
    kinds = set(ALWAYS)
    for n in the_plan.get("requirements") or []:
        if n in NEEDS:
            kinds.update(NEEDS[n][0])
    return kinds


def _peer_mismatch(report, the_plan):
    want = the_plan.get("peer_class")
    if not want:
        return None
    from .classes import PEER_CLASSES
    chip = PEER_CLASSES.get(want, (None,))[0]
    if not chip:
        return None
    names = [str(g.get("name", "")) for g in report.get("gpus") or [] if isinstance(g, dict)]
    bad = [n for n in names if chip.upper() not in n.upper()]
    return bad or None


def card_history(rows, card_ids, now):
    """Per card: earlier records and their failed checks within the window,
    each with its evidence tag and attestation."""
    since = now - HISTORY_DAYS * 86400
    out = {}
    for cid in card_ids:
        doc = record.history(rows, cid) if rows else {"records": 0, "entries": []}
        recent = [e for e in doc.get("entries") or [] if int(e.get("at") or 0) >= since]
        out[cid] = {"records": doc.get("records", 0),
                    "first_sighting": not doc.get("records"),
                    "recent_failures": [{"at": e.get("at"), "evidence_tag": e.get("evidence_tag"),
                                         "attested": bool(e.get("attested")),
                                         "failed": sorted(k for k, v in (e.get("statuses") or {}).items()
                                                          if v == "fail")}
                                        for e in recent if any(v == "fail" for v in (e.get("statuses") or {}).values())]}
    return out


def check(body, rows, now):
    if not isinstance(body, dict) or not isinstance(body.get("preflight_report"), dict):
        raise BadRequest("send {preflight_report: {...the report...}, plan?: {...the plan answer...}}")
    report = body["preflight_report"]
    if not report.get("preflight"):
        raise BadRequest("preflight_report is not a riggrade preflight report")
    the_plan = body.get("plan") if isinstance(body.get("plan"), dict) else {"requirements": list(ALWAYS)}
    required = _required_kinds(the_plan)
    by_kind = _status_by_kind(report)
    card_ids = [c["card_id"] for c in report.get("cards") or []
                if isinstance(c, dict) and isinstance(c.get("card_id"), str) and record.CARD_ID.match(c["card_id"])]
    history = card_history(rows, card_ids, now)
    cancel, warn = [], []
    for kind, items in by_kind.items():
        for name, status, detail in items:
            if status == "fail" and kind in required:
                cancel.append({"rule": "required check failed", "check": name, "detail": detail})
            elif status == "skipped" and kind in required and kind in ("ecc_errors",):
                cancel.append({"rule": "required check not met (not measured on this box)", "check": name,
                               "detail": detail})
            elif status == "fail":
                warn.append({"rule": "fail not named in the plan", "check": name, "detail": detail})
            elif status == "warn":
                warn.append({"rule": "warn", "check": name, "detail": detail})
    if "ecc_errors" in required and "ecc_errors" not in by_kind:
        # Asked for, and the report has no ECC line at all (the read failed or
        # an older runner): not measured, so it cannot count as met.
        cancel.append({"rule": "required check not met (not measured on this box)", "check": "ecc_errors",
                       "detail": "the preflight report has no ECC finding"})
    mismatch = _peer_mismatch(report, the_plan)
    if mismatch:
        cancel.append({"rule": "not the peer class the plan asked for", "check": "peer_class",
                       "detail": "reported: " + ", ".join(mismatch)})
    failing_now = {kind for kind, items in by_kind.items() for _n, s, _d in items if s == "fail"}
    for cid, h in history.items():
        for e in h["recent_failures"]:
            again = sorted(k for k in e["failed"] if (k.split(".", 1)[-1] if k.startswith("gpu") else k) in failing_now)
            if again:
                cancel.append({"rule": f"same check failed on this card within {HISTORY_DAYS} days",
                               "check": ", ".join(again), "card_id": cid, "evidence_tag": e["evidence_tag"],
                               "attested": e["attested"],
                               "detail": ("record entry is attested" if e["attested"] else
                                          "record entry is unattested: the card's identity is readable, "
                                          "not provable, so this is not proof it is the same physical card")})
    verdict = "cancel" if cancel else ("keep with warnings" if warn else "keep")
    prev = fixit.preview(report)
    return {"version": VERSION, "verdict": verdict, "reasons": cancel + warn, "history": history,
            "fixit": {"fixable": prev["fixable"], "checks": prev["checks"]},
            "recorded": False, "advice": ADVICE,
            "note": "Nothing from this call is stored. Free in v0."}
