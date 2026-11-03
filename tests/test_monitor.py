"""The free host monitor (docs/monitor-design.md): runs, transitions, busy cards, alerts."""
import io
import json
import tempfile
import unittest
from pathlib import Path

from runner import monitor as mon
from runner import preflight as pf
from tests.test_preflight import IDLE_ROW, FakeProbes, snap


class Throttled(FakeProbes):
    def load(self, seconds):
        self.loaded = seconds
        return pf.summarise_load([snap(clock=1200, temp=91, reasons="0x0000000000000040"),
                                  snap(clock=1200, temp=91, reasons="0x0000000000000040")]), {0: 60.0}


class Busy(FakeProbes):
    def gpus(self):
        return pf.parse_gpu_csv(IDLE_ROW.replace("450.00, 0, 310,", "450.00, 80, 20000,"))

    def load(self, seconds):
        raise AssertionError("a busy card must never be loaded")


class Hook:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, req, timeout):
        if self.fail:
            raise OSError("connection refused")
        self.calls.append(json.loads(req.data))

        class R:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R()


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = str(Path(self.tmp.name) / "state.json")
        self.hook = Hook()

    def tick(self, probes, t=1_800_000_000):
        return mon.tick(probes, self.state, "https://hooks.example/x", "box-1", 1, out=io.StringIO(),
                        now=lambda: t, opener=self.hook)

    def test_a_healthy_first_run_is_quiet_and_remembered(self):
        result, events, sent = self.tick(FakeProbes())
        self.assertEqual((result["state"], result["verdict"], events, sent), ("checked", pf.PASS, [], None))
        state = json.loads(Path(self.state).read_text())
        self.assertEqual(state["last"]["gpu0.clocks_under_load"], pf.PASS)
        self.assertEqual(state["runs"], 1)

    def test_degradation_alerts_once_and_recovery_alerts_again(self):
        self.tick(FakeProbes())
        _r, events, sent = self.tick(Throttled())
        changed = {e["check"]: e["to"] for e in events}
        self.assertEqual(changed["gpu0.slowdown_reasons"], pf.FAIL)
        self.assertIn("gpu0.clocks_under_load", changed)
        self.assertTrue(sent["sent"])
        text = self.hook.calls[0]["content"]
        self.assertIn("box-1", text)
        self.assertIn("gpu0.slowdown_reasons: pass -> FAIL", text)
        self.assertEqual(self.hook.calls[0]["text"], text)  # Slack reads "text", Discord "content"
        _r, events, _s = self.tick(Throttled())
        self.assertEqual(events, [])  # no repeat while it stays bad
        self.assertEqual(len(self.hook.calls), 1)
        _r, events, _s = self.tick(FakeProbes())
        self.assertTrue(all(e["to"] == pf.PASS for e in events))
        self.assertIn("recovered", self.hook.calls[1]["content"])

    def test_a_busy_card_is_skipped_never_loaded_and_keeps_the_last_status(self):
        self.tick(Throttled())
        before = json.loads(Path(self.state).read_text())["last"]
        result, events, _s = self.tick(Busy())
        self.assertEqual((result["state"], result["verdict"], events), ("busy", "skipped", []))
        self.assertTrue(all(f["status"] == "skipped" for f in result["findings"]))
        self.assertEqual(json.loads(Path(self.state).read_text())["last"], before)

    def test_a_failed_webhook_is_reported_not_raised(self):
        self.hook = Hook(fail=True)
        self.tick(FakeProbes())
        _r, events, sent = self.tick(Throttled())
        self.assertTrue(events)
        self.assertFalse(sent["sent"])
        self.assertIn("connection refused", sent["error"])

    def test_a_load_that_cannot_run_is_a_finding(self):
        result, events, _s = self.tick(FakeProbes(fail_load=True))
        self.assertEqual(result["verdict"], pf.FAIL)
        self.assertIn("compute", {e["check"] for e in events})

    def test_only_monitor_checks_are_reported(self):
        result, _e, _s = self.tick(FakeProbes())
        kinds = {f["check"].split(".", 1)[-1] for f in result["findings"]}
        self.assertTrue(kinds <= set(mon.CHECKS) | {"gpu_visible"})
        self.assertNotIn("download_rate", kinds)

    def test_cli_once_writes_state(self):
        out = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(out):
            code = mon.main(["--once", "--state", self.state, "--seconds", "1"], probes=FakeProbes())
        self.assertEqual(code, 0)
        self.assertIn("checked: pass", out.getvalue())
        self.assertEqual(json.loads(Path(self.state).read_text())["runs"], 1)


if __name__ == "__main__":
    unittest.main()
