"""Ed25519 certificate signatures: derived from the secret, published,
verifiable offline, and the HMAC check unchanged for older certificates."""

import io
import json
import tempfile
import unittest
from pathlib import Path

from riggrade import acceptance, certificate_page, endpoint, signing, verify

SECRET = b"s"

needs_crypto = unittest.skipUnless(signing.available(), "cryptography not installed")


def issued(secret=SECRET, signer=None):
    cert = certificate_page.sample()
    cert.pop("signature", None)
    cert["paying_wallet"] = "0x" + "ab" * 20
    cert["keys_url"] = "https://grade.example/.well-known/riggrade-keys.json"
    cert["signature"] = acceptance.sign(cert, secret)
    acceptance.attach_ed25519(cert, signer if signer is not None else signing.Signer.from_secret(secret))
    return cert


@needs_crypto
class SignerTests(unittest.TestCase):
    def test_deterministic_from_the_secret_and_distinct_per_secret(self):
        a, b, c = (signing.Signer.from_secret(s) for s in (b"one", b"one", b"two"))
        self.assertEqual(a.public_key_b64, b.public_key_b64)
        self.assertEqual(a.kid, b.kid)
        self.assertNotEqual(a.public_key_b64, c.public_key_b64)
        self.assertEqual(len(a.kid), 16)

    def test_roundtrip_and_tamper(self):
        s = signing.Signer.from_secret(SECRET)
        sig = s.sign(b"body")
        self.assertTrue(signing.verify(s.public_key_b64, b"body", sig))
        self.assertFalse(signing.verify(s.public_key_b64, b"body2", sig))
        self.assertFalse(signing.verify(s.public_key_b64, b"body", "bm90IGEgc2ln"))
        self.assertFalse(signing.verify("", b"body", sig))

    def test_no_secret_means_no_signer(self):
        self.assertIsNone(signing.Signer.from_secret(b""))

    def test_keys_document_lists_current_and_prior(self):
        s = signing.Signer.from_secret(SECRET)
        prior = signing.Signer.from_secret(b"old").public_key_b64
        doc = signing.keys_document(s, [prior, ""])
        self.assertEqual([k["status"] for k in doc["keys"]], ["current", "prior"])
        self.assertEqual(doc["keys"][0]["kid"], s.kid)
        self.assertEqual(doc["keys"][1]["kid"], signing.kid_of(prior))
        self.assertIn("except 'signature' and 'ed25519'", doc["canonicalisation"])
        self.assertEqual(signing.keys_document(None)["keys"], [])


@needs_crypto
class CertificateTests(unittest.TestCase):
    def test_certificate_verifies_both_ways_and_fails_when_changed(self):
        cert = issued()
        self.assertTrue(acceptance.verify(cert, SECRET))
        self.assertTrue(acceptance.verify_ed25519(cert))
        self.assertEqual(cert["keys_url"], "https://grade.example/.well-known/riggrade-keys.json")
        self.assertFalse(acceptance.verify_ed25519(dict(cert, keys_url="https://evil.example/keys")))
        self.assertEqual(cert["ed25519"]["kid"], signing.Signer.from_secret(SECRET).kid)
        changed = dict(cert, verdict="accept")
        self.assertFalse(acceptance.verify(changed, SECRET))
        self.assertFalse(acceptance.verify_ed25519(changed))
        # The two signatures are independent of each other.
        no_hmac = {k: v for k, v in cert.items() if k != "signature"}
        self.assertTrue(acceptance.verify_ed25519(no_hmac))

    def test_older_hmac_only_certificates_still_verify(self):
        cert = certificate_page.sample()
        cert["signature"] = acceptance.sign(cert, SECRET)
        self.assertTrue(acceptance.verify(cert, SECRET))
        self.assertIsNone(acceptance.verify_ed25519(cert))

    def test_verify_with_a_different_key_fails(self):
        cert = issued()
        other = signing.Signer.from_secret(b"other").public_key_b64
        self.assertFalse(acceptance.verify_ed25519(cert, other))


@needs_crypto
class RouteTests(unittest.TestCase):
    def call(self, app, path, accept="application/json"):
        status, headers = [], []
        environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "HTTP_ACCEPT": accept,
                   "HTTP_HOST": "grade.example", "wsgi.url_scheme": "https", "wsgi.input": io.BytesIO(b"")}
        out = b"".join(app(environ, lambda s, h: (status.append(s), headers.extend(h))))
        return status[0], dict(headers), out

    def test_keys_document_lookup_and_page(self):
        app = endpoint.GradingApp({"RIGGRADE_SECRET": "s"}, lambda: 1_800_000_000)
        cert = issued(signer=app.signer)
        app.store.put("cert:" + cert["certificate_id"], cert)
        status, headers, out = self.call(app, "/.well-known/riggrade-keys.json")
        self.assertTrue(status.startswith("200"))
        keys = json.loads(out)
        self.assertEqual(keys["keys"][0]["kid"], app.signer.kid)
        self.assertEqual(headers["Cache-Control"], "public, max-age=3600")
        path = endpoint.PREFIX + "/cluster/acceptance/" + cert["certificate_id"]
        status, _h, out = self.call(app, path)
        doc = json.loads(out)
        self.assertTrue(doc["signature_valid"])
        self.assertTrue(doc["ed25519_valid"])
        self.assertEqual(doc["public_keys_url"], "https://grade.example/.well-known/riggrade-keys.json")
        status, _h, out = self.call(app, path, accept="text/html")
        self.assertIn(b"valid: Ed25519 signature by key", out)
        self.assertIn(b"/.well-known/riggrade-keys.json", out)

    def test_prior_keys_env_is_published(self):
        prior = signing.Signer.from_secret(b"old").public_key_b64
        app = endpoint.GradingApp({"RIGGRADE_SECRET": "s", "RIGGRADE_PRIOR_PUBLIC_KEYS": prior + ", "},
                                  lambda: 1_800_000_000)
        _s, _h, out = self.call(app, "/.well-known/riggrade-keys.json")
        self.assertEqual([k["status"] for k in json.loads(out)["keys"]], ["current", "prior"])

    def test_page_for_an_older_certificate_says_none(self):
        app = endpoint.GradingApp({"RIGGRADE_SECRET": "s"}, lambda: 1_800_000_000)
        cert = certificate_page.sample()
        cert["signature"] = acceptance.sign(cert, b"s")
        app.store.put("cert:" + cert["certificate_id"], cert)
        _s, _h, out = self.call(app, endpoint.PREFIX + "/cluster/acceptance/" + cert["certificate_id"], "text/html")
        self.assertIn(b"predates independently verifiable signatures", out)


@needs_crypto
class VerifyCliTests(unittest.TestCase):
    def test_anchored_unanchored_tampered_and_wrong_key_set(self):
        cert = issued()
        keys = signing.keys_document(signing.Signer.from_secret(SECRET))
        with tempfile.TemporaryDirectory() as t:
            cpath, kpath = Path(t) / "c.json", Path(t) / "k.json"
            cpath.write_text(json.dumps({"certificate": cert, "signature_valid": True}))
            kpath.write_text(json.dumps(keys))
            # Anchored to a saved key set: 0.
            self.assertEqual(verify.main([str(cpath), "--keys", str(kpath)]), 0)
            # Anchored by fetching the certificate's keys_url: 0.
            fetched = []
            self.assertEqual(verify.main([str(cpath)], fetch=lambda url: (fetched.append(url), keys)[1]), 0)
            self.assertEqual(fetched, ["https://grade.example/.well-known/riggrade-keys.json"])
            # Offline with no key set: valid but unanchored, exit 2, and it says so.
            self.assertEqual(verify.main([str(cpath), "--offline"]), 2)
            # A fetch that fails falls back to unanchored, exit 2.
            self.assertEqual(verify.main([str(cpath)], fetch=lambda url: (_ for _ in ()).throw(OSError("down"))), 2)
            # Tampered: 1.
            cert["nodes"] = 99
            cpath.write_text(json.dumps(cert))
            self.assertEqual(verify.main([str(cpath), "--keys", str(kpath)]), 1)
            # A valid signature under a key that is not in the published set: 1.
            other = signing.keys_document(signing.Signer.from_secret(b"other"))
            kpath.write_text(json.dumps(other))
            cpath.write_text(json.dumps(issued()))
            self.assertEqual(verify.main([str(cpath), "--keys", str(kpath)]), 1)
            # HMAC-only certificate: 1.
            cpath.write_text(json.dumps(certificate_page.sample()))
            self.assertEqual(verify.main([str(cpath), "--offline"]), 1)

    def test_check_reports_anchoring(self):
        cert = issued()
        keys = signing.keys_document(signing.Signer.from_secret(SECRET))
        ok, anchored, lines = verify.check(cert, keys)
        self.assertEqual((ok, anchored), (True, True))
        ok, anchored, lines = verify.check(cert)
        self.assertEqual((ok, anchored), (True, False))
        self.assertTrue(any(ln.startswith("UNANCHORED") for ln in lines))


if __name__ == "__main__":
    unittest.main()


@needs_crypto
class McpVerifyTests(unittest.TestCase):
    def test_tool_verifies_and_rejects(self):
        from riggrade import mcp
        cert = issued()
        res = mcp.call_tool("verify_certificate", {"certificate": {"certificate": cert, "signature_valid": True}})
        out = json.loads(res["content"][0]["text"])
        self.assertTrue(out["valid"])
        self.assertFalse(out["anchored"])
        self.assertIn("only that the certificate matches its own embedded key", out["proves"])
        self.assertEqual(out["certificate_id"], cert["certificate_id"])
        good = signing.keys_document(signing.Signer.from_secret(SECRET))
        out = json.loads(mcp.call_tool("verify_certificate", {"certificate": cert, "keys_document": good})["content"][0]["text"])
        self.assertEqual((out["valid"], out["anchored"]), (True, True))
        self.assertIn("RigGrade issued", out["proves"])
        keys = signing.keys_document(signing.Signer.from_secret(b"other"))
        out = json.loads(mcp.call_tool("verify_certificate", {"certificate": cert, "keys_document": keys})["content"][0]["text"])
        self.assertFalse(out["valid"])
        self.assertTrue(mcp.call_tool("verify_certificate", {"certificate": "nope"})["isError"])
