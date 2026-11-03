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

    def test_bad_outcome_is_refused(self):
        with self.assertRaises(SystemExit):
            sa.total([{"box": "x", "outcome": "fine"}], {})


if __name__ == "__main__":
    unittest.main()
