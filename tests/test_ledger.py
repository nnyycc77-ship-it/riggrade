"""The compute supply ledger feed document: posture first and last, every
signal no-signal while its series are missing, arithmetic labelled, and
nothing filled in by hand."""

import unittest

from riggrade import index, ledger


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.doc = index.load_packaged()

    def test_feed_carries_posture_first_and_last_and_no_signal_states(self):
        out = ledger.feed(self.doc, weeks=4)
        self.assertEqual(out["read_this_first"], list(ledger.POSTURE))
        self.assertEqual(out["read_this_last"], list(ledger.POSTURE))
        self.assertEqual(next(iter(out)), "product")
        self.assertEqual(list(out)[-1], "read_this_last")
        self.assertIn("long-tail", out["read_this_first"][1])
        for name, sig in out["signals"].items():
            self.assertEqual(sig["state"], "no signal", name)
            self.assertTrue(sig["missing_series"], name)
        self.assertEqual(out["signals"]["delivery_gap"]["missing_series"], ["rig"])
        c = out["classes"]["rtx4090-24"]
        self.assertEqual(c["weeks_returned"], 4)
        self.assertEqual(c["link4"][-1]["week"], c["latest_week"])
        bands = c["implied_gross_usd_per_gpu_year"]["bands"]
        self.assertEqual(set(bands), {"40%", "60%", "80%"})
        self.assertEqual(bands["80%"], ledger.implied_revenue(c["link4"][-1]["median"], 0.8))
        self.assertIn("listed, not paid", c["implied_gross_usd_per_gpu_year"]["arithmetic"])
        self.assertIn("not a signal", c["flat_tolerance"]["note"])
        self.assertEqual(out["provenance"]["corrections"], 0)

    def test_class_filter_and_preview_name_unknown_classes(self):
        out = ledger.feed(self.doc, weeks=2, classes=["l40s-48", "nope"])
        self.assertEqual(list(out["classes"]), ["l40s-48"])
        prev = ledger.preview(self.doc, 2, ["l40s-48", "nope"])
        self.assertEqual(prev["unknown_classes"], ["nope"])
        self.assertEqual(prev["signals_computed"], 0)
        self.assertNotIn("median", str(prev))   # the preview carries no figure

    def test_window_never_exceeds_what_exists(self):
        out = ledger.feed(self.doc, weeks=ledger.MAX_WEEKS)
        for cid, c in out["classes"].items():
            self.assertLessEqual(c["weeks_returned"], len(self.doc["classes"][cid]["weeks"]), cid)


if __name__ == "__main__":
    unittest.main()
