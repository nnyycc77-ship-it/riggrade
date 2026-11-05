import io
import tempfile
import unittest
from pathlib import Path

from riggrade.endpoint import GradingApp
from runner import identity, stats
from runner.main import measure, run
from runner.bundle import output_entry
from runner.local import InProcessClient

CSV = ("NVIDIA GeForce RTX 4090, 24564, 550.54, 00000000:01:00.0, GPU-aaa\n"
       "NVIDIA GeForce RTX 4090, 24564, 550.54, 00000000:02:00.0, GPU-bbb\n")
IMAGE = "sha256:" + "cd" * 32


class IdentityTests(unittest.TestCase):
    def test_parse(self):
        got = identity.parse(CSV)
        self.assertEqual(got["device_name"], "NVIDIA GeForce RTX 4090")
        self.assertEqual(got["memory_gb"], 24.0)
        self.assertEqual(got["card_count"], 2)
        self.assertTrue(got["rig_id"].startswith("rg_"))

    def test_serial_recorded_where_exposed_and_uuid_never_written(self):
        got = identity.parse("NVIDIA H100 80GB HBM3, 81559, 550.54, 0:03:00.0, GPU-aaaa, 1650123456789\n"
                             "NVIDIA H100 80GB HBM3, 81559, 550.54, 0:04:00.0, GPU-bbbb, [N/A]\n")
        self.assertEqual([c["serial"] for c in got["cards"]], ["1650123456789", None])
        self.assertTrue(all(c["card_id"].startswith("gc_") for c in got["cards"]))
        self.assertNotIn("GPU-aaaa", repr(got))
        self.assertEqual(identity.card_id("GPU-aaaa"), got["cards"][0]["card_id"])

    def test_rig_id_ignores_order_and_changes_with_cards(self):
        self.assertEqual(identity.rig_id(["a", "b"]), identity.rig_id(["b", "a"]))
        self.assertNotEqual(identity.rig_id(["a", "b"]), identity.rig_id(["a", "c"]))

    def test_mixed_and_empty_refused(self):
        with self.assertRaises(identity.IdentityError):
            identity.parse(CSV + "NVIDIA H100 PCIe, 81559, 550.54, 0:03:00.0, GPU-c\n")
        with self.assertRaises(identity.IdentityError):
            identity.parse("")


class StatsTests(unittest.TestCase):
    def test_percentile(self):
        self.assertEqual(stats.percentile(list(range(1, 101)), 99), 99)
        self.assertEqual(stats.percentile([5], 50), 5)
        self.assertIsNone(stats.percentile([], 50))

    def test_decay(self):
        samples = [(t, 100.0 if t < 1800 else 90.0) for t in range(0, 3601, 60)]
        self.assertAlmostEqual(stats.decay(samples), 0.10)
        self.assertIsNone(stats.decay([(0, 1.0), (60, 1.0)]))
        rising = [(t, 100.0 + t) for t in range(0, 3601, 60)]
        self.assertEqual(stats.decay(rising), 0.0)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeBackend:
    def __init__(self, clock, throughput=1000.0, gate_ok=True):
        self.clock, self.throughput, self.gate_ok = clock, throughput, gate_ok

    def gate(self, workload):
        return ({"memory_integrity": self.gate_ok, "gemm": True,
                 "memory_bandwidth": True, "host_link": True}, 23.0)

    def micro(self):
        return {"memory_gb": 24.0, "gemm_tflops": 160.0, "mem_bw_gbs": 900.0}

    def ruler_pass(self, workload, params):
        return self.throughput, [0.010, 0.011, 0.012, 0.030]

    def sustained_sample(self, workload, params):
        self.clock.t += 60
        return self.throughput


class MeasureTests(unittest.TestCase):
    def test_measure_collects_passes_and_sustained_run(self):
        clock = FakeClock()
        params = {"passes": 3, "pass_order": ["inference"],
                  "inference": {"concurrency": 4}}
        gate, micro, metrics, raw = measure(FakeBackend(clock), params,
                                            ["inference"], clock=clock)
        m = metrics["inference"]
        self.assertEqual(m["throughput_passes"], [1000.0] * 3)
        self.assertEqual(m["decay"], 0.0)
        self.assertAlmostEqual(m["p99_p50"], 0.030 / 0.011)
        self.assertGreaterEqual(raw["ruler.inference"]["sustained"][-1][0], 3600)

    def test_failed_gate_skips_rulers(self):
        clock = FakeClock()
        params = {"passes": 3, "pass_order": ["inference"]}
        gate, _m, metrics, _r = measure(FakeBackend(clock, gate_ok=False), params,
                                        ["inference"], clock=clock)
        self.assertFalse(gate["inference"]["results"]["memory_integrity"])
        self.assertEqual(metrics, {})


class EndToEndTests(unittest.TestCase):
    def test_container_bundle_is_accepted_by_the_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"RIGGRADE_SECRET": "x", "RIGGRADE_IMAGE_DIGESTS": IMAGE,
                   "RIGGRADE_SALE_LOG": str(Path(tmp) / "s.jsonl")}
            app = GradingApp(env, lambda: 1_800_000_000)
            ident = identity.parse(CSV.splitlines()[0])
            out = io.StringIO()
            clock = FakeClock()
            report = run(InProcessClient(app), FakeBackend(clock), ident,
                         ["inference"], "buyer", IMAGE, out=out, clock=clock)
        self.assertTrue(report["accepted"])
        cls = report["preview"]["classes"]["inference"]
        self.assertTrue(cls["gate_passed"])
        self.assertEqual(cls["notice"], "reference-building session, speed not graded")
        self.assertTrue(report["grade_url"].endswith("/grade"))
        self.assertIn('"accepted": true', out.getvalue())

    def test_unpinned_image_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"RIGGRADE_SECRET": "x", "RIGGRADE_IMAGE_DIGESTS": IMAGE,
                   "RIGGRADE_SALE_LOG": str(Path(tmp) / "s.jsonl")}
            app = GradingApp(env, lambda: 1_800_000_000)
            clock = FakeClock()
            report = run(InProcessClient(app), FakeBackend(clock),
                         identity.parse(CSV.splitlines()[0]), ["inference"],
                         "buyer", "unpinned", out=io.StringIO(), clock=clock)
        self.assertFalse(report["accepted"])

    def test_output_entry_hash(self):
        entry = output_entry({"b": 1, "a": [2]})
        self.assertEqual(entry["content"], '{"a":[2],"b":1}')
        self.assertEqual(len(entry["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()


class AutoGradeTests(unittest.TestCase):
    """Owner decision, 5 Nov 2026: during the trial the container asks for the
    free, no-wallet grade itself and prints it, so the run ends with the grade."""

    def app(self, tmp, trial=True):
        from tests.test_endpoint import WALLET, FakeAdapter
        env = {"RIGGRADE_ENABLED": "1", "PAYOUT_WALLET_ADDRESS": WALLET, "RIGGRADE_SECRET": "x",
               "RIGGRADE_IMAGE_DIGESTS": IMAGE, "RIGGRADE_SALE_LOG": str(Path(tmp) / "s.jsonl")}
        if trial:
            env.update(RIGGRADE_TRIAL_PRODUCTS="grade", RIGGRADE_TRIAL_UNTIL="2027-12-31")
        return GradingApp(env, lambda: 1_800_000_000, adapter=FakeAdapter())

    def go(self, app, **kw):
        out = io.StringIO()
        clock = FakeClock()
        report = run(InProcessClient(app), FakeBackend(clock), identity.parse(CSV.splitlines()[0]),
                     ["inference"], "host", IMAGE, out=out, clock=clock, **kw)
        return report, out.getvalue()

    def test_the_run_ends_with_the_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, text = self.go(self.app(tmp))
        self.assertTrue(report["graded"])
        self.assertTrue(report["grade"]["labels"])
        self.assertTrue(report["grade"]["evidence_pack"]["no_wallet"])
        self.assertIn("Your RigGrade grade (free, no wallet used, nothing charged):", text)
        self.assertTrue(text.rstrip().endswith("}"))            # the JSON report still ends the output

    def test_switched_off_for_idle_grading(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.app(tmp)
            report, text = self.go(app, auto_grade=False)
        self.assertNotIn("graded", report)
        self.assertNotIn("Your RigGrade grade", text)

    def test_outside_the_trial_it_points_to_the_grade_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, text = self.go(self.app(tmp, trial=False))
        self.assertFalse(report["graded"])
        self.assertIn("Open the grade_url below in a browser", text)
        self.assertTrue(report["grade_url"].endswith("/grade"))
