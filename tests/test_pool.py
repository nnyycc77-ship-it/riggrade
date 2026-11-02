"""Pooled preflight results: the client keeps identifiers out, the server
keeps only the cleaned record, aggregates gate at five per class and
publish only when switched on."""

import io
import json
import tempfile
import unittest
from pathlib import Path

from riggrade import endpoint, mcp, pool
from runner import preflight
from tests.test_preflight import FakeProbes


def call(app, method, path, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    status, headers = [], []
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_LENGTH": str(len(raw)),
               "HTTP_HOST": "grade.example", "wsgi.url_scheme": "https", "wsgi.input": io.BytesIO(raw)}
    out = b"".join(app(environ, lambda s, h: (status.append(s), headers.extend(h))))
    return status[0], json.loads(out) if out else None


def shared_doc(name="NVIDIA GeForce RTX 4090", mem=24564, verdict="pass", clock=0.95):
    return {"pool": 1, "gpu_name": name, "driver": "550.1", "gpus": 1, "memory_total_mb": mem,
            "verdict": verdict, "statuses": {"gpu0.clocks_under_load": "pass", "download_rate": "warn"},
            "measures": {"clock_ratio": clock, "download_mbs": 42.0}, "exclusive": True,
            "hostname": "box-7", "ip": "10.0.0.1", "errors": {"x": "/home/user/secret"}}


class CleanTests(unittest.TestCase):
    def test_keeps_only_the_listed_fields_and_maps_the_class(self):
        rec = pool.clean(shared_doc(), 1_800_000_000)
        self.assertEqual(rec["peer_class"], "rtx4090-24")
        self.assertEqual(rec["at_hour"], 1_800_000_000)
        for forbidden in ("hostname", "ip", "errors", "detail"):
            self.assertNotIn(forbidden, rec)
        self.assertEqual(rec["measures"], {"clock_ratio": 0.95, "download_mbs": 42.0})
        self.assertEqual(rec["statuses"], {"gpu0.clocks_under_load": "pass", "download_rate": "warn"})

    def test_refuses_non_pool_documents_and_bad_shapes(self):
        with self.assertRaises(ValueError):
            pool.clean({"verdict": "pass"}, 0)
        with self.assertRaises(ValueError):
            pool.clean({"pool": 1, "statuses": "x"}, 0)
        rec = pool.clean({"pool": 1, "statuses": {"a": "bogus"}, "measures": {"clock_ratio": "9", "zzz": 1},
                          "gpus": 999, "memory_total_mb": -1, "verdict": "maybe", "gpu_name": "x" * 500}, 0)
        self.assertEqual(rec["statuses"], {})
        self.assertEqual(rec["measures"], {})
        self.assertEqual((rec["gpus"], rec["memory_total_mb"], rec["verdict"]), (1, None, "unknown"))
        self.assertEqual(len(rec["gpu_name"]), 80)
        self.assertEqual(rec["peer_class"], "unknown_class")


class AggregateTests(unittest.TestCase):
    def rows(self, n, cid="rtx4090-24", verdict="pass"):
        return [{"peer_class": cid, "verdict": verdict, "at_hour": 1000 + i,
                 "statuses": {"gpu0.clocks_under_load": "pass" if i % 2 else "warn"},
                 "measures": {"clock_ratio": 0.9 + i / 100}} for i in range(n)]

    def test_gate_and_medians(self):
        agg = pool.aggregate(self.rows(4) + self.rows(5, "rtx3090-24", "warn"))
        self.assertEqual(agg["rtx4090-24"], {"records": 4, "n": 4, "status": "insufficient", "min_n": 5})
        c = agg["rtx3090-24"]
        self.assertEqual(c["status"], "published")
        self.assertEqual(c["verdict_share"]["warn"], 1.0)
        self.assertEqual(c["measures_median"]["clock_ratio"], {"median": 0.92, "n": 5})
        self.assertEqual(c["check_status_share"]["clocks_under_load"], {"pass": 0.4, "warn": 0.6})


class ClientTests(unittest.TestCase):
    def test_anonymise_drops_details_errors_and_paths(self):
        report = preflight.run(FakeProbes(), seconds=5, download=False, out=io.StringIO())
        doc = preflight.anonymise(report)
        self.assertEqual(doc["pool"], 1)
        self.assertIn("statuses", doc)
        self.assertIn("measures", doc)
        text = json.dumps(doc)
        for forbidden in ("detail", "errors", "advice", "/tmp", "problems"):
            self.assertNotIn(forbidden, text)
        self.assertTrue(all(not isinstance(v, str) for v in doc["measures"].values()))

    def test_share_never_raises(self):
        def opener(req, timeout):
            raise OSError("down")
        answer = preflight.share({"pool": 1}, "https://x/riggrade/v1", opener=opener)
        self.assertFalse(answer["accepted"])
        self.assertIn("OSError", answer["error"])

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"accepted": true}'
        seen = []
        answer = preflight.share({"pool": 1}, "https://x/riggrade/v1", opener=lambda req, timeout: (seen.append(req.full_url), Resp())[1])
        self.assertTrue(answer["accepted"])
        self.assertEqual(seen, ["https://x/riggrade/v1/preflight/share"])

    def test_mcp_share_flag(self):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"accepted": true, "stored": {}}'
        res = mcp.call_tool("gpu_preflight", {"seconds": 5, "skip_download": True, "share": True},
                            probes=FakeProbes(), share_opener=lambda req, timeout: Resp())
        text = res["content"][0]["text"]
        self.assertIn('"shared"', text)
        self.assertIn('"accepted": true', text)
        self.assertIn("no per-sender deletion path", text)

    def test_dry_share_prints_consent_and_sends_nothing(self):
        import contextlib
        from unittest import mock
        buf = io.StringIO()
        with mock.patch.object(preflight, "Probes", FakeProbes), \
             mock.patch.object(preflight, "share", side_effect=AssertionError("must not send")), \
             contextlib.redirect_stdout(buf):
            preflight.main(["--seconds", "5", "--skip-download", "--dry-share"])
        out = buf.getvalue()
        self.assertIn("no per-sender deletion path", out)
        self.assertIn("Dry run: nothing was sent.", out)
        self.assertIn('"pool": 1', out)


class ServerTests(unittest.TestCase):
    def test_intake_stores_the_cleaned_record_and_quality_is_gated(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "pool.jsonl"
            app = endpoint.GradingApp({"RIGGRADE_SECRET": "s", "RIGGRADE_PREFLIGHT_POOL": str(path)},
                                      lambda: 1_800_003_599)
            status, doc = call(app, "POST", endpoint.PREFIX + "/preflight/share", shared_doc())
            self.assertEqual(status, "200 OK")
            self.assertTrue(doc["accepted"])
            self.assertIn("no per-sender deletion path", doc["consent"])
            self.assertEqual(doc["stored"]["peer_class"], "rtx4090-24")
            self.assertEqual(doc["stored"]["at_hour"], 1_800_000_000)
            stored = json.loads(path.read_text().splitlines()[0])
            self.assertNotIn("hostname", stored)
            status, doc = call(app, "POST", endpoint.PREFIX + "/preflight/share", {"nope": 1})
            self.assertEqual(status, "400 Bad Request")
            status, doc = call(app, "GET", endpoint.PREFIX + "/index/quality")
            self.assertEqual(status, "503 Service Unavailable")
            self.assertIn("under review", doc["error"])
            for _ in range(4):
                call(app, "POST", endpoint.PREFIX + "/preflight/share", shared_doc())
            app2 = endpoint.GradingApp({"RIGGRADE_SECRET": "s", "RIGGRADE_PREFLIGHT_POOL": str(path),
                                        "RIGGRADE_POOL_PUBLISH": "1"}, lambda: 1_800_003_599)
            status, doc = call(app2, "GET", endpoint.PREFIX + "/index/quality")
            self.assertEqual(status, "200 OK")
            self.assertEqual(doc["classes"]["rtx4090-24"]["records"], 5)
            self.assertEqual(doc["classes"]["rtx4090-24"]["status"], "published")
            self.assertIn("records, not rigs", doc["note"])
            self.assertEqual(doc["collection_began"], pool.COLLECTION_BEGAN)
            self.assertIn("not rigs", doc["unit"])

    def test_pool_off(self):
        app = endpoint.GradingApp({"RIGGRADE_SECRET": "s", "RIGGRADE_PREFLIGHT_POOL": "off"}, lambda: 0)
        status, doc = call(app, "POST", endpoint.PREFIX + "/preflight/share", shared_doc())
        self.assertEqual(status, "503 Service Unavailable")
        self.assertFalse(doc["accepted"])


if __name__ == "__main__":
    unittest.main()
