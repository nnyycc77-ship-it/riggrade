import io
import os
import tempfile
import unittest

from riggrade.label import lint
from runner import preflight as pf

FIELDS = pf.GPU_FIELDS
IDLE_ROW = ("0, NVIDIA GeForce RTX 4090, 550.54.15, 210, 3120, 34, 21.5, 450.00, 0, "
            "310, 24564, 1, 4, 16, 16, 0x0000000000000001")


def snap(clock=2700, temp=70, reasons="0x0000000000000004", gen=4, width=16):
    row = ("0, NVIDIA GeForce RTX 4090, 550.54.15, %d, 3120, %d, 440.1, 450.00, 100, "
           "20000, 24564, %d, 4, %d, 16, %s" % (clock, temp, gen, width, reasons))
    return pf.parse_gpu_csv(row)


class ParseTests(unittest.TestCase):
    def test_gpu_row(self):
        (g,) = pf.parse_gpu_csv(IDLE_ROW)
        self.assertEqual(g["name"], "NVIDIA GeForce RTX 4090")
        self.assertEqual(g["memory.used"], 310)
        self.assertEqual(g["reasons"], 1)
        self.assertEqual(pf.decode_reasons(0x24), ["sw_power_cap", "sw_thermal_slowdown"])

    def test_not_supported_fields_are_none(self):
        row = IDLE_ROW.replace("21.5", "[N/A]").replace("0x0000000000000001", "[Not Supported]")
        (g,) = pf.parse_gpu_csv(row)
        self.assertIsNone(g["power.draw"])
        self.assertIsNone(g["reasons"])

    def test_cuda_versions(self):
        smi = "| NVIDIA-SMI 550.54.15   Driver Version: 550.54.15   CUDA Version: 12.4     |"
        self.assertEqual(pf.parse_driver_cuda(smi), (12, 4))
        self.assertIsNone(pf.parse_driver_cuda("no gpu"))
        self.assertEqual(pf.parse_version("12.1"), (12, 1))

    def test_legacy_field_retry(self):
        calls = []

        class Out:
            def __init__(self, rc, stdout=""):
                self.returncode, self.stdout, self.stderr = rc, stdout, "bad field"

        def run(cmd, **kw):
            calls.append(cmd[1])
            return Out(1) if "clocks_event_reasons" in cmd[1] else Out(0, IDLE_ROW)
        (g,) = pf.query_gpus(run=run)
        self.assertEqual(len(calls), 2)
        self.assertIn(pf.LEGACY_REASONS_FIELD, calls[1])
        self.assertEqual(g["reasons"], 1)


class SummaryTests(unittest.TestCase):
    def test_median_clock_and_reasons(self):
        load = pf.summarise_load([snap(2800), snap(2700), snap(600, reasons="0x20", temp=91)])[0]
        self.assertAlmostEqual(load["clock_ratio"], round(2700 / 3120, 3))
        self.assertEqual(load["peak_temperature_c"], 91)
        self.assertEqual(load["reasons"], ["sw_power_cap", "sw_thermal_slowdown"])


def healthy_obs():
    return {"idle": pf.parse_gpu_csv(IDLE_ROW),
            "load": pf.summarise_load([snap(), snap()]),
            "driver_cuda": (12, 4), "software_cuda": (12, 1),
            "download_mbs": 180.0, "disk_write_mbs": 900}


class EvaluateTests(unittest.TestCase):
    def status(self, obs):
        return {f["check"]: f["status"] for f in pf.evaluate(obs)}

    def test_healthy_box_passes_and_power_cap_is_not_a_failure(self):
        findings = pf.evaluate(healthy_obs())
        self.assertEqual(pf.verdict(findings)["verdict"], pf.PASS)

    def test_throttled_box_that_reads_busy_fails(self):
        obs = healthy_obs()
        obs["load"] = pf.summarise_load([snap(400, temp=91, reasons="0x20")] * 3)
        s = self.status(obs)
        self.assertEqual(s["gpu0.clocks_under_load"], pf.FAIL)
        self.assertEqual(s["gpu0.slowdown_reasons"], pf.FAIL)
        self.assertEqual(s["gpu0.temperature"], pf.FAIL)
        self.assertEqual(pf.verdict(pf.evaluate(obs))["verdict"], pf.FAIL)

    def test_foreign_memory_and_compute(self):
        obs = healthy_obs()
        obs["idle"] = pf.parse_gpu_csv(IDLE_ROW.replace("450.00, 0, 310,", "450.00, 50, 16400,"))
        s = self.status(obs)
        self.assertEqual(s["gpu0.foreign_memory"], pf.FAIL)
        self.assertEqual(s["gpu0.foreign_compute"], pf.FAIL)

    def test_driver_too_old(self):
        obs = healthy_obs()
        obs["driver_cuda"], obs["software_cuda"] = (11, 8), (12, 1)
        self.assertEqual(self.status(obs)["driver_vs_cuda"], pf.FAIL)
        obs["driver_cuda"] = (12, 0)
        self.assertEqual(self.status(obs)["driver_vs_cuda"], pf.WARN)

    def test_narrow_pcie_and_slow_download_warn_or_fail(self):
        obs = healthy_obs()
        obs["load"] = pf.summarise_load([snap(width=4)])
        obs["download_mbs"] = 9.5
        s = self.status(obs)
        self.assertEqual(s["gpu0.pcie_link"], pf.WARN)
        self.assertEqual(s["download_rate"], pf.FAIL)

    def test_missing_measurements_are_skipped_not_passed(self):
        s = self.status({"idle": [], "download_mbs": None})
        self.assertEqual(s["download_rate"], "skipped")
        self.assertEqual(s["driver_vs_cuda"], "skipped")

    def test_advice_text_is_promise_free(self):
        texts = [pf.verdict([{"check": "x", "status": s, "detail": ""}])["advice"]
                 for s in (pf.PASS, pf.WARN, pf.FAIL)]
        self.assertEqual(lint(texts + [pf.__doc__]), [])


class FakeProbes:
    def __init__(self, fail_load=False):
        self.fail_load = fail_load

    def gpus(self):
        return pf.parse_gpu_csv(IDLE_ROW)

    def driver_cuda(self):
        return (12, 4)

    def software_cuda(self):
        return (12, 1)

    def load(self, seconds):
        if self.fail_load:
            raise RuntimeError("CUDA is not available to this process")
        return pf.summarise_load([snap(), snap()]), {0: 150.2}

    def download(self, url):
        return 120.0

    def disk(self, directory):
        return 1500


class RunTests(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_pass_run_writes_report(self):
        out = io.StringIO()
        report = pf.run(FakeProbes(), seconds=1, out=out)
        self.assertEqual(report["verdict"], pf.PASS)
        self.assertEqual(report["tflops_per_gpu"], {0: 150.2})
        self.assertTrue(os.path.exists("preflight-report.json"))
        self.assertIn("VERDICT: PASS", out.getvalue())

    def test_compute_failure_is_recorded_and_fails(self):
        report = pf.run(FakeProbes(fail_load=True), seconds=1, download=False,
                        out=io.StringIO())
        self.assertEqual(report["verdict"], pf.FAIL)
        self.assertIn("compute", report["problems"])
        self.assertIn("load", report["errors"])

    def test_no_gpu_and_no_torch(self):
        class NoGpu(FakeProbes):
            def gpus(self):
                raise FileNotFoundError("nvidia-smi")

            def load(self, seconds):
                raise ModuleNotFoundError("No module named 'torch'")
        report = pf.run(NoGpu(), seconds=1, download=False, out=io.StringIO())
        status = {f["check"]: f["status"] for f in report["findings"]}
        self.assertEqual(status["gpu_visible"], pf.FAIL)
        self.assertEqual(status["compute"], "skipped")

    def test_disk_probe_measures_and_cleans_up(self):
        mbs = pf.measure_disk(".", mb=2)
        self.assertGreater(mbs, 0)
        self.assertEqual([p for p in os.listdir(".") if p.startswith("riggrade-preflight-")], [])

    def test_download_probe(self):
        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        mbs = pf.measure_download("http://x", opener=lambda req, timeout: Resp(b"a" * (3 << 20)))
        self.assertGreater(mbs, 0)


if __name__ == "__main__":
    unittest.main()
