"""Host certificate and badge (docs/host-certificate-method.md): off until
priced, one per session, consent where the payer is not the host, grade
kind and full evidence tag on certificate, page and badge, supersession
only on a matching recorded rig identity."""

import base64
import json
import unittest

from riggrade import hostcert, salelog
from riggrade.endpoint import GradingApp
from tests.test_endpoint import (
    PAYER,
    FakeAdapter,
    Harness,
    _with_references,
    bundle_for,
    call,
)

HOST = "0x" + "77" * 20
OTHER = "0x" + "88" * 20


def header_from(wallet):
    body = {"x402Version": 2, "accepted": {}, "payload": {"signature": "0x00",
                                                           "authorization": {"from": wallet}}}
    return base64.b64encode(json.dumps(body).encode()).decode()


class HostCertTests(Harness):
    def app(self, priced=True, products="hostcert", adapter="default", free_until=None):
        env = dict(self.env, RIGGRADE_PRODUCTS=products)
        if free_until:
            env["RIGGRADE_HOSTCERT_PRICE_ATOMIC"] = "0"
            env["RIGGRADE_HOSTCERT_FREE_UNTIL"] = free_until
        elif priced:
            env["RIGGRADE_HOSTCERT_PRICE_ATOMIC"] = "1000000"
        self.adapter = FakeAdapter() if adapter == "default" else adapter
        return GradingApp(env, self.clock, adapter=self.adapter, references=_with_references())

    def graded(self, app, runner="host", host_wallet=PAYER, rig_id="rig-1", payment="pay"):
        _s, _h, s = call(app, "POST", "/riggrade/v1/session",
                         {"workload_classes": ["inference"], "declared_runner": runner,
                          "host_wallet": host_wallet})
        sid = s["session_id"]
        _s, _h, st = call(app, "POST", f"/riggrade/v1/session/{sid}/start")
        status, _h, _b = call(app, "POST", f"/riggrade/v1/session/{sid}/bundle",
                              bundle_for(sid, s["nonce"], st["params"], rig_id=rig_id))
        self.assertEqual(status, "200 OK")
        status, _h, doc = call(app, "POST", f"/riggrade/v1/session/{sid}/grade", payment=payment)
        self.assertEqual(status, "200 OK", doc)
        return sid, doc

    def test_off_without_a_price_and_hidden(self):
        app = self.app(priced=False)
        sid, _g = self.graded(app)
        calls = len(self.adapter.calls)      # the grade's own settlement
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/certificate", {"session_id": sid})
        self.assertEqual(status, "503 Service Unavailable")
        self.assertIn("no price is set for hostcert (RIGGRADE_HOSTCERT_PRICE_ATOMIC)", doc["reasons_off"])
        _s, _h, desc = call(app, "GET", "/riggrade/v1")
        self.assertNotIn("hostcert", desc["products"])
        status, _h, _d = call(app, "GET", "/riggrade/v1/host/certificate")
        self.assertEqual(status, "503 Service Unavailable")
        self.assertEqual(len(self.adapter.calls), calls)      # nothing settled for the certificate

    def test_host_paid_session_issues_a_certificate_once_with_the_wording(self):
        app = self.app()
        sid, grade = self.graded(app)
        self.assertEqual(grade["labels"][0]["form"], "point")
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/certificate", {"session_id": sid})
        self.assertEqual((status, doc["accepts"][0]["amount"]), ("402 Payment Required", "1000000"))
        self.assertEqual(doc["preview"]["consent"], "host_paid")
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/certificate", {"session_id": sid},
                               payment="pay")
        self.assertEqual(status, "200 OK", doc)
        cert = doc["certificate"]
        g = cert["grades"][0]
        self.assertEqual(g["grade_kind"], "point grade")                       # [261b-1]
        self.assertEqual(g["reliability"], "Reliability not measured (point grade)")
        self.assertTrue(cert["evidence_tag"].startswith("declared:host attestation:unattested"))
        self.assertEqual(cert["declared_note"], hostcert.DECLARED)
        self.assertEqual(cert["consent"], "host_paid")
        self.assertEqual(cert["rig_identity"][0], "rig-1")
        self.assertIn("later run supersedes", cert["validity"])
        self.assertEqual(cert["paying_wallet"], PAYER)
        cid = cert["certificate_id"]
        entry = [e for e in salelog.read_chain(self.log) if e["product"] == hostcert.PRODUCT]
        self.assertEqual((len(entry), entry[0]["certificate_id"]), (1, cid))
        # lookup: JSON, page, badge
        status, _h, got = call(app, "GET", f"/riggrade/v1/host/{cid}")
        self.assertTrue(got["signature_valid"])
        self.assertIsNone(got["superseded_by"])
        page = b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": f"/riggrade/v1/host/{cid}",
                             "HTTP_ACCEPT": "text/html"}, lambda s, h: None)).decode()
        self.assertIn("declared, not observed by RigGrade", page)
        self.assertIn("Reliability not measured (point grade)", page)
        self.assertIn("declared:host attestation:unattested", page)
        svg = b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": f"/riggrade/v1/host/{cid}/badge.svg"},
                           lambda s, h: None)).decode()
        self.assertIn("point grade", svg)                                     # [261b-1] on the badge
        self.assertIn("Reliability not measured (point grade)", svg)
        self.assertIn("declared:host attestation:unattested", svg)            # [261b-2] full tag on the badge
        self.assertIn(f"/riggrade/v1/host/{cid}", svg)
        self.assertNotIn("verified host", svg.lower())
        # one per session
        calls = len(self.adapter.calls)
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/certificate", {"session_id": sid},
                               payment="pay")
        self.assertEqual((status, doc["certificate_id"]), ("409 Conflict", cid))
        self.assertEqual(len(self.adapter.calls), calls)

    def test_buyer_paid_grade_needs_the_host_wallet_consent(self):          # [261b-3]
        app = self.app()
        sid, _g = self.graded(app, runner="buyer", host_wallet=HOST)
        calls = len(self.adapter.calls)
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/certificate", {"session_id": sid},
                               payment="pay")
        self.assertEqual(status, "403 Forbidden")
        self.assertIn("has not consented", doc["error"])
        self.assertEqual(len(self.adapter.calls), calls)
        # consent from the wrong wallet is refused; from the host wallet it is recorded
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/consent", {"session_id": sid},
                               payment="pay:" + OTHER)
        self.assertEqual(status, "403 Forbidden")
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/consent", {"session_id": sid})
        self.assertEqual((status, doc["accepts"][0]["amount"]), ("402 Payment Required", "0"))
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/consent", {"session_id": sid},
                               payment="pay:" + HOST)
        self.assertEqual((status, doc["consented"]), ("200 OK", True))
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/certificate", {"session_id": sid},
                               payment="pay")
        self.assertEqual(status, "200 OK", doc)
        self.assertEqual(doc["certificate"]["consent"], "consented")
        self.assertTrue(doc["certificate"]["evidence_tag"].startswith("declared:buyer"))

    def test_no_host_wallet_means_nobody_can_consent(self):
        app = self.app()
        sid, _g = self.graded(app, runner="buyer", host_wallet=None)
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/certificate", {"session_id": sid},
                               payment="pay")
        self.assertEqual(status, "403 Forbidden")
        self.assertIn("no host wallet", doc["error"])
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/consent", {"session_id": sid},
                               payment="pay:" + HOST)
        self.assertEqual(status, "409 Conflict")

    def test_certificate_must_be_paid_by_the_grade_payer(self):
        app = self.app()
        sid, _g = self.graded(app)
        calls = len(self.adapter.calls)
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/certificate", {"session_id": sid},
                               payment=header_from(OTHER))
        self.assertEqual(status, "403 Forbidden")
        self.assertIn("wallet that paid the grade", doc["error"])
        self.assertEqual(len(self.adapter.calls), calls)

    def test_supersession_only_on_matching_recorded_identity(self):          # [261b-4]
        app = self.app()
        ids = []
        for rig in ("rig-1", "rig-1", "rig-2"):
            sid, _g = self.graded(app, rig_id=rig)
            self.clock.t += 60
            status, _h, doc = call(app, "POST", "/riggrade/v1/host/certificate", {"session_id": sid},
                                   payment="pay")
            self.assertEqual(status, "200 OK", doc)
            ids.append(doc["certificate"]["certificate_id"])
        first, second, third = ids
        _s, _h, got = call(app, "GET", f"/riggrade/v1/host/{first}")
        self.assertEqual(got["superseded_by"]["certificate_id"], second)
        _s, _h, got = call(app, "GET", f"/riggrade/v1/host/{second}")
        self.assertIsNone(got["superseded_by"])      # rig-2 is a different recorded identity
        _s, _h, got = call(app, "GET", f"/riggrade/v1/host/{third}")
        self.assertIsNone(got["superseded_by"])

    def test_free_launch_window_issues_on_a_verified_zero_authorisation(self):
        app = self.app(free_until="2027-01-15")       # the clock reads 2027-01-15 04:00 UTC
        sid, _g = self.graded(app)
        _s, _h, desc = call(app, "GET", "/riggrade/v1")
        self.assertEqual((desc["products"]["hostcert"]["price_usdc"], desc["products"]["hostcert"]["free_until"]),
                         ("0", "2027-01-15"))
        self.assertTrue(desc["products"]["hostcert"]["on_sale"])
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/certificate", {"session_id": sid})
        self.assertEqual((status, doc["accepts"][0]["amount"]), ("402 Payment Required", "0"))
        self.assertIn("never settled", doc["resource"]["description"])
        before = len(self.adapter.calls)
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/certificate", {"session_id": sid},
                               payment="pay")
        self.assertEqual(status, "200 OK", doc)
        self.assertEqual(self.adapter.calls[before][0], "verify")      # verified, never settled
        self.assertEqual(len(self.adapter.calls), before + 1)
        entry = next(e for e in salelog.read_chain(self.log) if e["product"] == hostcert.PRODUCT)
        self.assertEqual((entry["amount_atomic"], entry["status"]), ("0", "free_launch"))
        # the window ends: off again, with the reason, nothing issued
        self.clock.t = 1_800_000_000 + 400 * 86400
        sid2, _g = self.graded(app, rig_id="rig-9")
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/certificate", {"session_id": sid2},
                               payment="pay")
        self.assertEqual(status, "503 Service Unavailable")
        self.assertIn("free launch window for hostcert ended on 2027-01-15", doc["reasons_off"][0])
        _s, _h, desc = call(app, "GET", "/riggrade/v1")
        self.assertNotIn("hostcert", desc["products"])

    def test_badge_text_passes_the_promise_lint(self):
        cert = {"grades": [{"grade": 2, "peer_class": "rtx4090-24", "workload_class": "inference",
                            "grade_kind": "point grade",
                            "reliability": "Reliability not measured (point grade)"}],
                "evidence_tag": "declared:host attestation:attested host-paid"}
        lines = hostcert.badge_lines(cert, "https://x/riggrade/v1/host/rh_1", "2 Nov 2026")
        self.assertEqual(lines[0], "Grade 2 · RTX 4090 class · Inference · point grade · "
                                   "Reliability not measured (point grade)")
        self.assertEqual(lines[1], "evidence: declared:host attestation:attested host-paid")


if __name__ == "__main__":
    unittest.main()
