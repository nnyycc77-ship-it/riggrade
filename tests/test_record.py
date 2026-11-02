"""The GPU record (docs/gpu-record-method.md draft): closed until switched
on, entries only through the explicit opt-in with the consent text, no
identifier beyond the method's, buyer-run entries held, append-only."""

import json
import tempfile
import unittest
from pathlib import Path

from riggrade import record
from riggrade.endpoint import GradingApp
from runner import preflight as pf
from tests.test_endpoint import IMAGE, WALLET, FakeAdapter, call

CARDS = [{"card_id": "gc_" + "ab" * 8, "serial": "1650000000001"}, {"card_id": "gc_" + "cd" * 8, "serial": None}]
DOC = {"record": 1, "rig_id": "rg_" + "12" * 8, "cards": CARDS, "declared_runner": "host",
       "gpu_name": "NVIDIA GeForce RTX 4090", "driver": "550.54", "gpus": 2, "memory_total_mb": 24564,
       "verdict": "pass", "statuses": {"gpu0.clocks_under_load": "pass"}, "measures": {"clock_ratio": 0.98},
       "exclusive": True, "hostname": "secret-box", "wallet": "0x" + "99" * 20}


class CleanTests(unittest.TestCase):
    def test_entry_keeps_the_method_fields_only(self):
        e = record.clean(DOC, 1_800_000_000)
        self.assertEqual(e["cards"], CARDS)
        self.assertEqual(e["evidence_tag"], "declared:host attestation:unattested")
        self.assertEqual(e["peer_class"], "rtx4090-24")
        self.assertFalse(e["held"])
        self.assertNotIn("hostname", json.dumps(e))
        self.assertNotIn("wallet", json.dumps(e))
        self.assertNotIn("secret-box", json.dumps(e))

    def test_buyer_entries_are_held_and_bad_identity_refused(self):
        self.assertTrue(record.clean(dict(DOC, declared_runner="buyer"), 1)["held"])
        for bad in (dict(DOC, record=0), dict(DOC, declared_runner="owner"), dict(DOC, rig_id="rig-1"),
                    dict(DOC, cards=[{"card_id": "GPU-1234"}]), dict(DOC, cards=[])):
            with self.assertRaises(ValueError):
                record.clean(bad, 1)
        e = record.clean(dict(DOC, cards=[{"card_id": "gc_" + "ab" * 8, "serial": "bad serial!"}]), 1)
        self.assertIsNone(e["cards"][0]["serial"])

    def test_history_is_append_only_and_serves_only_unheld(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.jsonl"
            record.append(path, record.clean(DOC, 100))
            record.append(path, record.clean(dict(DOC, verdict="warn"), 200))
            record.append(path, record.clean(dict(DOC, declared_runner="buyer"), 300))
            rows = record.read(path)
            h = record.history(rows, CARDS[0]["card_id"])
            self.assertEqual((h["records"], h["held"]), (2, 1))
            self.assertEqual([e["at"] for e in h["entries"]], [100, 200])
            self.assertIn("records, not cards owned", h["note"])
            self.assertEqual(record.counts(rows), {"rtx4090-24": {"total": "fewer than 5", "records": "fewer than 5",
                                                                  "held": "fewer than 5", "operator": "fewer than 5"}})
            for _ in range(10):
                record.append(path, record.clean(DOC, 400))
            c = record.counts(record.read(path))["rtx4090-24"]   # 13 entries: protection stage, rounded
            self.assertEqual((c["total"], c["records"], c["held"]), (15, 10, "fewer than 5"))
            for _ in range(7):
                record.append(path, record.clean(DOC, 500))
            c = record.counts(record.read(path))["rtx4090-24"]   # 20 entries: as counted, small cell protected
            self.assertEqual((c["total"], c["records"], c["held"]), (20, 19, "fewer than 5"))

    def test_operator_and_virtualised_entries_never_merge_into_the_card_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.jsonl"
            record.append(path, record.clean(DOC, 100))
            record.append(path, record.clean(dict(DOC, declared_runner="riggrade", virtualised=True), 200))
            record.append(path, record.clean(dict(DOC, virtualised=True), 300))
            h = record.history(record.read(path), CARDS[0]["card_id"])
            self.assertEqual(h["records"], 1)
            self.assertEqual([e["at"] for e in h["entries"]], [100])
            self.assertEqual(h["operator_series"]["records"], 2)
            self.assertIn("never merged", h["operator_series"]["note"])


class RunnerTests(unittest.TestCase):
    def test_record_doc_carries_identity_and_nothing_else(self):
        report = {"preflight": True, "verdict": "pass", "findings": [], "measures": {"clock_ratio": 0.97},
                  "gpus": [{"name": "NVIDIA RTX 4090", "driver": "550"}], "exclusive_box_assumed": False,
                  "rig_id": "rg_" + "12" * 8, "cards": CARDS, "errors": {"download": "host x failed"}}
        doc = pf.record_doc(report, "host")
        self.assertEqual((doc["record"], doc["declared_runner"], doc["virtualised"]), (1, "host", True))
        self.assertEqual(doc["cards"], CARDS)
        self.assertNotIn("pool", doc)
        self.assertNotIn("host x failed", json.dumps(doc))

    def test_record_requires_runner_and_prints_consent_first(self):
        with self.assertRaises(SystemExit):
            pf.main(["--record", "--skip-download", "--seconds", "0"])
        self.assertIn("explicit opt-in", record.CONSENT)
        self.assertIn("append-only", record.CONSENT)
        for words in ("board serial number", "persistent plain identifier", "public through the record lookup",
                      "kept for 24 months", "cannot be withdrawn", "a later run supersedes, nothing is deleted"):
            self.assertIn(words, record.CONSENT)                         # [C3]
        self.assertEqual(pf.RECORD_CONSENT, record.CONSENT)   # the runner prints the same words


class EndpointTests(unittest.TestCase):
    def app(self, path=None):
        env = {"RIGGRADE_ENABLED": "1", "PAYOUT_WALLET_ADDRESS": WALLET, "RIGGRADE_SECRET": "s",
               "RIGGRADE_IMAGE_DIGESTS": IMAGE}
        if path:
            env["RIGGRADE_RECORD"] = path
        return GradingApp(env, lambda: 1_800_000_000, adapter=FakeAdapter())

    def test_closed_by_default(self):
        app = self.app()
        status, _h, doc = call(app, "POST", "/riggrade/v1/record", DOC)
        self.assertEqual((status, doc["accepted"]), ("503 Service Unavailable", False))
        status, _h, _d = call(app, "GET", "/riggrade/v1/record/" + CARDS[0]["card_id"])
        self.assertEqual(status, "503 Service Unavailable")

    def test_open_record_accepts_and_serves_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = self.app(str(Path(tmp) / "record.jsonl"))
            status, _h, doc = call(app, "POST", "/riggrade/v1/record", DOC)
            self.assertEqual((status, doc["accepted"], doc["held"]), ("200 OK", True, False))
            self.assertIn("consent", doc)
            status, _h, doc = call(app, "POST", "/riggrade/v1/record", dict(DOC, declared_runner="buyer"))
            self.assertEqual((status, doc["held"]), ("200 OK", True))
            status, _h, doc = call(app, "GET", "/riggrade/v1/record/" + CARDS[0]["card_id"])
            self.assertEqual((status, doc["records"], doc["held"]), ("200 OK", 1, 1))
            self.assertEqual(doc["entries"][0]["evidence_tag"], "declared:host attestation:unattested")
            status, _h, _d = call(app, "GET", "/riggrade/v1/record/gc_" + "00" * 8)
            self.assertEqual(status, "404 Not Found")
            status, _h, _d = call(app, "POST", "/riggrade/v1/record", {"record": 1})
            self.assertEqual(status, "400 Bad Request")


if __name__ == "__main__":
    unittest.main()
