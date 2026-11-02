"""The ledger's weekly note: posture on the page, arithmetic labelled,
signals reported as no-signal with the missing series named, the flat
tolerance as the method states it."""

import unittest

from pricewatch import ledger_note as ln
from riggrade import index


class NoteTests(unittest.TestCase):
    def test_posture_arithmetic_and_no_signal(self):
        doc = index.load_packaged()
        lines = ln.build(doc, "2026-11-02")
        text = "\n".join(lines)
        for sentence in ln.POSTURE:
            self.assertEqual(text.count(sentence), 2, sentence)   # read this first, read this last
        self.assertIn("rtx4090-24", text)
        self.assertIn("band 80%", text)
        self.assertIn("no signal (missing series: chips, buildout, sponsors, secondary)", text)
        self.assertIn("no signal (missing series: rig", text)
        self.assertIn("Listed, not paid", text)
        self.assertNotIn("recommend", text.lower())
        self.assertNotIn("forecast " , text.lower().replace("not a forecast", ""))

    def test_implied_revenue_is_plain_arithmetic(self):
        self.assertEqual(ln.implied_revenue(0.60, 0.8), round(0.60 * 8760 * 0.8))

    def test_flat_tolerance(self):
        self.assertEqual(ln.is_flat([1.0, 1.02, 1.03])[0], True)
        self.assertEqual(ln.is_flat([1.0, 1.08, 1.0])[0], True)      # inside the 10% band, end within 5%
        self.assertEqual(ln.is_flat([1.0, 1.12, 1.0])[0], False)     # band breach
        self.assertEqual(ln.is_flat([1.0, 1.0, 1.06])[0], False)     # end move over 5%
        self.assertEqual(ln.is_flat([1.0])[0], None)

    def test_flat_reading_prints_the_breach_week_and_figure(self):
        r = ln.flat_reading([("w1", 84.0), ("w2", 90.0), ("w3", 102.0), ("w4", 83.0)])
        self.assertEqual((r["flat"], r["band_breach"]), (False, True))
        self.assertEqual((r["widest_departure_week"], r["widest_departure_value"]), ("w3", 102.0))
        self.assertIn("102 in week w3 is +21.4% from the start", r["detail"])
        self.assertIn("end move -1.2%", r["detail"])
        r = ln.flat_reading([("w1", 1.0), ("w2", 1.04), ("w3", 1.02)])
        self.assertEqual((r["flat"], r["band_breach"]), (True, False))
        self.assertIn("no week outside the 10% band", r["detail"])

    def test_page_prints_the_flat_rule_and_the_reconstructed_label(self):
        text = "\n".join(ln.build(index.load_packaged(), "2026-11-02"))
        self.assertIn("Flat rule: " + ln.FLAT_RULE, text)
        self.assertIn("## Link 4, marketplace (reconstructed series)", text)
        self.assertIn("Series: reconstructed, from GetDeploying", text)
        self.assertIn(ln.INDEX_METHOD_CITATION, text)
        self.assertIn("in week 2026-", text)    # every breach label shows its week


if __name__ == "__main__":
    unittest.main()
