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
    idle_interval = 0  # no real sleeping between idle samples in tests

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

    def test_a_visible_gpu_without_torch_is_never_a_pass(self):
        # Reported in an outside review (3 Nov 2026): compute skipped, verdict PASS.
        class NoTorch(FakeProbes):
            def load(self, seconds):
                raise ModuleNotFoundError("No module named 'torch'")
        report = pf.run(NoTorch(), seconds=1, download=False, out=io.StringIO())
        status = {f["check"]: f["status"] for f in report["findings"]}
        self.assertEqual(status["compute"], pf.WARN)
        self.assertEqual(report["verdict"], pf.WARN)
        self.assertIn("pip install torch", next(f["detail"] for f in report["findings"] if f["check"] == "compute"))

    def test_unmeasured_compute_is_never_counted_as_a_pass(self):
        # Message 321 condition 2: the warn stays a warn in the machine-readable
        # report, the shared summary, the record entry and the pooled figures.
        from riggrade import pool

        class NoTorch(FakeProbes):
            def load(self, seconds):
                raise ModuleNotFoundError("No module named 'torch'")
        report = pf.run(NoTorch(), seconds=1, download=False, out=io.StringIO())
        compute = next(f for f in report["findings"] if f["check"] == "compute")
        self.assertEqual((compute["status"], compute["basis"], compute["hint"]),
                         (pf.WARN, "not measured", "pip install torch"))
        load_kinds = ("clocks_under_load", "slowdown_reasons", "temperature", "memory_bandwidth")
        for f in report["findings"]:
            if f["check"].split(".", 1)[-1] in load_kinds:
                self.assertNotEqual(f["status"], pf.PASS, f["check"])
        for doc in (pf.anonymise(report), pf.record_doc(report, "buyer")):
            self.assertEqual(doc["verdict"], pf.WARN)
            self.assertEqual(doc["statuses"]["compute"], pf.WARN)
        rows = [dict(pf.anonymise(report), peer_class="rtx-4090-24") for _ in range(pool.MIN_N)]
        agg = pool.aggregate(rows)["rtx-4090-24"]
        self.assertEqual(agg["verdict_share"]["pass"], 0)
        self.assertNotIn("pass", agg["check_status_share"]["compute"])

    def test_cards_parse_and_never_reach_the_share(self):
        got = pf.parse_cards_csv("0, GPU-1111, 1650000000001\n1, GPU-2222, [N/A]\n")
        self.assertEqual(got["cards"][0]["serial"], "1650000000001")
        self.assertEqual([c["index"] for c in got["cards"]], [0, 1])
        self.assertIsInstance(got["cards"][0]["index"], int)
        self.assertIsNone(got["cards"][1]["serial"])
        self.assertTrue(got["rig_id"].startswith("rg_"))
        self.assertNotIn("GPU-1111", repr(got))
        self.assertIsNone(pf.parse_cards_csv(""))
        report = {"preflight": True, "verdict": "pass", "findings": [], "measures": {},
                  "gpus": [{"name": "NVIDIA RTX 4090", "driver": "550"}],
                  "rig_id": got["rig_id"], "cards": got["cards"]}
        shared = pf.anonymise(report)
        self.assertNotIn("1650000000001", repr(shared))
        self.assertNotIn("rig_id", shared)
        self.assertNotIn("cards", shared)

    def test_shared_box_skips_foreign_checks_not_fails(self):
        class Busy(FakeProbes):
            def gpus(self):
                return pf.parse_gpu_csv(IDLE_ROW.replace("450.00, 0, 310,", "450.00, 80, 20000,"))
        report = pf.run(Busy(), seconds=1, download=False, out=io.StringIO(), shared=True)
        status = {f["check"]: f["status"] for f in report["findings"]}
        self.assertEqual(status["gpu0.foreign_memory"], "skipped")
        self.assertEqual(status["gpu0.foreign_compute"], "skipped")
        self.assertFalse(report["exclusive_box_assumed"])
        self.assertEqual(report["verdict"], pf.PASS)

        class Mig(Busy):
            def mig_enabled(self):
                return True
        report = pf.run(Mig(), seconds=1, download=False, out=io.StringIO())
        self.assertIn("MIG", report["findings"][0]["detail"])
        # without either, the same box fails on foreign use
        report = pf.run(Busy(), seconds=1, download=False, out=io.StringIO())
        self.assertEqual(report["verdict"], pf.FAIL)

    def test_warn_is_distinct_in_report_and_exit_code(self):
        class Slow(FakeProbes):
            def download(self, url):
                return 30.0
        report = pf.run(Slow(), seconds=1, out=io.StringIO())
        self.assertEqual(report["verdict"], pf.WARN)
        status = {f["check"]: f["status"] for f in report["findings"]}
        self.assertEqual(status["download_rate"], pf.WARN)
        import unittest.mock as mock
        import contextlib
        with mock.patch.object(pf, "Probes", Slow), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pf.main(["--seconds", "1"]), 0)
            self.assertEqual(pf.main(["--seconds", "1", "--fail-on-warn"]), 1)

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


class DiagnosticsTests(unittest.TestCase):
    """ECC counts, host starvation, mount type, memory bandwidth (1 Nov 2026)."""

    def status(self, obs):
        return {f["check"]: f["status"] for f in pf.evaluate(obs)}

    def detail(self, obs, check):
        return {f["check"]: f["detail"] for f in pf.evaluate(obs)}[check]

    def test_ecc_parse_and_rules(self):
        rows = ("0, Enabled, 3, 0, 120, 0, No\n"
                "1, Enabled, 40, 1, 500, 2, Yes\n"
                "2, [N/A], [N/A], [N/A], [N/A], [N/A], [N/A]\n"
                "3, Disabled, 0, 0, 0, 0, No\n")
        ecc = pf.parse_ecc_csv(rows)
        self.assertEqual(ecc[0]["corrected_volatile"], 3)
        self.assertTrue(ecc[1]["retired_pages_pending"])
        self.assertIsNone(ecc[2]["mode"])
        obs = healthy_obs()
        obs["ecc"] = ecc
        st = self.status(obs)
        self.assertEqual(st["gpu0.ecc_errors"], pf.PASS)
        self.assertEqual(st["gpu1.ecc_errors"], pf.FAIL)
        self.assertEqual(st["gpu2.ecc_errors"], "skipped")
        self.assertEqual(st["gpu3.ecc_errors"], "skipped")
        self.assertIn("1 uncorrected", self.detail(obs, "gpu1.ecc_errors"))
        self.assertIn("retired pages pending", self.detail(obs, "gpu1.ecc_errors"))
        self.assertIn("consumer cards have none", self.detail(obs, "gpu2.ecc_errors"))
        obs["ecc"] = {0: dict(ecc[0], corrected_volatile=50)}
        self.assertEqual(self.status(obs)["gpu0.ecc_errors"], pf.WARN)

    def test_host_parse_and_rules(self):
        meminfo = "MemTotal:       65536000 kB\nMemAvailable:    5898240 kB\nSwapTotal:  8000000 kB\nSwapFree:   2000000 kB\n"
        host = pf.parse_host("12.50 10.0 9.0 1/800 12345", meminfo, 8, "powersave\n")
        self.assertEqual(host["load1_per_core"], 1.56)
        self.assertEqual(host["mem_available_pct"], 9.0)
        self.assertEqual(host["swap_used_pct"], 75.0)
        self.assertEqual(host["governor"], "powersave")
        obs = healthy_obs()
        obs["host"] = host
        st = self.status(obs)
        self.assertEqual((st["host.cpu_load"], st["host.memory"], st["host.swap"], st["host.cpu_governor"]),
                         (pf.WARN, pf.FAIL, pf.FAIL, pf.WARN))
        self.assertIn("1.56 per core (8 CPUs)", self.detail(obs, "host.cpu_load"))
        quiet = pf.parse_host("0.40 0.5 0.6 1/200 1", "MemTotal: 65536000 kB\nMemAvailable: 60000000 kB\n", 8, "")
        obs["host"] = quiet
        st = self.status(obs)
        self.assertEqual((st["host.cpu_load"], st["host.memory"], st["host.swap"], st["host.cpu_governor"]),
                         (pf.PASS, pf.PASS, pf.PASS, "skipped"))
        self.assertEqual(self.detail(obs, "host.swap"), "no swap configured")
        self.assertEqual(pf.parse_host("", "", None)["load1_per_core"], None)

    def test_mount_type_names_the_filesystem(self):
        mounts = ("overlay / overlay rw,relatime 0 0\n"
                  "proc /proc proc rw 0 0\n"
                  "/dev/nvme0n1p1 /workspace ext4 rw,relatime 0 0\n"
                  "nfs.example:/data /workspace/data nfs4 rw 0 0\n"
                  "tmpfs /mnt/with\\040space tmpfs rw 0 0\n")
        self.assertEqual(pf.find_mount(mounts, "/workspace/ckpt"), ("/workspace", "ext4"))
        self.assertEqual(pf.find_mount(mounts, "/workspace/data/x"), ("/workspace/data", "nfs4"))
        self.assertEqual(pf.find_mount(mounts, "/root"), ("/", "overlay"))
        self.assertEqual(pf.find_mount(mounts, "/mnt/with space/f"), ("/mnt/with space", "tmpfs"))
        self.assertEqual(pf.find_mount("", "/x"), (None, None))
        obs = healthy_obs()
        obs["disk_write_mbs"] = 137
        obs["disk_mount"] = {"fstype": "overlay", "mountpoint": "/"}
        d = self.detail(obs, "disk_write")
        self.assertIn("137 MB/s sequential write on overlay at / (network or layered storage)", d)
        obs["disk_mount"] = {"fstype": "ext4", "mountpoint": "/workspace"}
        self.assertNotIn("network or layered", self.detail(obs, "disk_write"))
        self.assertIn("on ext4 at /workspace", self.detail(obs, "disk_write"))

    def test_memory_bandwidth_rule(self):
        obs = healthy_obs()
        obs["mem_bw_gbs"] = {0: 820, 1: 60, 2: None}
        st = self.status(obs)
        self.assertEqual((st["gpu0.memory_bandwidth"], st["gpu1.memory_bandwidth"], st["gpu2.memory_bandwidth"]),
                         (pf.PASS, pf.WARN, "skipped"))
        self.assertIn("820 GB/s", self.detail(obs, "gpu0.memory_bandwidth"))

    def test_run_uses_the_new_probes_when_present(self):
        class Fake:
            idle_interval = 0

            def gpus(self):
                return pf.parse_gpu_csv(IDLE_ROW)

            def driver_cuda(self):
                return (12, 4)

            def software_cuda(self):
                return (12, 1)

            def load(self, seconds):
                return pf.summarise_load([snap(), snap()]), {0: 150.0}

            def download(self, url):
                return 180.0

            def disk(self, directory):
                return 137

            def ecc(self):
                return pf.parse_ecc_csv("0, Enabled, 0, 0, 0, 0, No\n")

            def host(self):
                return pf.parse_host("0.5 0.5 0.5 1/1 1", "MemTotal: 100 kB\nMemAvailable: 90 kB\n", 4, "performance")

            def mount(self, directory):
                return {"fstype": "overlay", "mountpoint": "/"}

            def bandwidth(self):
                return {0: 900}
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                report = pf.run(Fake(), seconds=0, download=False, directory=tmp, out=io.StringIO())
            finally:
                os.chdir(cwd)
        by = {f["check"]: f for f in report["findings"]}
        self.assertEqual(by["gpu0.ecc_errors"]["status"], pf.PASS)
        self.assertEqual(by["host.cpu_governor"]["status"], pf.PASS)
        self.assertEqual(by["gpu0.memory_bandwidth"]["status"], pf.PASS)
        self.assertEqual(by["disk_write"]["status"], pf.WARN)
        self.assertIn("overlay", by["disk_write"]["detail"])
        self.assertEqual(report["verdict"], pf.WARN)
        self.assertEqual(lint([json_text for json_text in [str(report["findings"])]]), [])
