"""The browser checkout page: same routes, no price or payout address of its own."""
import re
import tempfile
import unittest
from pathlib import Path

from riggrade import acceptance, checkout
from riggrade.endpoint import GradingApp
from tests.test_endpoint import IMAGE, WALLET, FakeAdapter, call


class CheckoutPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = str(Path(self.tmp.name) / "sales.jsonl")

    def app(self):
        env = {"RIGGRADE_ENABLED": "1", "PAYOUT_WALLET_ADDRESS": WALLET, "RIGGRADE_SALE_LOG": self.log,
               "RIGGRADE_SECRET": "s", "RIGGRADE_IMAGE_DIGESTS": IMAGE, "RIGGRADE_PRODUCTS": "fixit,cluster"}
        return GradingApp(env, lambda: 1_800_000_000, adapter=FakeAdapter())

    def page(self):
        got = {}
        app = self.app()
        environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/riggrade/v1/checkout"}

        def start(status, headers):
            got["status"], got["headers"] = status, dict(headers)
        body = b"".join(app(environ, start)).decode()
        return got["status"], got["headers"], body

    def test_page_is_served_with_a_nonce_policy(self):
        status, headers, body = self.page()
        self.assertEqual(status, "200 OK")
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        nonce = re.search(r"script-src 'nonce-([^']+)'", headers["Content-Security-Policy"]).group(1)
        self.assertIn(f'<script nonce="{nonce}">', body)
        self.assertNotIn("<script src", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_page_holds_no_price_or_payout_address(self):
        _s, _h, body = self.page()
        self.assertNotIn(WALLET.lower(), body.lower())
        self.assertNotIn("2000000", body)
        self.assertNotIn("25000000", body)
        self.assertIn('"path": "/fixit"', body)
        self.assertIn('"path": "/cluster/acceptance"', body)
        self.assertNotIn("__", re.sub(r"__init__", "", body))  # every placeholder filled

    def test_grade_sessions_can_be_claimed_or_paid_from_the_page(self):
        # Owner direction, 3 Nov 2026: grade sessions on the io.net sample rentals. The operator claim
        # (spec 13) is a 0 USDC authorisation from the operator's own wallet; the page takes a session id.
        _s, _h, body = self.page()
        self.assertIn('<option value="grade">', body)
        self.assertIn('"path": "/grade"', body)
        self.assertIn('id=sid', body)
        self.assertIn("free_reference_session", body)
        self.assertIn("0 USDC operator claim", body)

    def test_page_names_every_payer_role_the_endpoint_accepts(self):
        self.assertEqual(checkout.ROLES, acceptance.PAYER_ROLES)

    def test_descriptor_and_head_point_at_it(self):
        _s, _h, desc = call(self.app(), "GET", "/riggrade/v1")
        self.assertEqual(desc["checkout"], "/riggrade/v1/checkout")
        got = {}
        body = self.app()({"REQUEST_METHOD": "HEAD", "PATH_INFO": "/riggrade/v1/checkout"},
                          lambda s, h: got.update(status=s))
        self.assertEqual((got["status"], b"".join(body)), ("200 OK", b""))


if __name__ == "__main__":
    unittest.main()


class LandingPageTests(unittest.TestCase):
    def app(self, **extra):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {"RIGGRADE_ENABLED": "1", "PAYOUT_WALLET_ADDRESS": WALLET,
               "RIGGRADE_SALE_LOG": str(Path(tmp.name) / "sales.jsonl"), "RIGGRADE_SECRET": "s",
               "RIGGRADE_IMAGE_DIGESTS": IMAGE, "RIGGRADE_PRODUCTS": "fixit,cluster"}
        env.update(extra)
        return GradingApp(env, lambda: 1_793_700_000, adapter=FakeAdapter())

    def get(self, app, accept):
        got = {}
        environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/"}
        if accept:
            environ["HTTP_ACCEPT"] = accept
        body = b"".join(app(environ, lambda s, h: got.update(status=s, headers=dict(h)))).decode()
        return got, body

    def test_browsers_get_the_page_and_agents_keep_the_json(self):
        got, body = self.get(self.app(), "text/html,application/xhtml+xml,*/*;q=0.8")
        self.assertTrue(got["headers"]["Content-Type"].startswith("text/html"))
        self.assertIn("pip install riggrade", body)
        self.assertIn('href="/riggrade/v1/checkout"', body)
        self.assertNotIn(WALLET.lower(), body.lower())
        got, body = self.get(self.app(), None)
        self.assertEqual(got["headers"]["Content-Type"], "application/json")
        self.assertIn('"descriptor"', body)

    def test_prices_come_from_the_live_configuration(self):
        _g, body = self.get(self.app(), "text/html")
        self.assertIn("2 USDC", body)
        self.assertIn("25 USDC", body)
        _g, body = self.get(self.app(RIGGRADE_TRIAL_PRODUCTS="fixit", RIGGRADE_TRIAL_UNTIL="2026-12-31"), "text/html")
        self.assertIn("free until 2026-12-31 (UTC), then 2 USDC", body)
        _g, body = self.get(self.app(RIGGRADE_PRODUCTS=""), "text/html")
        self.assertNotIn("then 2 USDC", body)
        self.assertIn("not on sale right now", body)
