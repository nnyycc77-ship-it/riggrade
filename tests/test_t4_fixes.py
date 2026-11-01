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
            server.__exit__(None, None, None)
            self.assertTrue(server._log.closed)


class PreviewTests(unittest.TestCase):
    def test_unknown_class_says_why(self):
        bundle = {"identity": {"device_name": "Tesla T4", "memory_gb": 15.0, "card_count": 1},
                  "gate": {"training": {"results": {}, "free_memory_gb": 14.5}}}
        entry = preview(bundle, ["training"])["classes"]["training"]
        self.assertFalse(entry["gate_passed"])
        self.assertEqual(entry["reason"], "no v0 peer class for this device")


if __name__ == "__main__":
    unittest.main()


class SecondRunTests(unittest.TestCase):
    """What the second T4 run (1 Nov 2026) showed."""

    def test_server_corrects_bf16_below_ampere(self):
        s = rulers.InferenceServer(model="m", dtype="bfloat16", capability=lambda: (7, 5))
        self.assertEqual((s.dtype, s.dtype_corrected), ("float16", True))
        s = rulers.InferenceServer(model="m", dtype="bfloat16", capability=lambda: (8, 9))
        self.assertEqual((s.dtype, s.dtype_corrected), ("bfloat16", False))
        s = rulers.InferenceServer(model="m", dtype="bfloat16", capability=lambda: None)
        self.assertEqual(s.dtype, "bfloat16")

    def test_root_cause_line_is_found_above_the_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "vllm.log")

            def popen(cmd, stdout, stderr):
                stdout.write("INFO starting\n")
                stdout.write("ValueError: Free memory on device (5.1/14.6 GiB) on startup is "
                             "less than desired GPU memory utilization (0.6, 8.8 GiB).\n")
                stdout.write("".join("  frame %d\n" % k for k in range(40)))
                stdout.write("RuntimeError: Engine core initialization failed. "
                             "See root cause above. Failed core proc(s): {}\n")
                stdout.flush()
                return FakeProc(1)
            server = rulers.InferenceServer(model="m", dtype="float16", log_path=log,
                                            popen=popen, sleep=lambda s: None)
            with self.assertRaises(RuntimeError) as ctx:
                server.__enter__()
            msg = str(ctx.exception)
            self.assertIn("less than desired GPU memory utilization", msg)
            self.assertIn("Error lines in the server log", msg)

    def test_idle_state_is_the_median_of_several_samples(self):
        busy = IDLE_ROW.replace("450.00, 0, 310,", "450.00, 35, 310,")

        class Fading:
            idle_interval = 0

            def __init__(self):
                self.calls = 0

            def gpus(self):
                self.calls += 1
                return pf.parse_gpu_csv(busy if self.calls == 1 else IDLE_ROW)
        probes = Fading()
        (row,) = pf.idle_snapshot(probes, samples=5)
        self.assertEqual(probes.calls, 5)
        self.assertEqual(row["utilization.gpu"], 0)
        self.assertEqual(row["idle_samples"], 5)


class ThirdRunTests(unittest.TestCase):
    """What the third T4 run (1 Nov 2026, 14 of 14 smoke steps) showed."""

    def test_warmup_pass_is_kept_out_of_the_timed_passes(self):
        from runner.main import measure
        from tests.test_runner import FakeBackend, FakeClock

        class ColdStart(FakeBackend):
            calls = 0

            def ruler_pass(self, workload, params):
                self.calls += 1
                # first pass after start-up runs at half speed, as on the T4
                return (327.0 if self.calls == 1 else 650.0), [0.01, 0.011]
        clock = FakeClock()
        params = {"passes": 3, "pass_order": ["inference"], "inference": {"concurrency": 4}}
        _gate, _micro, metrics, raw = measure(ColdStart(clock), params, ["inference"],
                                              sustained_s=120, clock=clock)
        self.assertEqual(metrics["inference"]["throughput_passes"], [650.0, 650.0, 650.0])
        self.assertEqual(raw["warmup.inference"], {"throughput": 327.0})

    def test_preview_for_undefined_class_shows_no_gate_list(self):
        bundle = {"identity": {"device_name": "Tesla T4", "memory_gb": 15.0, "card_count": 1},
                  "gate": {"inference": {"results": {}, "free_memory_gb": 14.5}}}
        entry = preview(bundle, ["inference"])["classes"]["inference"]
        self.assertEqual(entry, {"gate_passed": False,
                                 "reason": "no v0 peer class for this device"})
