"""Facilitator responses here are synthetic, shaped like the x402 v2
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
NETWORK = "eip155:8453"
REQ = {"scheme": "exact", "network": NETWORK, "amount": "3000000",
       "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "payTo": PAY_TO,
       "maxTimeoutSeconds": 300, "extra": {"name": "USD Coin", "version": "2"}}


def header(frm=PAYER, to=PAY_TO, value="3000000", network=NETWORK, scheme="exact", version=2,
           accepted_to=PAY_TO):
    body = {"x402Version": version,
            "resource": {"url": "/riggrade/v1/session/s/grade", "description": "d",
                         "mimeType": "application/json"},
            "accepted": dict(REQ, scheme=scheme, network=network, payTo=accepted_to),
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
OK_SETTLE = {"success": True, "transaction": "0x" + "ef" * 32, "network": NETWORK, "payer": PAYER}
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
                 "network mismatch": header(network="eip155:84532"),
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

    def test_bazaar_outcome_is_recorded(self):
        # The paying client did not echo the extension: nothing to catalogue.
        post = Post({A + "/verify": OK_VERIFY, A + "/settle": OK_SETTLE})
        res = pay.FacilitatorAdapter([A], allow, post=post).settle(header(), REQ)
        self.assertEqual(res["bazaar"], {"echoed_by_client": False, "facilitator_outcome": None})
        # Echoed, and the facilitator reports the cataloguing outcome in its sidechannel.
        h = header()
        payload = json.loads(base64.b64decode(h))
        payload["extensions"] = {"bazaar": {"info": {"input": {"type": "http", "method": "POST"}}}}
        h2 = base64.b64encode(json.dumps(payload).encode()).decode()
        settle = dict(OK_SETTLE, extensions={"bazaar": {"status": "processing"}})
        post = Post({A + "/verify": OK_VERIFY, A + "/settle": settle})
        res = pay.FacilitatorAdapter([A], allow, post=post).settle(h2, REQ)
        self.assertEqual(res["bazaar"], {"echoed_by_client": True, "facilitator_outcome": {"status": "processing"}})
        self.assertEqual(pay.bazaar_outcome({}, {"extensions": {"bazaar": {"status": "rejected", "rejectedReason": "x"}}}, {}),
                         {"echoed_by_client": False, "facilitator_outcome": {"status": "rejected", "rejectedReason": "x"}})

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


class CdpAuthTests(unittest.TestCase):
    """The Coinbase facilitator's bearer JWT, shaped as cdp-sdk signs it."""

    def setUp(self):
        try:
            from cryptography.hazmat.primitives.asymmetric import ed25519
            from cryptography.hazmat.primitives import serialization
        except ImportError:  # pragma: no cover
            self.skipTest("cryptography not installed")
        self.ed25519, self.serialization = ed25519, serialization
        key = ed25519.Ed25519PrivateKey.generate()
        seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption())
        pub = key.public_key().public_bytes(serialization.Encoding.Raw,
                                            serialization.PublicFormat.Raw)
        # The portal's secret: base64 of seed + public key (64 bytes).
        self.secret = base64.b64encode(seed + pub).decode()
        self.public = key.public_key()
        self.key_id = "3cc94088-a825-422c-91a2-616893f23202"

    def _decode(self, part):
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))

    def test_token_shape_claims_and_signature(self):
        auth = pay.CdpAuth(self.key_id, self.secret, now=lambda: 1_800_000_000)
        token = auth.token("POST", "/platform/v2/x402/verify")
        h, c, sig = token.split(".")
        header, claims = self._decode(h), self._decode(c)
        self.assertEqual((header["alg"], header["kid"], header["typ"]), ("EdDSA", self.key_id, "JWT"))
        self.assertRegex(header["nonce"], r"^[0-9]{16}$")
        self.assertEqual(claims["sub"], self.key_id)
        self.assertEqual(claims["iss"], "cdp")
        self.assertEqual((claims["nbf"], claims["exp"]), (1_800_000_000, 1_800_000_120))
        self.assertEqual(claims["uris"], ["POST api.cdp.coinbase.com/platform/v2/x402/verify"])
        self.assertNotIn("aud", claims)
        raw_sig = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        self.public.verify(raw_sig, (h + "." + c).encode())  # raises if wrong
        # A fresh token differs (nonce), and a 32-byte seed secret is accepted too.
        self.assertNotEqual(auth.token("POST", "/platform/v2/x402/verify"), token)
        seed_only = base64.b64encode(base64.b64decode(self.secret)[:32]).decode()
        pay.CdpAuth(self.key_id, seed_only)
        with self.assertRaises(ValueError):
            pay.CdpAuth(self.key_id, base64.b64encode(b"short").decode())

    def test_portal_key_file_is_accepted_as_the_secret(self):
        # The CDP portal downloads {"id": ..., "privateKey": ...}; uploading
        # that file as the secret value works with no copying of the key.
        keyfile = json.dumps({"id": self.key_id, "privateKey": self.secret, "name": "riggrade-cdp-facilitator"})
        auth = pay.CdpAuth("", keyfile)
        self.assertEqual(auth.key_id, self.key_id)
        claims = self._decode(auth.token("POST", "/platform/v2/x402/settle").split(".")[1])
        self.assertEqual(claims["sub"], self.key_id)
        self.assertEqual(claims["uris"], ["POST api.cdp.coinbase.com/platform/v2/x402/settle"])
        # An explicit key ID wins over the file's; a file without the key is refused.
        self.assertEqual(pay.CdpAuth("other-id", keyfile).key_id, "other-id")
        with self.assertRaises(ValueError):
            pay.CdpAuth("", json.dumps({"id": self.key_id}))
        with tempfile.TemporaryDirectory() as tmp:
            screen = Path(tmp) / "blocked.txt"
            screen.write_text("# none\n")
            env = {"RIGGRADE_FACILITATORS": pay.CDP_FACILITATOR + "," + A,
                   "RIGGRADE_SCREEN_LIST": str(screen), "RIGGRADE_CDP_KEY_SECRET": keyfile}
            adapter = pay.from_env(env)  # no RIGGRADE_CDP_KEY_ID needed
            self.assertEqual(adapter.facilitators, [pay.CDP_FACILITATOR, A])
            self.assertEqual(adapter.auth[pay.CDP_FACILITATOR].key_id, self.key_id)

    def test_adapter_signs_only_the_cdp_facilitator(self):
        seen = []

        def post(url, body, headers=None):
            seen.append((url, headers))
            if url.endswith("/verify"):
                return OK_VERIFY
            return OK_SETTLE
        auth = pay.CdpAuth(self.key_id, self.secret)
        cdp = pay.CDP_FACILITATOR
        adapter = pay.FacilitatorAdapter([cdp, A], allow, post=post, auth={cdp: auth})
        res = adapter.settle(header(), REQ)
        self.assertTrue(res["ok"])
        self.assertEqual([u for u, _ in seen], [cdp + "/verify", cdp + "/settle"])
        for url, hdrs in seen:
            self.assertTrue(hdrs["Authorization"].startswith("Bearer "))
            claims = self._decode(hdrs["Authorization"].split(".")[1])
            self.assertEqual(claims["uris"], ["POST " + url.split("https://", 1)[1]])
        # The keyless facilitator gets no headers argument at all.
        seen.clear()
        adapter = pay.FacilitatorAdapter([A], allow, post=lambda url, body: OK_VERIFY if url.endswith("/verify") else OK_SETTLE, auth={cdp: auth})
        self.assertTrue(adapter.settle(header(), REQ)["ok"])

    def test_from_env_drops_the_cdp_facilitator_without_its_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen = Path(tmp) / "blocked.txt"
            screen.write_text("# none\n")
            env = {"RIGGRADE_FACILITATORS": pay.CDP_FACILITATOR + "," + A,
                   "RIGGRADE_SCREEN_LIST": str(screen)}
            adapter = pay.from_env(env)
            self.assertEqual(adapter.facilitators, [A])
            self.assertEqual(adapter.auth, {})
            env.update({"RIGGRADE_CDP_KEY_ID": self.key_id, "RIGGRADE_CDP_KEY_SECRET": self.secret})
            adapter = pay.from_env(env)
            self.assertEqual(adapter.facilitators, [pay.CDP_FACILITATOR, A])
            self.assertIn(pay.CDP_FACILITATOR, adapter.auth)
            env["RIGGRADE_CDP_KEY_SECRET"] = "not base64!!"
            self.assertEqual(pay.from_env(env).facilitators, [A])
            # CDP alone with no key: no adapter, so the sale stays off.
            self.assertIsNone(pay.from_env({"RIGGRADE_FACILITATORS": pay.CDP_FACILITATOR,
                                            "RIGGRADE_SCREEN_LIST": str(screen)}))


class DescriptorFacilitatorTests(unittest.TestCase):
    def test_descriptor_names_the_facilitator_hosts_only(self):
        import io
        from riggrade.endpoint import GradingApp
        with tempfile.TemporaryDirectory() as tmp:
            screen = Path(tmp) / "blocked.txt"
            screen.write_text("# none\n")
            adapter = pay.from_env({"RIGGRADE_FACILITATORS": pay.CDP_FACILITATOR + "," + A,
                                    "RIGGRADE_SCREEN_LIST": str(screen)})
            app = GradingApp({"RIGGRADE_ENABLED": "1", "PAYOUT_WALLET_ADDRESS": PAY_TO,
                              "RIGGRADE_SALE_LOG": tmp + "/sales.jsonl", "RIGGRADE_SECRET": "s",
                              "RIGGRADE_IMAGE_DIGESTS": "sha256:" + "ab" * 32},
                             lambda: 1_800_000_000, adapter=adapter)
            environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/riggrade/v1", "CONTENT_LENGTH": "0",
                       "wsgi.input": io.BytesIO(b"")}
            out = b"".join(app(environ, lambda s, h: None))
            doc = json.loads(out)
            # No CDP key: the Coinbase entry was dropped; the host list says so.
            self.assertEqual(doc["sale"]["facilitators"], ["a.example"])
            self.assertNotIn("key", json.dumps(doc["sale"]).lower())
