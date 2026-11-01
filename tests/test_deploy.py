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
        self.assertIn("# input: " + screenlist.SOURCE, text)
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


XML = ('<?xml version="1.0"?><Sanctions><ReferenceValueSets><FeatureTypeValues>'
       '<FeatureType ID="344" FeatureTypeGroupID="1">Digital Currency Address - XBT</FeatureType>'
       '<FeatureType ID="345" FeatureTypeGroupID="1">Digital Currency Address - ETH</FeatureType>'
       '<FeatureType ID="364" FeatureTypeGroupID="1">D-U-N-S Number</FeatureType>'
       '</FeatureTypeValues></ReferenceValueSets>'
       '<Feature ID="1" FeatureTypeID="345"><FeatureVersion ID="2"><VersionDetail DetailTypeID="1432">'
       + A1 + '</VersionDetail></FeatureVersion></Feature>'
       '<Feature ID="3" FeatureTypeID="345"><FeatureVersion ID="4"><VersionDetail DetailTypeID="1432">'
       '0x' + 'cc' * 20 + '</VersionDetail></FeatureVersion></Feature>'
       '<Feature ID="5" FeatureTypeID="364"><FeatureVersion ID="6"><VersionDetail DetailTypeID="1432">'
       '0x' + 'dd' * 20 + '</VersionDetail></FeatureVersion></Feature>'
       '<Feature ID="7" FeatureTypeID="344"><FeatureVersion ID="8"><VersionDetail DetailTypeID="1432">'
       '1BoatSLRHtKNngkdXEeobR76b53LETtpyT</VersionDetail></FeatureVersion></Feature>'
       '</Sanctions>')


class ScreenListXmlTests(unittest.TestCase):
    def test_xml_reads_only_digital_currency_features(self):
        found = screenlist.extract_xml(XML)
        self.assertEqual(found, {A1: ["ETH"], "0x" + "cc" * 20: ["ETH"]})

    def test_xml_and_csv_union_with_both_inputs_recorded(self):
        text, count = screenlist.build(XML.encode(), now=NOW, more=[SDN.encode()])
        # XML has A1 and cc..; the CSV adds A2, which the XML here lacks
        self.assertEqual(count, 3)
        self.assertIn("# input: " + screenlist.SOURCE_XML, text)
        self.assertIn("# input: " + screenlist.SOURCE, text)
        self.assertIn("ETH,USDC", text)  # A1 listed under ETH in XML and USDC in CSV


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
