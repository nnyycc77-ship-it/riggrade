"""Paid products beyond the grade: the host fix-it report and the cluster
acceptance certificate (owner decision, 1 Nov 2026)."""

import json
import tempfile
import unittest
from pathlib import Path

from riggrade import acceptance, cluster_rules, fixit, salelog
from riggrade.endpoint import GradingApp
from tests.test_endpoint import FakeAdapter, IMAGE, PAYER, WALLET, call

REPORT = {"preflight": True, "verdict": "fail", "gpus": [{"index": 0, "name": "NVIDIA RTX 4090"}],
          "findings": [
              {"check": "gpu0.foreign_memory", "status": "pass", "detail": "0.00 GiB in use before this check started"},
              {"check": "gpu0.clocks_under_load", "status": "warn",
               "detail": "median SM clock 62% of max under load; held by a power limit the host "
                         "lowered to 150 W (card default 450 W)"},
              {"check": "gpu0.slowdown_reasons", "status": "fail",
               "detail": "bad reasons active: hw_thermal_slowdown; all reasons seen: hw_thermal_slowdown, sw_power_cap"},
              {"check": "gpu0.pcie_link", "status": "warn",
               "detail": "Gen3 x8 under load (card supports Gen4 x16)"},
              {"check": "download_rate", "status": "skipped", "detail": "not measured"},
          ]}
CLEAN = {"preflight": True, "verdict": "pass", "findings": [
    {"check": "gpu0.foreign_memory", "status": "pass", "detail": "0.00 GiB in use"}]}


def node(rank, tflops=80.0, image="sha256:" + "ab" * 32, verdict="pass", fabric=None, storage=None):
    n = {"rank": rank, "identity": {"card_count": 8, "device_name": "NVIDIA H100 80GB HBM3"},
         "image": image, "preflight": {"verdict": verdict},
         "tflops_per_gpu": {str(i): tflops for i in range(8)}}
    if fabric is not None:
        n["fabric"] = {"busbw_gbs": fabric}
    if storage is not None:
        n["storage_read_gbs"] = storage
    return n


def pack_for(claims, nodes):
    checks, result = cluster_rules.evaluate(claims, nodes)
    pack = {"method": "cluster-acceptance-v0-draft", "claims": claims, "nodes": nodes,
            "checks": checks, "images": sorted({n["image"] for n in nodes})}
    pack.update(result)
    return pack


CLAIMS = {"nodes": 2, "gpus_per_node": 8, "gpu_model": "NVIDIA H100 80GB HBM3",
          "inter_node_gbps_per_gpu": 400, "storage_read_gbs": 10}


class FixitRulesTests(unittest.TestCase):
    def test_preview_counts_only_warn_and_fail(self):
        prev = fixit.preview(REPORT)
        self.assertEqual((prev["fixable"], prev["fail"], prev["warn"]), (3, 1, 2))
        self.assertEqual(prev["not_measured"], ["download_rate"])
        self.assertEqual(fixit.preview(CLEAN)["fixable"], 0)

    def test_diagnosis_reads_the_numbers_out_of_the_details(self):
        doc = fixit.diagnose(REPORT)
        by = {i["check"]: i for i in doc["items"]}
        clocks = by["gpu0.clocks_under_load"]
        self.assertIn("150 W", clocks["cause"])
        self.assertIn("nvidia-smi -pl 450", clocks["fix"][0])
        self.assertEqual((clocks["who"], clocks["confidence"], clocks["threatens"]),
                         ("host", "high", "speed"))
        self.assertIn("thermal", by["gpu0.slowdown_reasons"]["cause"])
        self.assertIn("Gen3 x8", by["gpu0.pcie_link"]["cause"])
        self.assertEqual(doc["by_who"], {"host": 3})
        self.assertIn("not a promise", doc["statement"])

    def test_unknown_check_gets_a_low_confidence_item(self):
        doc = fixit.diagnose({"preflight": True, "findings": [
            {"check": "gpu0.new_thing", "status": "fail", "detail": "x"}]})
        self.assertEqual(doc["items"][0]["confidence"], "low")


class AcceptanceRulesTests(unittest.TestCase):
    def test_pack_recomputes_and_certificate_verifies(self):
        nodes = [node(0, fabric=45.0, storage=6.0), node(1, storage=6.0)]
        pack = pack_for(CLAIMS, nodes)
        self.assertIsNone(acceptance.validate(pack))
        checks, result = acceptance.recompute(pack)
        self.assertEqual(result["verdict"], pack["verdict"])
        cert = acceptance.certificate(pack, checks, result, b"s3cret", 1_800_000_000, "rc_1")
        self.assertTrue(acceptance.verify(cert, b"s3cret"))
        self.assertFalse(acceptance.verify(cert, b"other"))
        cert["verdict"] = "reject"  # any edit breaks the signature
        self.assertFalse(acceptance.verify(cert, b"s3cret"))

    def test_validate_names_what_is_missing(self):
        self.assertIn("claims", acceptance.validate({"nodes": [{}], "checks": [], "verdict": "accept"}))
        self.assertIn("nodes", acceptance.validate({"claims": {}, "nodes": [], "checks": [], "verdict": "x"}))


class ProductEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = str(Path(self.tmp.name) / "sales.jsonl")
        self.adapter = FakeAdapter()

    def app(self, products="fixit,cluster", enabled="1"):
        env = {"RIGGRADE_ENABLED": enabled, "PAYOUT_WALLET_ADDRESS": WALLET,
               "RIGGRADE_SALE_LOG": self.log, "RIGGRADE_SECRET": "s",
               "RIGGRADE_IMAGE_DIGESTS": IMAGE, "RIGGRADE_PRODUCTS": products}
        return GradingApp(env, lambda: 1_800_000_000, adapter=self.adapter)

    def test_products_default_off_with_reasons_and_no_charge(self):
        app = self.app(products="")
        status, _h, doc = call(app, "POST", "/riggrade/v1/fixit", {"preflight_report": REPORT})
        self.assertEqual(status, "503 Service Unavailable")
        self.assertIn("RIGGRADE_PRODUCTS does not include fixit", doc["reasons_off"])
        self.assertEqual(doc["preview"]["fixable"], 3)
        self.assertEqual(self.adapter.calls, [])
        _s, _h, desc = call(app, "GET", "/riggrade/v1")
        self.assertFalse(desc["products"]["fixit"]["on_sale"])
        self.assertEqual(desc["products"]["cluster"]["price_usdc"], "25")

    def test_fixit_402_then_paid_report_and_sale_log(self):
        app = self.app()
        status, _h, doc = call(app, "POST", "/riggrade/v1/fixit", {"preflight_report": REPORT})
        self.assertEqual(status, "402 Payment Required")
        req = doc["accepts"][0]
        self.assertEqual(req["maxAmountRequired"], "2000000")
        self.assertTrue(req["resource"].endswith("/riggrade/v1/fixit"))
        self.assertIn("preflight_report", doc["extensions"]["bazaar"]["schema"]["properties"])
        self.assertEqual(self.adapter.calls, [])
        status, headers, doc = call(app, "POST", "/riggrade/v1/fixit",
                                    {"preflight_report": REPORT}, payment="pay")
        self.assertEqual(status, "200 OK")
        self.assertEqual(len(doc["report"]["items"]), 3)
        self.assertEqual(doc["report"]["paying_wallet"], PAYER)
        self.assertEqual(headers["X-PAYMENT-RESPONSE"], "eyJ0eCI6ICJvayJ9")
        (entry,) = salelog.read_chain(self.log)
        self.assertEqual((entry["product"], entry["amount_atomic"], entry["findings"]),
                         ("riggrade_fixit_report", "2000000", 3))

    def test_fixit_nothing_to_fix_is_not_sold(self):
        app = self.app()
        status, _h, doc = call(app, "POST", "/riggrade/v1/fixit",
                               {"preflight_report": CLEAN}, payment="pay")
        self.assertEqual(status, "422 Unprocessable Entity")
        self.assertEqual(self.adapter.calls, [])
        status, _h, _d = call(app, "POST", "/riggrade/v1/fixit", {"preflight_report": {"x": 1}})
        self.assertEqual(status, "400 Bad Request")

    def test_cluster_certificate_is_issued_stored_and_verifiable(self):
        app = self.app()
        pack = pack_for(CLAIMS, [node(0, fabric=45.0, storage=6.0), node(1, storage=6.0)])
        status, _h, doc = call(app, "POST", "/riggrade/v1/cluster/acceptance", {"evidence_pack": pack})
        self.assertEqual(status, "402 Payment Required")
        self.assertEqual(doc["accepts"][0]["maxAmountRequired"], "25000000")
        self.assertEqual(doc["preview"]["verdict"], pack["verdict"])
        status, _h, doc = call(app, "POST", "/riggrade/v1/cluster/acceptance",
                               {"evidence_pack": pack}, payment="pay")
        self.assertEqual(status, "200 OK")
        cert = doc["certificate"]
        self.assertEqual((cert["verdict"], cert["nodes"], cert["paying_wallet"]),
                         (pack["verdict"], 2, PAYER))
        self.assertTrue(acceptance.verify(cert, b"s"))
        self.assertTrue(doc["verify_url"].endswith("/riggrade/v1/cluster/acceptance/" + cert["certificate_id"]))
        status, _h, got = call(app, "GET", "/riggrade/v1/cluster/acceptance/" + cert["certificate_id"])
        self.assertEqual(status, "200 OK")
        self.assertTrue(got["signature_valid"])
        self.assertEqual(got["certificate"]["pack_sha256"], cert["pack_sha256"])
        status, _h, _d = call(app, "GET", "/riggrade/v1/cluster/acceptance/rc_nope")
        self.assertEqual(status, "404 Not Found")
        (entry,) = salelog.read_chain(self.log)
        self.assertEqual((entry["product"], entry["amount_atomic"], entry["certificate_id"]),
                         ("riggrade_cluster_acceptance", "25000000", cert["certificate_id"]))

    def test_tampered_pack_is_refused_before_payment(self):
        app = self.app()
        pack = pack_for(CLAIMS, [node(0, fabric=45.0, storage=6.0), node(1, tflops=40.0, storage=6.0)])
        self.assertEqual(pack["verdict"], "reject")
        pack["verdict"] = "accept"
        status, _h, doc = call(app, "POST", "/riggrade/v1/cluster/acceptance",
                               {"evidence_pack": pack}, payment="pay")
        self.assertEqual(status, "422 Unprocessable Entity")
        self.assertIn("does not recompute", doc["error"])
        self.assertEqual(self.adapter.calls, [])

    def test_sale_switch_off_gates_products_too(self):
        app = self.app(enabled="0")
        status, _h, doc = call(app, "POST", "/riggrade/v1/fixit", {"preflight_report": REPORT})
        self.assertEqual(status, "503 Service Unavailable")
        self.assertIn("RIGGRADE_ENABLED is not 1", doc["reasons_off"])
