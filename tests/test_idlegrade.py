"""riggrade-monitor --grade-when-idle (owner decision, 4 Nov 2026)."""
import io
import json
import re
import unittest
from pathlib import Path

from runner import idlegrade, monitor
from runner import preflight as pf

ROOT = Path(__file__).resolve().parents[1]
H = 3600


class FakeProc:
    def __init__(self, polls_before_exit, code=0, output=""):
        self.left, self.returncode, self.stdout = polls_before_exit, None, io.StringIO(output)
        self._code = code

    def poll(self):
        if self.left <= 0:
            self.returncode = self._code
            return self.returncode
        self.left -= 1
        return None

    def wait(self):
        self.returncode = -9
        return self.returncode


class Opener:
    def __init__(self, doc):
        self.doc, self.requests = doc, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        body = json.dumps(self.doc).encode()

        class R(io.BytesIO):
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False
        return R(body)


def sh_for(gpu_pids="", inspect_id="abc123"):
    calls = []

    def sh(cmd, timeout=30):
        calls.append(cmd)
        if cmd[0] == "nvidia-smi":
            return 0, gpu_pids
        if cmd[:2] == ["docker", "inspect"]:
            return 0, inspect_id + "\n"
        return 0, ""
    return sh, calls


class DueTests(unittest.TestCase):
    def test_idle_clock_and_interval(self):
        s = {}
        self.assertFalse(idlegrade.due(s, False, 1000 * H))            # idle clock starts
        self.assertFalse(idlegrade.due(s, False, 1005 * H))            # 5 h < 6 h
        self.assertTrue(idlegrade.due(s, False, 1006 * H))
        self.assertFalse(idlegrade.due(s, True, 1007 * H))             # busy resets the clock
        self.assertIsNone(s["idle_since"])
        s = {"idle_since": 0, "last_grade": {"at": 1000 * H}}
        self.assertFalse(idlegrade.due(s, False, 1000 * H + 10 * 24 * H))   # graded 10 days ago
        self.assertTrue(idlegrade.due(s, False, 1000 * H + 31 * 24 * H))


class RunTests(unittest.TestCase):
    REPORT = 'log line\n{"session_id": "rg_s_1", "accepted": true, "preview": {}}\n'

    def test_graded_run_sends_the_token(self):
        sh, _calls = sh_for(gpu_pids="4242")
        opener = Opener({"labels": [{"grade": 2}], "badges": ["Grade 2"],
                         "evidence_pack": {"operator_claim": "standing"}})
        cmds = []

        def popen(cmd, **kw):
            cmds.append(cmd)
            return FakeProc(2, 0, self.REPORT)
        out = idlegrade.run("https://e.example/riggrade/v1", "rgc_tok", now=lambda: 1, sh=sh, popen=popen,
                            sleep=lambda s: None, read_cgroup=lambda pid: "docker/abc123", opener=opener)
        self.assertEqual((out["outcome"], out["session_id"], out["operator_claim"]), ("graded", "rg_s_1", "standing"))
        self.assertIn(idlegrade.BENCH_IMAGE, cmds[0])
        self.assertEqual(cmds[0][-6:], ["--endpoint", "https://e.example/riggrade/v1", "--workloads",
                                        "inference", "--runner", "host"])
        (req,) = opener.requests
        self.assertEqual(req.full_url, "https://e.example/riggrade/v1/session/rg_s_1/grade")
        self.assertEqual(req.get_header("Riggrade-claim-token"), "rgc_tok")

    def test_a_renter_process_stops_the_run_at_once(self):
        sh, calls = sh_for(gpu_pids="4242\n5151")
        out = idlegrade.run("https://e", "rgc_tok", now=lambda: 1, sh=sh,
                            popen=lambda cmd, **kw: FakeProc(10, 0, self.REPORT), sleep=lambda s: None,
                            read_cgroup=lambda pid: "docker/abc123" if pid == 4242 else "docker/renter",
                            opener=Opener({}))
        self.assertEqual(out["outcome"], "stopped, card needed")
        self.assertIn(["docker", "kill", "riggrade-grade-1"], calls)

    def test_failures_are_reported_not_raised(self):
        sh, _ = sh_for()
        out = idlegrade.run("https://e", "t", now=lambda: 1, sh=sh, popen=lambda c, **k: FakeProc(0, 1),
                            sleep=lambda s: None, read_cgroup=lambda p: "", opener=Opener({}))
        self.assertEqual(out["outcome"], "failed")
        out = idlegrade.run("https://e", "t", now=lambda: 1, sh=sh,
                            popen=lambda c, **k: FakeProc(0, 0, self.REPORT), sleep=lambda s: None,
                            read_cgroup=lambda p: "", opener=Opener({"error": "no claim attached"}))
        self.assertEqual((out["outcome"], out["detail"]), ("not graded", "no claim attached"))

    def test_image_is_the_pinned_one(self):
        env = (ROOT / "container" / "bench-image.env").read_text()
        self.assertEqual(re.search(r"^BENCH_IMAGE=(\S+)", env, re.MULTILINE).group(1), idlegrade.BENCH_IMAGE)


class MonitorTests(unittest.TestCase):
    def test_only_a_passing_idle_rig_is_graded(self):
        grade = {"token": "t", "endpoint": "e", "idle_hours": 0, "every_days": 30}
        runs = []

        def run(endpoint, token):
            runs.append(token)
            return {"at": 1, "outcome": "graded", "badges": []}
        stored = {}
        self.assertIsNone(monitor.maybe_grade(stored, {"state": "busy", "verdict": "skipped"}, grade,
                                              now=lambda: 1000 * H, run=run))
        self.assertIsNone(monitor.maybe_grade(stored, {"state": "idle", "verdict": pf.WARN}, grade,
                                              now=lambda: 1000 * H, run=run))
        rec = monitor.maybe_grade(stored, {"state": "idle", "verdict": pf.PASS}, grade,
                                  now=lambda: 1000 * H, run=run)
        self.assertEqual((rec["outcome"], runs), ("graded", ["t"]))
        self.assertEqual(stored["last_grade"]["outcome"], "graded")

    def test_a_missing_docker_is_reported_not_raised(self):
        def run(endpoint, token):
            raise FileNotFoundError("docker")
        stored = {}
        rec = monitor.maybe_grade(stored, {"state": "idle", "verdict": pf.PASS},
                                  {"token": "t", "endpoint": "e", "idle_hours": 0, "every_days": 30},
                                  now=lambda: 1000 * H, run=run)
        self.assertEqual(rec["outcome"], "failed")

    def test_grade_mode_needs_a_token(self):
        with self.assertRaises(SystemExit):
            monitor.main(["--once", "--grade-when-idle", "--claim-token", ""], probes=object())


if __name__ == "__main__":
    unittest.main()
