import unittest

from riggrade import grading as g
from riggrade.classes import identify
from riggrade.label import build_label, badge_text, evidence_tag, lint, STATEMENT


class ClassTests(unittest.TestCase):
    def test_real_driver_names(self):
        self.assertEqual(identify("NVIDIA H100 80GB HBM3", 79.6), "h100-sxm-80")
        self.assertEqual(identify("NVIDIA H100 PCIe", 79.6), "h100-pcie-80")
        self.assertEqual(identify("NVIDIA A100-SXM4-80GB", 79.2), "a100-sxm-80")
        self.assertEqual(identify("NVIDIA A100-PCIE-40GB", 39.4), "a100-pcie-40")
        self.assertEqual(identify("NVIDIA A100 80GB PCIe", 79.2), "a100-pcie-80")
        self.assertEqual(identify("NVIDIA GeForce RTX 4090", 23.6), "rtx4090-24")
        self.assertEqual(identify("NVIDIA H200", 139.8), "h200-sxm-141")

    def test_unknown_and_wrong_memory(self):
        self.assertIsNone(identify("NVIDIA H200 NVL", 139.8))
        self.assertIsNone(identify("NVIDIA GeForce RTX 4090", 48))  # modded card
        self.assertIsNone(identify("Tesla T4", 15))


class GateTests(unittest.TestCase):
    OK = {"memory_integrity": True, "gemm": True, "memory_bandwidth": True,
          "host_link": True, "card_link": True}

    def test_pass_and_memory_floor(self):
        self.assertEqual(g.gate("inference", self.OK, 23.0, 1), (True, []))
        self.assertEqual(g.gate("inference", self.OK, 15.0, 1), (False, ["memory"]))
        self.assertEqual(g.gate("training", self.OK, 12.5, 1), (True, []))
        self.assertEqual(g.gate("training", self.OK, 11.0, 1), (False, ["memory"]))

    def test_card_link_only_for_multi_card_training(self):
        results = dict(self.OK, card_link=False)
        self.assertTrue(g.gate("training", results, 40, 1)[0])
        self.assertEqual(g.gate("training", results, 40, 2), (False, ["card_link"]))
        self.assertTrue(g.gate("inference", results, 40, 2)[0])


class SpeedTests(unittest.TestCase):
    def test_award_bars(self):
        self.assertEqual(g.speed_grade(0.96), (1, False))
        self.assertEqual(g.speed_grade(0.90), (2, False))
        self.assertEqual(g.speed_grade(0.70), (3, False))
        self.assertEqual(g.speed_grade(None), (g.INSUFFICIENT, False))

    def test_keep_buffer(self):
        self.assertEqual(g.speed_grade(0.93, held=1), (1, False))
        self.assertEqual(g.speed_grade(0.91, held=1), (2, True))
        self.assertEqual(g.speed_grade(0.83, held=2), (2, False))
        self.assertEqual(g.speed_grade(0.80, held=2), (3, True))
        self.assertEqual(g.speed_grade(0.97, held=2), (1, False))


class ConsistencyTests(unittest.TestCase):
    def test_worst_measure_caps(self):
        grade, m = g.consistency_grade([100, 101, 99], 0.02, p99_p50=2.5)
        self.assertEqual(grade, 2)
        self.assertIn("p99_p50", m)

    def test_needs_three_passes_and_decay(self):
        self.assertEqual(g.consistency_grade([100, 101], 0.02)[0], g.INSUFFICIENT)
        self.assertEqual(g.consistency_grade([100, 101, 99], None)[0], g.INSUFFICIENT)

    def test_isolation(self):
        self.assertEqual(g.consistency_grade([100, 100, 100], 0.0,
                                             isolation_drop=0.20)[0], 3)


class ReliabilityTests(unittest.TestCase):
    END = 100 * 86400

    def beats(self, gaps=()):
        start = self.END - 30 * 86400
        ts = list(range(start, self.END + 1, 300))
        for a, b in gaps:
            ts = [t for t in ts if not a < t < b]
        return ts

    def test_clean_chain(self):
        grade, hours = g.reliability_grade(self.beats(), self.END)
        self.assertEqual(grade, 1)
        self.assertAlmostEqual(hours, 0.0)

    def test_outage_counts(self):
        start = self.END - 10 * 86400
        grade, hours = g.reliability_grade(self.beats([(start, start + 5 * 3600)]), self.END)
        self.assertEqual(grade, 2)
        self.assertAlmostEqual(hours, 5 - 300 / 3600, places=2)

    def test_short_coverage_is_insufficient(self):
        ts = list(range(self.END - 10 * 86400, self.END, 300))
        self.assertEqual(g.reliability_grade(ts, self.END)[0], g.INSUFFICIENT)


class OverallTests(unittest.TestCase):
    def test_weakest_pillar_caps(self):
        self.assertEqual(g.overall({"speed": 1, "consistency": 2}, "point"),
                         (2, "consistency", "point"))

    def test_insufficient_speed(self):
        self.assertEqual(g.overall({"speed": g.INSUFFICIENT, "consistency": 1},
                                   "point")[0], g.INSUFFICIENT)

    def test_full_falls_back_to_point_without_reliability(self):
        self.assertEqual(g.overall({"speed": 1, "consistency": 1,
                                    "reliability": g.INSUFFICIENT}, "full"),
                         (1, "speed", "point"))

    def test_full_uses_reliability(self):
        self.assertEqual(g.overall({"speed": 1, "consistency": 1,
                                    "reliability": 3}, "full"),
                         (3, "reliability", "full"))

    def test_top_grade_rule(self):
        self.assertEqual(g.top_grade_rule(1, "speed", False, False), (2, "evidence"))
        self.assertEqual(g.top_grade_rule(1, "speed", True, False), (1, "speed"))
        self.assertEqual(g.top_grade_rule(1, "speed", False, True), (1, "speed"))
        self.assertEqual(g.top_grade_rule(2, "speed", False, False), (2, "speed"))

    def test_recheck_tolerance(self):
        self.assertTrue(g.recheck_matches(0.96, 1, 0.94, 1))
        self.assertFalse(g.recheck_matches(0.96, 1, 0.92, 1))
        self.assertFalse(g.recheck_matches(0.96, 1, 0.96, 2))


class ReferenceTests(unittest.TestCase):
    def test_five_distinct_rigs_and_one_per_rig(self):
        book = g.ReferenceBook()
        for _ in range(10):
            book.add("rtx4090-24", "inference", "rig-a", 999.0)
        self.assertEqual(book.reference("rtx4090-24", "inference"), (None, True))
        for i, v in enumerate([100, 110, 120, 130]):
            book.add("rtx4090-24", "inference", "rig-%d" % i, v)
        self.assertEqual(book.reference("rtx4090-24", "inference"), (120, True))

    def test_attested_rigs_take_priority(self):
        book = g.ReferenceBook()
        for i in range(5):
            book.add("h100-sxm-80", "inference", "un-%d" % i, 50.0)
        self.assertEqual(book.reference("h100-sxm-80", "inference")[0], 50.0)
        for i in range(5):
            book.add("h100-sxm-80", "inference", "at-%d" % i, 100.0, attested=True)
        self.assertEqual(book.reference("h100-sxm-80", "inference")[0], 100.0)
        self.assertEqual(book.basis("h100-sxm-80", "inference")["attested_rigs"], 5)
        self.assertIn("attested rigs only", book.basis("h100-sxm-80", "inference")["basis"])

    def test_basis_before_five_rigs(self):
        book = g.ReferenceBook()
        book.add("rtx4090-24", "training", "r1", 10.0)
        info = book.basis("rtx4090-24", "training")
        self.assertEqual((info["rigs"], info["provisional"]), (1, True))
        self.assertIn("speed not graded", info["basis"])

    def test_controlled_replaces_provisional(self):
        book = g.ReferenceBook()
        book.set_controlled("h100-sxm-80", "training", 5000.0)
        self.assertEqual(book.reference("h100-sxm-80", "training"), (5000.0, False))


class IdentityTests(unittest.TestCase):
    def test_off_envelope(self):
        env = {"mem_bw_gbs": 3000.0, "gemm_tflops": 700.0}
        self.assertEqual(g.identity_mismatch("h100-sxm-80",
                                             {"mem_bw_gbs": 2000.0, "gemm_tflops": 690.0},
                                             env), ["mem_bw_gbs"])
        self.assertEqual(g.identity_mismatch("h100-sxm-80", {}, env), [])


class LabelTests(unittest.TestCase):
    def test_statement_and_badge_are_promise_free(self):
        label = build_label("rg_1", "rtx4090-24", "inference", 2, "point",
                            "consistency", {"speed": 1, "consistency": 2},
                            "assigned", evidence_tag("buyer", False, provisional=True),
                            {"from": 0, "to": 1})
        text = badge_text(label, "2 Nov 2026")
        self.assertEqual(text, "Grade 2 · RTX 4090 class · Inference · point grade, single session · "
                               "capped by Consistency · declared buyer run, unattested · "
                               "provisional bars · 2 Nov 2026")
        self.assertEqual(lint([STATEMENT, text]), [])
        self.assertEqual(label["pillars"]["reliability"], g.NOT_MEASURED)
        self.assertEqual(label["evidence_basis"], "single session")

    def test_lint_catches_promise_words(self):
        self.assertEqual(lint(["Guaranteed uptime!", "certified rig"]),
                         ["certified", "guaranteed"])


if __name__ == "__main__":
    unittest.main()
