import io
import json
import os
import tempfile
import unittest

from runner import smoke


class FakeGpu:
    def __init__(self, fail_gemm=False):
        self.fail_gemm = fail_gemm

    def preferred_dtype(self):
        return "float16"

    def free_memory_gb(self):
        return 14.6

    def memory_integrity(self, fraction=0.5):
        return True

    def gemm(self, n=4096, reps=10, dtype="bfloat16"):
        if self.fail_gemm:
            raise RuntimeError("CUDA error: out of memory")
        return True, 61.2

    def memory_bandwidth(self, gb=1.0, reps=5):
        return True, 280.4

    def host_link(self, gb=0.5, reps=3):
        return True, 11.9


class FakeServer:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.stopped = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stopped = True

    def run_pass(self, params):
        return 1500.0, 0.05, [0.010, 0.011, 0.012, 0.030]


class FakeRulers:
    def __init__(self):
        self.servers = []

    def training_pass(self, params, seconds=60, device=0, dtype="bfloat16"):
        return 9000.0

    def InferenceServer(self, **kwargs):
        server = FakeServer(**kwargs)
        self.servers.append(server)
        return server


class FakeIdentity:
    @staticmethod
    def collect():
        return {"device_name": "Tesla T4", "memory_gb": 15.0, "card_count": 1,
                "rig_id": "rig-test"}


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_full_run_reaches_endpoint_and_writes_report(self):
        rulers = FakeRulers()
        report = smoke.run(FakeGpu(), rulers, FakeIdentity, minutes=0.0005,
                           out=io.StringIO())
        self.assertEqual(report["summary"]["failed"], [])
        self.assertEqual(report["dtype"], "float16")
        self.assertEqual(rulers.servers[0].kwargs["model"], smoke.SMOKE_MODEL)
        self.assertEqual(rulers.servers[0].kwargs["dtype"], "float16")
        self.assertTrue(rulers.servers[0].stopped)
        self.assertEqual(report["endpoint"]["accepted"], True)
        self.assertTrue(report["smoke"])
        with open("smoke-report.json", encoding="utf-8") as fh:
            written = json.load(fh)
        self.assertEqual(written["summary"], report["summary"])

    def test_failing_step_is_recorded_not_raised(self):
        report = smoke.run(FakeGpu(fail_gemm=True), FakeRulers(), FakeIdentity,
                           inference=False, out=io.StringIO())
        self.assertEqual(report["summary"]["failed"], ["micro.gemm"])
        self.assertIn("out of memory", report["steps"]["micro.gemm"]["error"])
        self.assertTrue(report["steps"]["ruler.training"]["ok"])
        self.assertTrue(report["steps"]["endpoint.session"]["ok"])
        self.assertNotIn("inference", report["metrics"])


if __name__ == "__main__":
    unittest.main()
