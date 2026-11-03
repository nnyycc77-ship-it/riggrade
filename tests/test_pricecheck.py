"""The 0.01 USDC price check (owner decision, 3 Nov 2026): placing, routes, listing."""
import base64
import json
import tempfile
import unittest
from pathlib import Path

from riggrade import discovery, endpoint, pricecheck, salelog
from riggrade.endpoint import GradingApp
from tests.test_endpoint import IMAGE, WALLET, FakeAdapter, call

NOW = 1_800_000_000
WEEKS = {"2026-10-12": {"median_price": 2.6, "provider_median_price": 2.7, "min_price": 1.5, "max_price": 6.0,
                        "provider_count": 30, "offering_count": 70},
         "2026-10-19": {"median_price": 2.5, "provider_median_price": 2.6, "min_price": 1.4, "max_price": 6.2,
                        "provider_count": 31, "offering_count": 72},
         "2026-10-26": {"median_price": 2.4, "provider_median_price": 2.5, "min_price": 1.3, "max_price": 5.9,
                        "provider_count": 32, "offering_count": 75}}
DOC = {"classes": {"h100-sxm-80": {"latest_week": "2026-10-26", "source_name": "H100 SXM", "weeks": WEEKS},
                   "a10-24": {"latest_week": None, "weeks": {}}},
       "source": {"title": "GetDeploying", "licence": "CC BY 4.0", "attribution": "GetDeploying"},
       "method_url": "https://example/method"}


class PlacingTests(unittest.TestCase):
    def test_positions(self):
        week = WEEKS["2026-10-26"]
        cases = {1.0: "below the listed minimum", 2.0: "between the listed minimum and the median",
                 2.45: "at the median (within 5%)", 4.0: "between the median and the listed maximum",
                 7.0: "above the listed maximum"}
        for price, want in cases.items():
            self.assertEqual(pricecheck.position(price, week), want, price)

    def test_check_reads_the_latest_week_and_states_its_posture(self):
        out = pricecheck.check(DOC, "h100-sxm-80", 3.0)
        self.assertEqual(out["latest_week"], "2026-10-26")
        self.assertEqual(out["week"]["usd_per_gpu_hour_median"], 2.4)
        self.assertEqual(out["vs_median_pct"], 25.0)
        self.assertEqual([w["week"] for w in out["recent_weeks"]], sorted(WEEKS))
        self.assertEqual(out["recent_median_range"], [2.4, 2.6])
        self.assertEqual(out["read_this_first"], list(pricecheck.POSTURE))
        text = json.dumps(out).lower()
        for word in ("vast", "runpod", "cheapest", "you should"):
            self.assertNotIn(word, text)

    def test_bad_input_and_preview(self):
        for body in (None, {}, {"peer_class": "h100-sxm-80"}, {"peer_class": "x", "usd_per_gpu_hour": 0},
                     {"peer_class": "x", "usd_per_gpu_hour": True}, {"peer_class": "x", "usd_per_gpu_hour": 5000}):
            with self.assertRaises(pricecheck.BadRequest):
                pricecheck.parse(body)
        self.assertEqual(pricecheck.preview(DOC, "a10-24")["covered"], False)
        self.assertEqual(pricecheck.preview(DOC, "a10-24")["known"], ["h100-sxm-80"])
        self.assertNotIn("week", pricecheck.preview(DOC, "h100-sxm-80"))  # no figures before payment

    def test_discovery_price_matches_the_atomic_price(self):
        atomic = endpoint.PRODUCT_PRICE_ATOMIC["pricecheck"]
        self.assertEqual(atomic, 10_000)
        self.assertEqual("%.2f" % (atomic / 1e6), discovery.PRICES_USD["pricecheck"]["amount"])


class RouteTests(unittest.TestCase):
    def app(self, products="pricecheck", adapter=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = str(Path(tmp.name) / "sales.jsonl")
        env = {"RIGGRADE_ENABLED": "1", "PAYOUT_WALLET_ADDRESS": WALLET, "RIGGRADE_IMAGE_DIGESTS": IMAGE,
               "RIGGRADE_SALE_LOG": self.log, "RIGGRADE_SECRET": "s", "RIGGRADE_PRODUCTS": products}
        self.adapter = adapter or FakeAdapter()
        app = GradingApp(env, lambda: NOW, adapter=self.adapter)
        app.index.doc, app.index.url = DOC, None
        return app

    def test_off_until_named_in_products_and_unlisted_while_off(self):
        app = self.app(products="fixit")
        status, _h, out = call(app, "POST", "/riggrade/v1/price/check",
                               {"peer_class": "h100-sxm-80", "usd_per_gpu_hour": 2.0})
        self.assertEqual(status, "503 Service Unavailable")
        self.assertFalse(out["charged"])
        _s, _h, doc = call(app, "GET", "/openapi.json")
        self.assertNotIn("/price/check", doc["paths"])
        _s, _h, card = call(app, "GET", "/.well-known/agent.json")
        self.assertNotIn("price_check", {s["id"] for s in card["skills"]})
        self.assertEqual(self.adapter.calls, [])

    def test_probe_answers_402_for_one_cent(self):
        app = self.app()
        status, headers, _out = call(app, "GET", "/riggrade/v1/price/check")
        self.assertEqual(status, "402 Payment Required")
        req = json.loads(base64.b64decode(headers["PAYMENT-REQUIRED"]))
        self.assertEqual(req["accepts"][0]["amount"], "10000")
        self.assertTrue(req["resource"]["url"].endswith("/riggrade/v1/price/check"))
        self.assertIn("bazaar", req["extensions"])
        _s, _h, doc = call(app, "GET", "/openapi.json")
        self.assertEqual(doc["paths"]["/price/check"]["post"]["x-payment-info"]["price"]["amount"], "0.01")

    def test_paid_call_delivers_and_logs_the_sale(self):
        app = self.app()
        body = {"peer_class": "h100-sxm-80", "usd_per_gpu_hour": 3.0}
        status, _h, out = call(app, "POST", "/riggrade/v1/price/check", body)
        self.assertEqual(status, "402 Payment Required")
        self.assertEqual(out["preview"]["latest_week"], "2026-10-26")
        status, headers, out = call(app, "POST", "/riggrade/v1/price/check", body, payment="pay")
        self.assertEqual(status, "200 OK")
        self.assertEqual(out["position"], "between the median and the listed maximum")
        self.assertIn("PAYMENT-RESPONSE", headers)
        rows = salelog.read_chain(self.log)
        sale = [r for r in rows if r.get("product") == pricecheck.PRODUCT]
        self.assertEqual(len(sale), 1)
        self.assertEqual((sale[0]["amount_atomic"], sale[0]["peer_class"]), ("10000", "h100-sxm-80"))

    def test_uncovered_class_is_refused_before_any_charge(self):
        app = self.app()
        status, _h, out = call(app, "POST", "/riggrade/v1/price/check",
                               {"peer_class": "a10-24", "usd_per_gpu_hour": 1.0}, payment="pay")
        self.assertEqual(status, "422 Unprocessable Entity")
        self.assertFalse(out["charged"])
        self.assertEqual(self.adapter.calls, [])

    def test_bad_body_with_payment_is_400_and_never_charged(self):
        app = self.app()
        status, _h, out = call(app, "POST", "/riggrade/v1/price/check", {"peer_class": "h100-sxm-80"},
                               payment="pay")
        self.assertEqual(status, "400 Bad Request")
        self.assertFalse(out["charged"])
        self.assertEqual(self.adapter.calls, [])


if __name__ == "__main__":
    unittest.main()
