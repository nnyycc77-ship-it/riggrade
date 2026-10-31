"""Facilitator responses here are synthetic, shaped like the x402 v1
facilitator specification; no real facilitator is called."""

import base64
import json
import tempfile
import unittest
from pathlib import Path

from riggrade import payment as pay

PAY_TO = "0x" + "12" * 20
PAYER = "0x" + "34" * 20
BLOCKED = "0x" + "66" * 20
REQ = {"scheme": "exact", "network": "base", "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
       "payTo": PAY_TO, "maxAmountRequired": "3000000", "resource": "/riggrade/v1/session/s/grade",
       "description": "d", "mimeType": "application/json", "maxTimeoutSeconds": 300,
       "extra": {"name": "USD Coin", "version": "2"}}


def header(frm=PAYER, to=PAY_TO, value="3000000", network="base", scheme="exact"):
    body = {"x402Version": 1, "scheme": scheme, "network": network,
            "payload": {"signature": "0x" + "ab" * 65,
                        "authorization": {"from": frm, "to": to, "value": value,
                                          "validAfter": "0", "validBefore": "9999999999",
                                          "nonce": "0x" + "01" * 32}}}
    return base64.b64encode(json.dumps(body).encode()).decode()


class Post:
    """Scripted facilitator: maps URL -> response dict or exception."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def __call__(self, url, body):
        self.calls.append(url)
        out = self.script.get(url)
        if isinstance(out, Exception):
            raise out
        if out is None:
            raise pay.Unreachable("no route")
        return out


OK_VERIFY = {"isValid": True, "payer": PAYER}
OK_SETTLE = {"success": True, "transaction": "0x" + "ef" * 32, "network": "base", "payer": PAYER}
A, B = "https://a.example", "https://b.example"


def allow(_payer):
    return True, None


class AdapterTests(unittest.TestCase):
    def test_happy_path_uses_primary(self):
        post = Post({A + "/verify": OK_VERIFY, A + "/settle": OK_SETTLE})
        res = pay.FacilitatorAdapter([A, B], allow, post=post).settle(header(), REQ)
        self.assertTrue(res["ok"])
        self.assertEqual(res["tx"], OK_SETTLE["transaction"])
        self.assertEqual(res["facilitator"], A)
        self.assertEqual(json.loads(base64.b64decode(res["receipt_header"]))["success"], True)
        self.assertEqual(post.calls, [A + "/verify", A + "/settle"])

    def test_unreachable_primary_falls_back_to_backup(self):
        post = Post({A + "/verify": pay.Unreachable("timeout"),
                     B + "/verify": OK_VERIFY, B + "/settle": OK_SETTLE})
        res = pay.FacilitatorAdapter([A, B], allow, post=post).settle(header(), REQ)
        self.assertTrue(res["ok"])
        self.assertEqual(res["facilitator"], B)

    def test_invalid_payment_is_not_retried_elsewhere(self):
        post = Post({A + "/verify": {"isValid": False, "invalidReason": "insufficient_funds"},
                     B + "/verify": OK_VERIFY, B + "/settle": OK_SETTLE})
        res = pay.FacilitatorAdapter([A, B], allow, post=post).settle(header(), REQ)
        self.assertFalse(res["ok"])
        self.assertFalse(res["charged"])
        self.assertIn("insufficient_funds", res["error"])
        self.assertEqual(post.calls, [A + "/verify"])

    def test_settle_with_no_answer_is_charged_unknown_and_never_resubmitted(self):
        post = Post({A + "/verify": OK_VERIFY, A + "/settle": pay.Unreachable("reset"),
                     B + "/verify": OK_VERIFY, B + "/settle": OK_SETTLE})
        res = pay.FacilitatorAdapter([A, B], allow, post=post).settle(header(), REQ)
        self.assertFalse(res["ok"])
        self.assertEqual(res["charged"], "unknown")
        self.assertEqual(post.calls, [A + "/verify", A + "/settle"])

    def test_local_checks_refuse_before_any_network_call(self):
        post = Post({})
        ad = pay.FacilitatorAdapter([A], allow, post=post)
        cases = {"not to the payout address": header(to="0x" + "99" * 20),
                 "amount below price": header(value="2999999"),
                 "network mismatch": header(network="base-sepolia"),
                 "scheme mismatch": header(scheme="upto"),
                 "not base64": "%%%"}
        for expect, h in cases.items():
            res = ad.settle(h, REQ)
            self.assertFalse(res["ok"])
            self.assertFalse(res["charged"])
            self.assertIn(expect, res["error"])
        self.assertEqual(post.calls, [])

    def test_blocked_payer_refused_before_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blocked.txt"
            path.write_text("# list\n%s  # example\n" % BLOCKED.upper().replace("0X", "0x"))
            screen = pay.AddressListScreen(path)
            post = Post({A + "/verify": OK_VERIFY, A + "/settle": OK_SETTLE})
            res = pay.FacilitatorAdapter([A], screen, post=post).settle(header(frm=BLOCKED), REQ)
            self.assertEqual((res["ok"], res["charged"]), (False, False))
            self.assertIn("screen", res["error"])
            self.assertEqual(post.calls, [])
            self.assertTrue(pay.FacilitatorAdapter([A], screen, post=post).settle(header(), REQ)["ok"])

    def test_screen_fails_closed(self):
        screen = pay.AddressListScreen("/nonexistent/list.txt")
        self.assertFalse(screen(PAYER)[0])
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.txt"
            bad.write_text("not-an-address\n")
            self.assertFalse(pay.AddressListScreen(bad)(PAYER)[0])

    def test_settled_payer_mismatch_is_charged_but_refused(self):
        post = Post({A + "/verify": OK_VERIFY,
                     A + "/settle": dict(OK_SETTLE, payer="0x" + "77" * 20)})
        res = pay.FacilitatorAdapter([A], allow, post=post).settle(header(), REQ)
        self.assertEqual((res["ok"], res["charged"]), (False, True))

    def test_all_unreachable(self):
        res = pay.FacilitatorAdapter([A, B], allow, post=Post({})).settle(header(), REQ)
        self.assertEqual((res["ok"], res["charged"]), (False, False))
        self.assertIn("no facilitator reachable", res["error"])

    def test_from_env_needs_both_settings_and_a_screen(self):
        self.assertIsNone(pay.from_env({}))
        self.assertIsNone(pay.from_env({"RIGGRADE_FACILITATORS": A}))
        ad = pay.from_env({"RIGGRADE_FACILITATORS": A + "," + B, "RIGGRADE_SCREEN_LIST": "/x"})
        self.assertEqual(ad.facilitators, [A, B])
        with self.assertRaises(ValueError):
            pay.FacilitatorAdapter([A], None)


if __name__ == "__main__":
    unittest.main()
