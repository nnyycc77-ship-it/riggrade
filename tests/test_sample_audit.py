"""runner.sample_audit: outcomes, model match, totals, no identifiers."""
import json
import unittest

from runner import preflight as pf
from runner import sample_audit as sa
from tests.test_preflight import FakeProbes


def line(box, report):
    return f"RGAUDIT {box} " + json.dumps(pf.anonymise(report))


class SampleAuditTests(unittest.TestCase):
    def test_model_match(self):
        self.assertTrue(sa.model_matches("RTX 4090", "NVIDIA GeForce RTX 4090"))
        self.assertTrue(sa.model_matches("A100 80GB", "NVIDIA A100 80GB PCIe"))
        self.assertFalse(sa.model_matches("RTX 4090", "NVIDIA GeForce RTX 3090"))
        self.assertFalse(sa.model_matches("RTX 4090", "NVIDIA GeForce RTX 4090 D"))
        self.assertFalse(sa.model_matches("A100", "NVIDIA A1000"))

    def test_variants_refuse_a_match(self):
        # Coordinator Message 332 condition (a): Ti, Super and D are other cards.
        self.assertFalse(sa.model_matches("RTX 4090", "NVIDIA GeForce RTX 4090 Ti"))
        self.assertFalse(sa.model_matches("RTX 4080", "NVIDIA GeForce RTX 4080 SUPER"))
        self.assertFalse(sa.model_matches("RTX 4090 Ti", "NVIDIA GeForce RTX 4090"))
        self.assertTrue(sa.model_matches("RTX 4080 Super", "NVIDIA GeForce RTX 4080 SUPER"))

    def test_chip_only_listings_match_full_driver_names(self):
        # Condition (b): naming grain is not a mismatch. Listings as io.net names them (Message 333 picks).
        self.assertTrue(sa.model_matches("A100", "NVIDIA A100-SXM4-80GB"))
        self.assertTrue(sa.model_matches("A100", "NVIDIA A100 80GB PCIe"))
        self.assertTrue(sa.model_matches("H100", "NVIDIA H100 80GB HBM3"))
        self.assertTrue(sa.model_matches("A6000", "NVIDIA RTX A6000"))
        self.assertTrue(sa.model_matches("L40S", "NVIDIA L40S"))
        self.assertTrue(sa.model_matches("V100_32G", "Tesla V100-SXM2-32GB"))
        self.assertTrue(sa.model_matches("RTX6000Ada", "NVIDIA RTX 6000 Ada Generation"))
        self.assertFalse(sa.model_matches("L40S", "NVIDIA L40"))
        self.assertFalse(sa.model_matches("RTX6000Ada", "Quadro RTX 6000"))
        self.assertFalse(sa.model_matches("H100", "NVIDIA H200"))
        self.assertTrue(sa.model_matches("A100 x8", "NVIDIA A100-SXM4-80GB"))

    def test_a_listed_memory_size_is_checked(self):
        self.assertFalse(sa.model_matches("V100_32G", "Tesla V100-SXM2-16GB"))
        self.assertTrue(sa.model_matches("V100_32G", "Tesla V100-SXM2", memory_total_mb=32768))
        self.assertFalse(sa.model_matches("V100_32G", "Tesla V100-SXM2", memory_total_mb=16384))
        self.assertFalse(sa.model_matches("V100_32G", "Tesla V100-SXM2"))   # memory not shown: not proven

    def test_no_torch_is_not_compute_measured(self):
        # Condition (h): compute warn with basis "not measured" (PR 117) never counts as measured.
        import io

        class NoTorch(FakeProbes):
            def load(self, seconds):
                raise ModuleNotFoundError("No module named 'torch'")
        report = pf.run(NoTorch(), seconds=1, download=False, out=io.StringIO())
        name = report["gpus"][0]["name"]
        out = sa.total([{"box": "b1", "listed_model": name, "outcome": "ready", "usd_per_hour": "1",
                         "minutes_billed": "60"}], sa.read_lines(line("b1", report)))
        self.assertEqual(out["compute_measured"], 0)
        self.assertFalse(out["boxes"][0]["compute_measured"])

    def test_untried_picks_and_gpu_counts(self):
        import io
        report = pf.run(FakeProbes(), seconds=1, download=False, out=io.StringIO())
        name = report["gpus"][0]["name"]
        sheet = [{"box": "b1", "listed_model": name, "listed_gpus": "1", "outcome": "ready", "usd_per_hour": "1",
                  "minutes_billed": "60"},
                 {"box": "b2", "listed_model": name, "listed_gpus": "8", "outcome": "not_attempted"}]
        out = sa.total(sheet, sa.read_lines(line("b1", report)))
        self.assertEqual((out["boxes_picked"], out["boxes_tried"]), (2, 1))
        self.assertEqual(out["outcomes"]["not_attempted"], 1)
        self.assertEqual(out["gpu_count_matches_listing"], {"match": 1})
        self.assertEqual(out["cost_usd"], 1.0)

    def test_totals_from_sheet_and_lines(self):
        import io
        report = pf.run(FakeProbes(), seconds=1, download=False, out=io.StringIO())
        name = report["gpus"][0]["name"]
        sheet = [
            {"box": "b1", "network": "io.net", "listed_model": name, "usd_per_hour": "0.30",
             "outcome": "ready", "minutes_billed": "60"},
            {"box": "b2", "network": "io.net", "listed_model": "RTX 3090", "usd_per_hour": "0.20",
             "outcome": "ready", "minutes_billed": "60"},
            {"box": "b3", "network": "io.net", "listed_model": "RTX 4090", "usd_per_hour": "0.30",
             "outcome": "deploy_failed", "minutes_billed": "0"},
            {"box": "b4", "network": "io.net", "listed_model": "RTX 4090", "usd_per_hour": "0.30",
             "outcome": "ready", "minutes_billed": "60"},   # ready but never printed a line
        ]
        text = "\n".join([line("b1", report), line("b2", report), "noise", "RGAUDIT b9 {broken"])
        out = sa.total(sheet, sa.read_lines(text))
        self.assertEqual(out["boxes_tried"], 4)
        self.assertEqual(out["outcomes"], {"ready": 2, "deploy_failed": 1, "ready_no_report": 1})
        self.assertEqual(out["boxes_checked"], 2)
        self.assertEqual(out["model_matches_listing"], {"match": 1, "mismatch": 1})
        self.assertEqual(out["cost_usd"], 0.8)
        dumped = json.dumps(out)
        for word in ("card_id", "serial", "rig_id"):
            self.assertNotIn(word, dumped)

    def test_ready_no_report_written_on_the_sheet(self):
        # Coordinator Message 336 condition (1): the outcome the method prescribes is accepted,
        # counted as tried, never as checked or as a pass.
        out = sa.total([{"box": "b1", "listed_model": "A100", "outcome": "ready_no_report", "usd_per_hour": "1",
                         "minutes_billed": "60"}], {})
        self.assertEqual((out["boxes_tried"], out["boxes_checked"]), (1, 0))
        self.assertEqual(out["outcomes"], {"ready_no_report": 1})
        self.assertEqual(out["verdicts"], {})

    def test_bad_outcome_is_refused(self):
        with self.assertRaises(SystemExit):
            sa.total([{"box": "x", "outcome": "fine"}], {})


if __name__ == "__main__":
    unittest.main()
