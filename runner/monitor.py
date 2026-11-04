"""riggrade-monitor: the free host agent (docs/monitor-design.md).

Runs the light part of the preflight on a schedule and tells the host, on the
host's own webhook, the moment a GPU check gets worse or recovers:

    riggrade-monitor --webhook https://discord.com/api/webhooks/...   # every 6 hours
    riggrade-monitor --once                                           # one run, no loop
    riggrade-monitor --grade-when-idle --claim-token rgc_...          # also grade free when idle

Each run: memory and compute already in use (the card is busy with a renter's
work, so the run is "skipped, busy" and nothing is loaded), then ECC counts,
and a short load that reads clocks, slowdown flags, temperature and the PCIe
link. Without --grade-when-idle it sends nothing to RigGrade: the only network
call is the alert to the URL the host gives, and only when a check changes.
With it, the monitor also runs a free reference grade when the rig has been
idle long enough (runner/idlegrade.py). State (the last status of
each check) is kept in a small JSON file on the box.
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from runner import idlegrade
from runner import preflight as pf

CHECKS = ("foreign_memory", "foreign_compute", "clocks_under_load", "slowdown_reasons",
          "temperature", "pcie_link", "ecc_errors")
BUSY_CHECKS = ("foreign_memory", "foreign_compute")
LOAD_SECONDS = 10.0
EVERY_HOURS = 6.0
RANK = {pf.PASS: 0, pf.WARN: 1, pf.FAIL: 2}
DEFAULT_STATE = Path.home() / ".riggrade-monitor.json"


def _kind(check):
    return check.split(".", 1)[-1]


def check_once(probes, seconds=LOAD_SECONDS, now=time.time):
    """One monitor run. Returns {"at", "state", "findings", "verdict"}; never
    raises for a probe failure (it is recorded as a finding)."""
    obs, errors = {}, {}

    def attempt(name, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - recorded, never raised
            errors[name] = f"{type(exc).__name__}: {exc}"
            return None

    obs["idle"] = attempt("gpus", lambda: pf.idle_snapshot(probes)) or []
    if "gpus" in errors:
        obs["gpu_error"] = errors["gpus"]
    pre = pf.evaluate(dict(obs))
    busy = [f for f in pre if _kind(f["check"]) in BUSY_CHECKS and f["status"] in (pf.WARN, pf.FAIL)]
    if busy:
        # A renter's job (or anything else) holds the card: never load it, never
        # read that as a failure. The run is recorded as skipped, busy.
        return {"at": int(now()), "state": "busy", "verdict": "skipped",
                "findings": [dict(f, status="skipped", detail="busy: " + f["detail"]) for f in busy],
                "errors": errors}
    if getattr(probes, "ecc", None):
        obs["ecc"] = attempt("ecc", probes.ecc) or {}
    if getattr(probes, "power_limits", None):
        obs["power_limits"] = attempt("power_limits", probes.power_limits) or {}
    loaded = attempt("load", lambda: probes.load(seconds))
    if loaded:
        obs["load"], obs["tflops"] = loaded
    elif "load" in errors:
        obs["compute_error"] = errors["load"]
    findings = [f for f in pf.evaluate(obs)
                if f["check"] == "gpu_visible" or (f["check"].startswith("gpu") and _kind(f["check"]) in CHECKS)]
    if "load" in errors:
        findings.append({"check": "compute", "status": pf.FAIL, "detail": "load did not run: " + errors["load"]})
    return {"at": int(now()), "state": "checked", "verdict": pf.verdict(findings)["verdict"],
            "findings": findings, "errors": errors}


def changes(previous, result):
    """Checks that got worse or recovered since the last run. A skipped check
    (busy, or not measured) is not a change: the last known status stands."""
    out = []
    for f in result["findings"]:
        new = f["status"]
        if new not in RANK:
            continue
        old = previous.get(f["check"])
        if old is None:
            if new != pf.PASS:
                out.append({"check": f["check"], "from": None, "to": new, "detail": f["detail"]})
            continue
        if RANK[new] != RANK.get(old, 0):
            out.append({"check": f["check"], "from": old, "to": new, "detail": f["detail"]})
    return out


def remember(previous, result):
    state = dict(previous)
    for f in result["findings"]:
        if f["status"] in RANK:
            state[f["check"]] = f["status"]
    return state


def message(host_label, events, when):
    stamp = dt.datetime.fromtimestamp(when, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"RigGrade monitor, {host_label}, {stamp}:"]
    for e in events:
        verb = "recovered" if e["to"] == pf.PASS else f"{e['from'] or 'new'} -> {e['to'].upper()}"
        lines.append(f"- {e['check']}: {verb}. {e['detail']}")
    lines.append("Measured on the box; not a grade. The fix-it report explains a warn or fail.")
    return "\n".join(lines)


def alert(url, text, events, opener=urllib.request.urlopen, timeout=10):
    """POST to the host's own webhook. Discord reads "content", Slack reads
    "text"; anything else gets both plus the events. Never raises."""
    body = json.dumps({"content": text[:1900], "text": text, "riggrade": {"events": events}}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "riggrade-monitor"})
    try:
        with opener(req, timeout=timeout) as resp:
            return {"sent": True, "status": getattr(resp, "status", None)}
    except Exception as exc:  # noqa: BLE001 - an alert failure is printed, never fatal
        return {"sent": False, "error": f"{type(exc).__name__}: {exc}"}


def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    Path(path).write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def tick(probes, state_path, webhook=None, label="this box", seconds=LOAD_SECONDS, out=None,
         now=time.time, opener=urllib.request.urlopen, grade=None):
    stored = load_state(state_path)
    result = check_once(probes, seconds, now)
    events = changes(stored.get("last", {}), result)
    sent = None
    if events and webhook:
        sent = alert(webhook, message(label, events, result["at"]), events, opener=opener)
    last = remember(stored.get("last", {}), result)
    if sent is not None and not sent["sent"]:
        # The host was not told: keep the old status for these checks so the
        # next run sees the same change and tries the alert again.
        for e in events:
            if e["from"] is None:
                last.pop(e["check"], None)
            else:
                last[e["check"]] = e["from"]
        stored["unsent_alerts"] = int(stored.get("unsent_alerts", 0)) + 1
    stored["last"] = last
    stored["runs"] = int(stored.get("runs", 0)) + 1
    stored["last_run"] = {"at": result["at"], "state": result["state"], "verdict": result["verdict"]}
    save_state(state_path, stored)
    if grade is not None:
        graded = maybe_grade(stored, result, grade, now)
        if graded is not None:
            save_state(state_path, stored)
            if webhook:
                alert(webhook, f"RigGrade monitor, {label}: idle grade run {graded['outcome']}"
                      + (": " + "; ".join(graded.get("badges") or []) if graded.get("badges") else
                         (": " + graded["detail"] if graded.get("detail") else "")), [], opener=opener)
            print("grade run: " + graded["outcome"], file=out or sys.stdout)
    summary = f"{result['state']}: {result['verdict']}, {len(events)} change(s)"
    if sent is not None:
        summary += ", alert " + ("sent" if sent["sent"] else "failed: " + sent.get("error", ""))
    print(summary, file=out or sys.stdout)
    return result, events, sent


def maybe_grade(stored, result, grade, now=time.time, run=idlegrade.run):
    """Run one idle grade when due (runner/idlegrade.py). Returns the run's
    record, or None when nothing was due. A failed or failing check never
    starts a grade: only a rig whose checks all passed is graded."""
    busy = result["state"] == "busy" or result["verdict"] != pf.PASS
    if not idlegrade.due(stored, busy, now(), grade["idle_hours"], grade["every_days"]):
        return None
    try:
        record = run(grade["endpoint"], grade["token"])
    except Exception as exc:  # noqa: BLE001 - e.g. Docker missing: reported, the monitor keeps running
        record = {"at": int(now()), "outcome": "failed", "detail": f"{type(exc).__name__}: {exc}"}
    stored["last_grade"] = record
    if record["outcome"] == "stopped, card needed":
        stored["idle_since"] = None
    return record


def main(argv=None, probes=None, sleep=time.sleep):
    ap = argparse.ArgumentParser(prog="riggrade-monitor", description=__doc__.split("\n\n")[0])
    ap.add_argument("--webhook", help="your own webhook URL (Discord, Slack or any) for alerts")
    ap.add_argument("--every", type=float, default=EVERY_HOURS, help="hours between runs (default 6)")
    ap.add_argument("--once", action="store_true", help="run once and exit")
    ap.add_argument("--state", default=str(DEFAULT_STATE), help="state file (default ~/.riggrade-monitor.json)")
    ap.add_argument("--label", default="this box", help="how alerts name this box")
    ap.add_argument("--seconds", type=float, default=LOAD_SECONDS, help="load length in seconds (default 10)")
    ap.add_argument("--grade-when-idle", action="store_true",
                    help="free reference grade of this rig when every card has been idle --idle-hours "
                         "(needs Docker with GPU access and --claim-token; stops at once if a renter's "
                         "process appears on the GPU)")
    ap.add_argument("--claim-token", default=os.environ.get("RIGGRADE_CLAIM_TOKEN"),
                    help="your standing-claim token for this rig (from the checkout page; or RIGGRADE_CLAIM_TOKEN)")
    ap.add_argument("--idle-hours", type=float, default=idlegrade.IDLE_HOURS,
                    help="hours every card must stay idle before a grade run (default 6)")
    ap.add_argument("--grade-every-days", type=float, default=idlegrade.EVERY_DAYS,
                    help="days between grade runs (default 30)")
    ap.add_argument("--endpoint", default=os.environ.get("RIGGRADE_ENDPOINT") or pf.DEFAULT_ENDPOINT)
    a = ap.parse_args(argv)
    grade = None
    if a.grade_when_idle:
        if not a.claim_token:
            ap.error("--grade-when-idle needs --claim-token (get one free on the checkout page)")
        grade = {"token": a.claim_token, "endpoint": a.endpoint, "idle_hours": a.idle_hours,
                 "every_days": a.grade_every_days}
    probes = probes or pf.Probes()
    while True:
        tick(probes, a.state, a.webhook, a.label, a.seconds, grade=grade)
        if a.once:
            return 0
        sleep(max(0.25, a.every) * 3600)


if __name__ == "__main__":
    sys.exit(main())
