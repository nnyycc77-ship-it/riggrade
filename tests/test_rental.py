"""The rental check loop v0: plan, keep rules, card history, the routes."""
import json
import tempfile
import unittest
from pathlib import Path

from riggrade import rental
from riggrade.endpoint import GradingApp
from tests.test_endpoint import IMAGE, WALLET, FakeAdapter, call

NOW = 1_800_000_000
CARD = "gc_" + "a1" * 8


def report(findings, cards=(CARD,), name="NVIDIA GeForce RTX 4090"):
    return {"preflight": True, "verdict": "pass", "gpus": [{"index": 0, "name": name}],
            "cards": [{"card_id": c} for c in cards],
            "findings": [{"check": c, "status": s, "detail": c + " detail"} for c, s in findings]}


CLEAN = [("gpu0.foreign_memory", "pass"), ("gpu0.foreign_compute", "pass"), ("gpu0.clocks_under_load", "pass"),
         ("gpu0.slowdown_reasons", "pass"), ("gpu0.temperature", "pass"), ("driver_vs_cuda", "pass"),
         ("gpu0.pcie_link", "pass"), ("gpu0.ecc_errors", "skipped"), ("download_rate", "pass"),
         ("disk_write", "pass"), ("host.memory", "pass")]


def swap(findings, check, status):
    return [(c, status if c == check else s) for c, s in findings]


class PlanTests(unittest.TestCase):
    def test_workload_defaults_and_named_needs(self):
        p = rental.plan({"workload": "training", "needs": ["ecc"], "gpus": {"peer_class": "h100-sxm-80"}})
        self.assertIn("pcie", p["requirements"])
        self.assertIn("ecc", p["requirements"])
        self.assertIn("foreign_memory", p["requirements"])
        ecc = next(f for f in p["filters"] if f["need"] == "ecc")
        self.assertEqual(ecc["confirmed_by"], ["ecc_errors"])
        self.assertEqual(p["preflight"]["run"], "riggrade-preflight")
        text = json.dumps(p).lower()
        for word in ("vast", "runpod", "usdc", "price:"):
            self.assertNotIn(word, text)  # no marketplace, no price

    def test_bad_input_is_refused(self):
        with self.assertRaises(rental.BadRequest):
            rental.plan({"workload": "mining"})
        with self.assertRaises(rental.BadRequest):
            rental.plan({"workload": "inference", "needs": ["teleport"]})


class CheckTests(unittest.TestCase):
    def verdict(self, findings, plan=None, rows=(), **kw):
        body = {"preflight_report": report(findings, **kw)}
        if plan is not None:
            body["plan"] = plan
        return rental.check(body, list(rows), NOW)

    def test_clean_box_is_kept_and_nothing_is_stored(self):
        out = self.verdict(CLEAN)
        self.assertEqual(out["verdict"], "keep")
        self.assertFalse(out["recorded"])
        self.assertIn("marketplace's own terms", out["advice"])
        self.assertTrue(out["history"][CARD]["first_sighting"])

    def test_a_required_fail_cancels_and_an_unrequired_fail_warns(self):
        plan = rental.plan({"workload": "inference"})
        out = self.verdict(swap(CLEAN, "gpu0.slowdown_reasons", "fail"), plan)
        self.assertEqual(out["verdict"], "cancel")
        out = self.verdict(swap(CLEAN, "disk_write", "fail"), plan)  # disk not asked for
        self.assertEqual(out["verdict"], "keep with warnings")
        out = self.verdict(swap(CLEAN, "gpu0.pcie_link", "warn"), plan)
        self.assertEqual(out["verdict"], "keep with warnings")

    def test_ecc_asked_for_but_not_measured_cancels(self):
        plan = rental.plan({"workload": "inference", "needs": ["ecc"]})
        out = self.verdict(CLEAN, plan)
        self.assertEqual(out["verdict"], "cancel")
        self.assertIn("not measured", out["reasons"][0]["rule"])
        self.assertEqual(self.verdict(CLEAN, rental.plan({"workload": "inference"}))["verdict"], "keep")

    def test_ecc_asked_for_and_absent_from_the_report_cancels(self):
        plan = rental.plan({"workload": "inference", "needs": ["ecc"]})
        no_ecc = [(c, st) for c, st in CLEAN if not c.endswith("ecc_errors")]
        out = self.verdict(no_ecc, plan)
        self.assertEqual(out["verdict"], "cancel")
        self.assertEqual(out["reasons"][0]["check"], "ecc_errors")
        self.assertEqual(self.verdict(no_ecc, rental.plan({"workload": "inference"}))["verdict"], "keep")

    def test_wrong_peer_class_cancels(self):
        plan = rental.plan({"workload": "inference", "gpus": {"peer_class": "h100-sxm-80"}})
        out = self.verdict(CLEAN, plan)
        self.assertEqual(out["verdict"], "cancel")
        self.assertEqual(out["reasons"][0]["check"], "peer_class")

    def test_same_failure_in_recent_history_cancels_with_evidence(self):
        entry = {"at": NOW - 86400, "cards": [{"card_id": CARD}], "evidence_tag": "declared:buyer attestation:unattested",
                 "attested": False, "statuses": {"gpu0.temperature": "fail"}}
        failing = swap(CLEAN, "gpu0.temperature", "warn")
        # a warn now with a past fail is not "failing again"
        self.assertEqual(self.verdict(failing, rows=[entry])["verdict"], "keep with warnings")
        old = dict(entry, at=NOW - 40 * 86400)
        out = self.verdict(swap(CLEAN, "gpu0.temperature", "fail"), plan={"requirements": []}, rows=[entry, old])
        reason = next(r for r in out["reasons"] if r.get("card_id") == CARD)
        self.assertEqual(out["verdict"], "cancel")
        self.assertEqual(reason["evidence_tag"], "declared:buyer attestation:unattested")
        self.assertIn("not proof", reason["detail"])
        self.assertEqual(len(out["history"][CARD]["recent_failures"]), 1)  # the 40-day-old one is outside

    def test_not_a_report_is_refused(self):
        with self.assertRaises(rental.BadRequest):
            rental.check({"preflight_report": {"x": 1}}, [], NOW)


class RouteTests(unittest.TestCase):
    def test_routes_are_free_and_refuse_bad_input(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {"RIGGRADE_ENABLED": "1", "PAYOUT_WALLET_ADDRESS": WALLET, "RIGGRADE_IMAGE_DIGESTS": IMAGE,
               "RIGGRADE_SALE_LOG": str(Path(tmp.name) / "s.jsonl"), "RIGGRADE_SECRET": "s"}
        adapter = FakeAdapter()
        app = GradingApp(env, lambda: NOW, adapter=adapter)
        status, _h, p = call(app, "POST", "/riggrade/v1/rental/plan", {"workload": "inference"})
        self.assertEqual(status, "200 OK")
        status, _h, out = call(app, "POST", "/riggrade/v1/rental/check",
                               {"preflight_report": report(CLEAN), "plan": p})
        self.assertEqual((status, out["verdict"]), ("200 OK", "keep"))
        status, _h, _err = call(app, "POST", "/riggrade/v1/rental/plan", {"workload": "x"})
        self.assertEqual(status, "400 Bad Request")
        self.assertEqual(adapter.calls, [])  # never a payment


if __name__ == "__main__":
    unittest.main()
