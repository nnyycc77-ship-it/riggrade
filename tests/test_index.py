"""The free price index: importer (append-only, loud on surprises), the
packaged series, the service route, the MCP tool."""

import io
import json
import unittest

from pricewatch import reconstructed as rec
from riggrade import endpoint, index, mcp
from riggrade.classes import PEER_CLASSES

ROW = {"gpu_slug": "nvidia-rtx-4090", "date": "2026-10-19", "billing_type": "ON_DEMAND",
       "reservation_months": None, "min_price": 0.3, "max_price": 2.0, "median_price": 0.6,
       "provider_median_price": 0.5, "provider_count": 10, "offering_count": 20}


def payload(rows, licence="CC-BY-4.0"):
    return json.dumps({"meta": {"license": licence, "latest_week": "2026-10-26",
                                "last_snapshot": "2026-11-01"}, "data": rows}).encode()


class ImporterTests(unittest.TestCase):
    def test_maps_on_demand_rows_and_drops_the_rest(self):
        rows = [ROW, dict(ROW, billing_type="SPOT", median_price=0.1),
                dict(ROW, gpu_slug="nvidia-h100"), dict(ROW, gpu_slug="nvidia-h200", date="2026-10-26")]
        doc = rec.build(payload(rows), now="2026-11-01T00:00:00Z")
        self.assertEqual(doc["classes"]["rtx4090-24"]["weeks"]["2026-10-19"]["median_price"], 0.6)
        self.assertEqual(doc["classes"]["rtx4090-24"]["latest_week"], "2026-10-19")
        self.assertEqual(doc["classes"]["h200-sxm-141"]["latest_week"], "2026-10-26")
        self.assertEqual(doc["dropped_rows_by_source_name"], {"nvidia-h100": 1})
        self.assertIn("nvidia-h100", doc["ambiguous_dropped"])
        self.assertEqual(doc["imports"][0]["bytes"], len(payload(rows)))
        self.assertEqual(doc["billing"], "ON_DEMAND")

    def test_every_mapped_class_is_a_v0_class(self):
        for cid in rec.MAPPING.values():
            self.assertIn(cid, PEER_CLASSES)

    def test_history_is_never_rewritten(self):
        first = rec.build(payload([ROW]), now="2026-11-01T00:00:00Z")
        changed = dict(ROW, median_price=0.9)
        second = rec.build(payload([changed, dict(ROW, date="2026-10-26")]), existing=first,
                           now="2026-11-08T00:00:00Z")
        weeks = second["classes"]["rtx4090-24"]["weeks"]
        self.assertEqual(weeks["2026-10-19"]["median_price"], 0.6)       # original stands
        self.assertEqual(weeks["2026-10-26"]["median_price"], 0.6)       # new week appended
        self.assertEqual(len(second["corrections"]), 1)
        self.assertEqual(second["corrections"][0]["source_now_states"]["median_price"], 0.9)
        self.assertEqual(len(second["imports"]), 2)

    def test_a_week_missing_from_the_new_file_stays_recorded(self):
        first = rec.build(payload([ROW]), now="2026-11-01T00:00:00Z")
        second = rec.build(payload([dict(ROW, date="2026-10-26")]), existing=first)
        self.assertIn("2026-10-19", second["classes"]["rtx4090-24"]["weeks"])

    def test_loud_on_shape_or_licence_surprise(self):
        with self.assertRaises(rec.SchemaError):
            rec.build(payload([ROW], licence="proprietary"))
        with self.assertRaises(rec.SchemaError):
            rec.build(payload([{k: v for k, v in ROW.items() if k != "median_price"}]))
        with self.assertRaises(rec.SchemaError):
            rec.build(b"not json")

    def test_csv_has_attribution_on_every_row(self):
        doc = rec.build(payload([ROW]))
        lines = rec.to_csv(doc).splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("CC BY 4.0", lines[1])
        self.assertIn("getdeploying.com", lines[1])


class PackagedSeriesTests(unittest.TestCase):
    def test_packaged_series_is_present_and_attributed(self):
        doc = index.load_packaged()
        self.assertIsNotNone(doc, "riggrade/index/reconstructed-getdeploying.json must ship")
        self.assertEqual(doc["source"]["licence"], "CC BY 4.0")
        self.assertTrue(doc["imports"])
        for cid in doc["classes"]:
            self.assertIn(cid, PEER_CLASSES)
        latest = index.latest(doc)
        self.assertIn("rtx4090-24", latest["classes"])
        self.assertIn("attribution", latest["source"])
        self.assertIn("not an offer", latest["note"])


class RefreshTests(unittest.TestCase):
    def test_refresh_once_an_hour_and_keep_last_good_copy(self):
        clock = [1000.0]
        calls = []

        def fetch(url):
            calls.append(url)
            if len(calls) == 2:
                raise OSError("down")
            return {"classes": {"rtx4090-24": {"source_name": "x", "latest_week": "2026-10-26",
                                               "weeks": {"2026-10-26": dict(ROW)}}},
                    "source": {}, "imports": []}
        idx = index.Index(url="https://x/series.json", fetch=fetch, clock=lambda: clock[0], packaged={"classes": {}})
        self.assertEqual(list(idx.current()["classes"]), ["rtx4090-24"])
        idx.current()
        self.assertEqual(len(calls), 1)                      # cached inside the hour
        clock[0] += 3601
        self.assertEqual(list(idx.current()["classes"]), ["rtx4090-24"])  # failed refresh keeps the copy
        self.assertIn("OSError", idx.last_error)

    def test_no_url_means_packaged_only(self):
        idx = index.Index(packaged={"classes": {"a": {}}})
        self.assertEqual(idx.current(), {"classes": {"a": {}}})


class RouteTests(unittest.TestCase):
    def test_index_is_free_and_cacheable(self):
        app = endpoint.GradingApp({"RIGGRADE_SECRET": "s"}, lambda: 1_800_000_000)
        status, headers = [], []
        environ = {"REQUEST_METHOD": "GET", "PATH_INFO": endpoint.PREFIX + "/index",
                   "HTTP_HOST": "grade.example", "wsgi.url_scheme": "https", "wsgi.input": io.BytesIO(b"")}
        out = b"".join(app(environ, lambda s, h: (status.append(s), headers.extend(h))))
        self.assertTrue(status[0].startswith("200"))
        self.assertEqual(dict(headers)["Cache-Control"], "public, max-age=3600")
        doc = json.loads(out)
        self.assertIn("rtx4090-24", doc["classes"])
        self.assertEqual(doc["source"]["licence"], "CC BY 4.0")


class McpTests(unittest.TestCase):
    def test_price_index_tool_lists_and_filters(self):
        names = [t["name"] for t in mcp.TOOLS]
        self.assertIn("gpu_price_index", names)
        fetch = lambda url: (_ for _ in ()).throw(OSError("offline"))  # noqa: E731
        res = mcp.call_tool("gpu_price_index", {"class_id": "rtx4090-24"}, fetch=fetch)
        self.assertFalse(res["isError"])
        doc = json.loads(res["content"][0]["text"])
        self.assertEqual(list(doc["classes"]), ["rtx4090-24"])
        self.assertIn("packaged copy shown", doc["refresh_note"])
        bad = mcp.call_tool("gpu_price_index", {"class_id": "nope"}, fetch=fetch)
        self.assertTrue(bad["isError"])


if __name__ == "__main__":
    unittest.main()
