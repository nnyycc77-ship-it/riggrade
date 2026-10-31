"""Synthetic SDN-shaped lines: the addresses are made up, not real entries."""

import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path

from riggrade import payment, screenlist, serve

A1 = "0x" + "aa" * 20
A2 = "0x" + "bB" * 20
SDN = ('36,"EXAMPLE, Person","individual","CYBER2",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,'
       '"Digital Currency Address - ETH %s; alt. Digital Currency Address - XBT '
       '1BoatSLRHtKNngkdXEeobR76b53LETtpyT; alt. Digital Currency Address - USDT %s."\n'
       '37,"EXAMPLE TWO","entity","CYBER2",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,'
       '"Digital Currency Address - USDC %s; Secondary sanctions risk: see section 1."\n'
       '38,"NO CRYPTO","entity","SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"Website example.org."\n'
       % (A1, A2, A1.upper().replace("0X", "0x")))
NOW = dt.datetime(2026, 11, 1, 2, 0, tzinfo=dt.timezone.utc)


class ScreenListTests(unittest.TestCase):
    def test_extracts_evm_addresses_across_tickers(self):
        found = screenlist.extract(SDN)
        self.assertEqual(found, {A1: ["ETH", "USDC"], A2.lower(): ["USDT"]})

    def test_build_has_provenance_and_feeds_the_screen(self):
        text, count = screenlist.build(SDN.encode(), now=NOW)
        self.assertEqual(count, 2)
        self.assertIn("# source: " + screenlist.SOURCE, text)
        self.assertIn("# built: 2026-11-01T02:00:00Z", text)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blocked.txt"
            path.write_text(text)
            screen = payment.AddressListScreen(path)
            self.assertIsNone(screen.error)
            self.assertFalse(screen(A2.upper().replace("0X", "0x"))[0])
            self.assertTrue(screen("0x" + "12" * 20)[0])

    def test_refuses_a_file_with_no_addresses(self):
        with self.assertRaises(ValueError):
            screenlist.build(b'38,"NO CRYPTO","entity"\n')


class ServeTests(unittest.TestCase):
    def call(self, app, path, method="GET", host="riggrade.example", proto=None):
        env = {"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_LENGTH": "0",
               "wsgi.input": io.BytesIO(b""), "HTTP_HOST": host, "wsgi.url_scheme": "http"}
        if proto:
            env["HTTP_X_FORWARDED_PROTO"] = proto
        got = {}
        out = b"".join(app(env, lambda s, h: got.update(status=s)))
        return got["status"], json.loads(out)

    def test_empty_environment_serves_dark(self):
        app = serve.build({})
        self.assertIsNone(app.adapter)
        status, doc = self.call(app, "/riggrade/v1")
        self.assertEqual(status, "200 OK")
        self.assertFalse(doc["sale"]["on_sale"])
        self.assertIn("no payment adapter", doc["sale"]["reasons_off"])

    def test_adapter_is_built_only_with_both_settings(self):
        app = serve.build({"RIGGRADE_FACILITATORS": "https://f.example",
                           "RIGGRADE_SCREEN_LIST": "/nonexistent"})
        self.assertIsInstance(app.adapter, payment.FacilitatorAdapter)
        # an adapter alone does not open the sale
        self.assertFalse(app.on_sale)

    def test_resource_is_an_absolute_url(self):
        from riggrade.endpoint import _url
        env = {"HTTP_HOST": "riggrade.example", "wsgi.url_scheme": "http",
               "HTTP_X_FORWARDED_PROTO": "https"}
        self.assertEqual(_url(env, "/riggrade/v1/session/x/grade"),
                         "https://riggrade.example/riggrade/v1/session/x/grade")


if __name__ == "__main__":
    unittest.main()
