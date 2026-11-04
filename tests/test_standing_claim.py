"""Standing operator claim (spec 13, owner decision 4 Nov 2026): one 0 USDC
authorisation per rig identity stands in for the per-session claim on that
rig's free reference sessions, for up to 30 days."""

import io
import json

from riggrade import salelog
from tests.test_endpoint import OPS, Harness, call

RIG = "rg_" + "1" * 16
OTHER = "rg_" + "2" * 16


def call_token(app, path, token):
    environ = {"REQUEST_METHOD": "POST", "PATH_INFO": path, "CONTENT_LENGTH": "0",
               "wsgi.input": io.BytesIO(b""), "HTTP_RIGGRADE_CLAIM_TOKEN": token}
    got = {}

    def start_response(status, headers):
        got["status"] = status
    out = b"".join(app(environ, start_response))
    return got["status"], json.loads(out)


class StandingClaimTests(Harness):
    def token(self, app, rig=RIG, days=30, payer=OPS[0]):
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/standing-claim",
                               {"rig_id": rig, "days": days}, payment="pay:" + payer)
        self.assertEqual(status, "200 OK", doc)
        return doc

    def test_claim_once_then_free_reference_grade_with_the_token(self):
        app = self.app()
        status, _h, doc = call(app, "POST", "/riggrade/v1/host/standing-claim", {"rig_id": RIG})
        self.assertEqual((status, doc["accepts"][0]["amount"]), ("402 Payment Required", "0"))
        got = self.token(app)
        self.assertTrue(got["token"].startswith("rgc_"))
        self.assertEqual((got["operator"], got["rig_id"], got["charged"]), (OPS[0], RIG, False))
        stored = json.dumps(app.store.__dict__, default=str)
        self.assertNotIn(got["token"], stored)                  # only a hash is kept
        sid, status, _b = self.run_session(app, rig_id=RIG)
        self.assertEqual(status, "200 OK")
        calls_before = len(self.adapter.calls)
        status, doc = call_token(app, f"/riggrade/v1/session/{sid}/grade", got["token"])
        self.assertEqual(status, "200 OK", doc)
        self.assertTrue(doc["free_reference_session"])
        self.assertEqual((doc["evidence_pack"]["operator"], doc["evidence_pack"]["operator_claim"]),
                         (OPS[0], "standing"))
        self.assertEqual(len(self.adapter.calls), calls_before)  # no new authorisation needed
        self.assertEqual(app.references.basis("rtx4090-24", "inference")["operators"], 1)
        entry = salelog.read_chain(self.log)[-1]
        self.assertEqual((entry["status"], entry["claim"]), ("free_reference_session", "standing"))

    def test_token_only_works_for_its_rig_and_before_it_expires(self):
        app = self.app()
        got = self.token(app, days=1)
        sid, _s, _b = self.run_session(app, rig_id=OTHER)
        status, doc = call_token(app, f"/riggrade/v1/session/{sid}/grade", got["token"])
        self.assertEqual(status, "402 Payment Required")           # falls back to asking for a claim
        self.assertFalse(doc["charged"])
        sid, _s, _b = self.run_session(app, rig_id=RIG)
        self.clock.t += 2 * 86400
        status, _doc = call_token(app, f"/riggrade/v1/session/{sid}/grade", got["token"])
        self.assertEqual(status, "402 Payment Required")
        status, _doc = call_token(app, f"/riggrade/v1/session/{sid}/grade", "rgc_" + "0" * 48)
        self.assertEqual(status, "402 Payment Required")

    def test_operator_rules_still_bind(self):
        # Cap: at most 2 rigs per operator count toward a class reference.
        app = self.app()
        for k in range(3):
            rig = "rg_" + ("%x" % (k + 3)) * 16
            got = self.token(app, rig=rig)
            sid, _s, _b = self.run_session(app, rig_id=rig)
            status, _doc = call_token(app, f"/riggrade/v1/session/{sid}/grade", got["token"])
            self.assertEqual(status, "200 OK")
        self.assertEqual(app.references.basis("rtx4090-24", "inference")["rigs"], 2)

    def test_bad_input(self):
        app = self.app()
        for body in ({"rig_id": "rig-1"}, {"rig_id": RIG, "days": 31}, {"rig_id": RIG, "days": 0},
                     {"rig_id": RIG, "days": True}, None):
            status, _h, doc = call(app, "POST", "/riggrade/v1/host/standing-claim", body, payment="pay")
            self.assertEqual(status, "400 Bad Request", body)
            self.assertFalse(doc["charged"])
