"""Regression tests for what the first real GPU run (free Colab T4,
31 Oct 2026) showed."""

import os
import tempfile
import unittest

from riggrade.engine import preview
from runner import gpu, preflight as pf, rulers
from tests.test_preflight import IDLE_ROW, healthy_obs, snap


class Bf16Tests(unittest.TestCase):
    def test_native_bf16_only_from_ampere(self):
        self.assertFalse(gpu.native_bf16((7, 5)))   # T4: emulated bf16, vLLM refuses it
        self.assertTrue(gpu.native_bf16((8, 0)))    # A100
        self.assertTrue(gpu.native_bf16((8, 9)))    # RTX 4090, L40S
        self.assertTrue(gpu.native_bf16((9, 0)))    # H100


class PowerCapTests(unittest.TestCase):
    def t4_obs(self, limits):
        obs = healthy_obs()
        # T4 under full load: ~57% of max clock, only the power cap active
        obs["load"] = pf.summarise_load([snap(clock=900, temp=61, reasons="0x4")] * 3)
        obs["power_limits"] = limits
        return obs

    def status(self, obs):
        return {f["check"]: f for f in pf.evaluate(obs)}["gpu0.clocks_under_load"]

    def test_power_cap_at_default_limit_passes(self):
        f = self.status(self.t4_obs({0: (70.0, 70.0)}))
        self.assertEqual(f["status"], pf.PASS)
        self.assertIn("by design", f["detail"])

    def test_lowered_power_limit_warns(self):
        f = self.status(self.t4_obs({0: (40.0, 70.0)}))
        self.assertEqual(f["status"], pf.WARN)
        self.assertIn("lowered", f["detail"])

    def test_unknown_default_limit_warns(self):
        self.assertEqual(self.status(self.t4_obs({}))["status"], pf.WARN)

    def test_low_clock_without_power_cap_still_fails(self):
        obs = self.t4_obs({0: (70.0, 70.0)})
        obs["load"] = pf.summarise_load([snap(clock=900, reasons="0x0")] * 3)
        self.assertEqual(self.status(obs)["status"], pf.FAIL)

    def test_thermal_slowdown_is_never_excused_by_power_cap(self):
        obs = self.t4_obs({0: (70.0, 70.0)})
        obs["load"] = pf.summarise_load([snap(clock=900, reasons="0x24")] * 3)
        status = {f["check"]: f["status"] for f in pf.evaluate(obs)}
        self.assertEqual(status["gpu0.clocks_under_load"], pf.FAIL)
        self.assertEqual(status["gpu0.slowdown_reasons"], pf.FAIL)

    def test_all_reasons_are_reported(self):
        f = {x["check"]: x for x in pf.evaluate(self.t4_obs({0: (70.0, 70.0)}))}
        self.assertIn("sw_power_cap", f["gpu0.slowdown_reasons"]["detail"])


class FakeProc:
    def __init__(self, code):
        self.code = code
        self.terminated = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self.code


class ServerStartTests(unittest.TestCase):
    def test_early_exit_reports_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "vllm.log")

            def popen(cmd, stdout, stderr):
                stdout.write("ValueError: Bfloat16 is only supported on GPUs with "
                             "compute capability of at least 8.0.\n")
                stdout.flush()
                self.assertIn("float16", cmd)
                return FakeProc(1)
            server = rulers.InferenceServer(model="m", dtype="float16", log_path=log,
                                            popen=popen, sleep=lambda s: None)
            with self.assertRaises(RuntimeError) as ctx:
                server.__enter__()
            self.assertIn("exited during start-up (code 1)", str(ctx.exception))
            self.assertIn("Bfloat16 is only supported", str(ctx.exception))

    def test_timeout_reports_the_log_and_stops_the_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = FakeProc(None)

            def health():
                raise OSError("refused")
            server = rulers.InferenceServer(model="m", log_path=os.path.join(tmp, "l"),
                                            startup_timeout=0, popen=lambda *a, **k: proc,
                                            health=health, sleep=lambda s: None)
            with self.assertRaises(RuntimeError) as ctx:
                server.__enter__()
            self.assertIn("did not start within 0 s", str(ctx.exception))
            self.assertTrue(proc.terminated)

    def test_healthy_start_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = rulers.InferenceServer(model="m", log_path=os.path.join(tmp, "l"),
                                            popen=lambda *a, **k: FakeProc(None),
                                            health=lambda: True, sleep=lambda s: None)
            self.assertIs(server.__enter__(), server)


class PreviewTests(unittest.TestCase):
    def test_unknown_class_says_why(self):
        bundle = {"identity": {"device_name": "Tesla T4", "memory_gb": 15.0, "card_count": 1},
                  "gate": {"training": {"results": {}, "free_memory_gb": 14.5}}}
        entry = preview(bundle, ["training"])["classes"]["training"]
        self.assertFalse(entry["gate_passed"])
        self.assertEqual(entry["reason"], "no v0 peer class for this device")


if __name__ == "__main__":
    unittest.main()
