"""Grade this rig free while it is idle (riggrade-monitor --grade-when-idle).

Owner decision, 4 Nov 2026: hosts' idle GPU time is the free way to build
the class references. When the monitor has seen every card idle for
--idle-hours and the rig has not been graded in --grade-every-days, it runs
the pinned benchmark container once (about 1.5 hours, inference) and asks
for the free reference grade with the host's standing-claim token (spec 13).

The card stays the renter's: every 30 seconds during the run the monitor
lists the processes on the GPUs, and if any of them is not inside the grade
container it stops the container at once and records "stopped, card needed".
Nothing is sent to RigGrade except the session the container itself runs and
the grade request; the token is read from the host's own config.
"""

import json
import subprocess
import time
import urllib.request

# The published benchmark image the server accepts (container/bench-image.env;
# a test holds the two together).
BENCH_IMAGE = ("ghcr.io/nnyycc77-ship-it/riggrade-bench@sha256:"
               "6c2087bee4ee2d31b139fcfe96679d2d35e806d3e5d6def5fd32a71b3b3a767d")
POLL_SECONDS = 30
IDLE_HOURS = 6.0
EVERY_DAYS = 30.0


def due(state, busy, now, idle_hours=IDLE_HOURS, every_days=EVERY_DAYS):
    """Update the idle clock in state and say whether a grade run is due."""
    if busy:
        state["idle_since"] = None
        return False
    state["idle_since"] = state.get("idle_since") or int(now)
    idle_for = now - state["idle_since"]
    last = (state.get("last_grade") or {}).get("at") or 0
    return idle_for >= idle_hours * 3600 and now - last >= every_days * 86400


def _sh(cmd, timeout=30):
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    return out.returncode, out.stdout


def gpu_pids(sh=_sh):
    code, out = sh(["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"])
    if code != 0:
        return set()
    return {int(x) for x in out.split() if x.strip().isdigit()}


def _read_cgroup(pid):
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def foreign_pids(container_id, sh=_sh, read_cgroup=_read_cgroup):
    """GPU processes that are not inside our grade container."""
    return {p for p in gpu_pids(sh) if container_id not in read_cgroup(p)}


def last_json(text):
    """The last JSON object the container printed (its session report)."""
    depth, start, found = 0, None, None
    for i, ch in enumerate(text or ""):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    found = json.loads(text[start:i + 1])
                except ValueError:
                    pass
    return found


def request_grade(endpoint, session_id, token, opener=urllib.request.urlopen, timeout=60):
    url = endpoint.rstrip("/") + f"/session/{session_id}/grade"
    req = urllib.request.Request(url, data=b"", method="POST",
                                 headers={"RigGrade-Claim-Token": token, "Content-Type": "application/json",
                                          "User-Agent": "riggrade-monitor"})
    try:
        with opener(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - reported to the host, never raised
        return {"error": f"{type(exc).__name__}: {exc}"}


def run(endpoint, token, now=time.time, sh=_sh, popen=subprocess.Popen, sleep=time.sleep,
        read_cgroup=_read_cgroup, opener=urllib.request.urlopen, poll=POLL_SECONDS, image=BENCH_IMAGE):
    """One grade run. Returns a dict the monitor stores and reports."""
    name = f"riggrade-grade-{int(now())}"
    digest = image.split("@", 1)[1]
    cmd = ["docker", "run", "--rm", "--gpus", "all", "--name", name,
           "-e", f"RIGGRADE_IMAGE_DIGEST={digest}", "-e", f"RIGGRADE_ENDPOINT={endpoint}",
           image, "--endpoint", endpoint, "--workloads", "inference", "--runner", "host"]
    started = int(now())
    proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    container_id = None
    while proc.poll() is None:
        sleep(poll)
        if container_id is None:
            code, out = sh(["docker", "inspect", "-f", "{{.Id}}", name])
            container_id = out.strip() if code == 0 and out.strip() else None
        if container_id and foreign_pids(container_id, sh, read_cgroup):
            sh(["docker", "kill", name])
            proc.wait()
            return {"at": started, "outcome": "stopped, card needed",
                    "detail": "another process started on the GPU; the grade run was stopped"}
    output = proc.stdout.read() if proc.stdout else ""
    if proc.returncode != 0:
        return {"at": started, "outcome": "failed", "detail": f"container exited {proc.returncode}"}
    report = last_json(output) or {}
    sid = report.get("session_id")
    if not sid or not report.get("accepted"):
        return {"at": started, "outcome": "failed", "detail": "no accepted session in the container output"}
    graded = request_grade(endpoint, sid, token, opener=opener)
    if graded.get("labels") is None:
        return {"at": started, "outcome": "not graded", "session_id": sid,
                "detail": graded.get("error") or graded.get("note") or "the service did not grade it"}
    return {"at": started, "outcome": "graded", "session_id": sid,
            "badges": graded.get("badges", []), "operator_claim":
                (graded.get("evidence_pack") or {}).get("operator_claim")}
