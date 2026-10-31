import io
import json
import tempfile
import unittest
from pathlib import Path

from riggrade import BENCH_VERSION, salelog
from riggrade.endpoint import GradingApp
from riggrade.session import draw, sha256_hex, load_distribution

IMAGE = "sha256:" + "ab" * 32
WALLET = "0x" + "12" * 20
PAYER = "0x" + "34" * 20


class Clock:
    def __init__(self, t=1_800_000_000):
        self.t = t

    def __call__(self):
        return self.t


class FakeAdapter:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def settle(self, header, requirement):
        self.calls.append((header, requirement))
        if not self.ok:
            return {"ok": False, "error": "payer refused by screen", "charged": False}
        return {"ok": True, "payer": PAYER, "tx": "0x" + "ef" * 32,
                "receipt_header": "eyJ0eCI6ICJvayJ9"}


def call(app, method, path, body=None, payment=None):
    data = json.dumps(body).encode() if body is not None else b""
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path,
               "CONTENT_LENGTH": str(len(data)), "wsgi.input": io.BytesIO(data)}
    if payment:
        environ["HTTP_X_PAYMENT"] = payment
    got = {}

    def start_response(status, headers):
        got["status"], got["headers"] = status, dict(headers)
    out = b"".join(app(environ, start_response))
    return got["status"], got["headers"], json.loads(out)


def bundle_for(session_id, nonce, params, device="NVIDIA GeForce RTX 4090",
               memory=23.6, passes=(100.0, 101.0, 99.5), rig_id="rig-1"):
    content = "raw output"
    return {"session_id": session_id, "nonce": nonce,
            "bench_version": BENCH_VERSION, "image_digest": IMAGE,
            "params": params,
            "identity": {"device_name": device, "memory_gb": memory,
                         "card_count": 1, "rig_id": rig_id},
            "gate": {"inference": {"free_memory_gb": 23.0,
                                   "results": {"memory_integrity": True, "gemm": True,
                                               "memory_bandwidth": True,
                                               "host_link": True}}},
            "micro": {},
            "metrics": {"inference": {"throughput_passes": list(passes),
                                      "decay": 0.02, "p99_p50": 1.5}},
            "slice": None,
            "outputs": {"ruler.json": {"content": content,
                                       "sha256": sha256_hex(content)}}}


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = str(Path(self.tmp.name) / "sales.jsonl")
        self.env = {"RIGGRADE_ENABLED": "1", "PAYOUT_WALLET_ADDRESS": WALLET,
                    "RIGGRADE_SALE_LOG": self.log, "RIGGRADE_SECRET": "s3cret",
                    "RIGGRADE_IMAGE_DIGESTS": IMAGE}
        self.clock = Clock()

    def app(self, env=None, adapter="default"):
        self.adapter = FakeAdapter() if adapter == "default" else adapter
        return GradingApp(self.env if env is None else env, self.clock,
                          adapter=self.adapter)

    def run_session(self, app, **kw):
        _s, _h, s = call(app, "POST", "/riggrade/v1/session",
                         {"workload_classes": ["inference"], "declared_runner": "buyer"})
        _s, _h, st = call(app, "POST", "/riggrade/v1/session/%s/start" % s["session_id"])
        status, _h, b = call(app, "POST", "/riggrade/v1/session/%s/bundle" % s["session_id"],
                             bundle_for(s["session_id"], s["nonce"], st["params"], **kw))
        return s["session_id"], status, b


class DarkTests(Harness):
    def test_dark_by_default(self):
        app = GradingApp({}, self.clock)
        status, _h, doc = call(app, "GET", "/riggrade/v1")
        self.assertEqual(status, "200 OK")
        self.assertFalse(doc["sale"]["on_sale"])
        self.assertIn("no payment adapter", doc["sale"]["reasons_off"])

    def test_no_adapter_keeps_sale_off_even_with_every_switch(self):
        app = self.app(adapter=None)
        sid, status, _b = self.run_session(app)
        self.assertEqual(status, "200 OK")
        status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid,
                               payment="anything")
        self.assertEqual(status, "503 Service Unavailable")
        self.assertFalse(doc["charged"])
        self.assertIn("preview", doc)
        self.assertEqual(salelog.read_chain(self.log), [])

    def test_heartbeat_closed_while_dark(self):
        app = GradingApp({}, self.clock)
        status, _h, _d = call(app, "POST", "/riggrade/v1/heartbeat", {"rig_id": "r"})
        self.assertEqual(status, "503 Service Unavailable")


class SessionTests(Harness):
    def test_params_are_drawn_from_the_published_distribution(self):
        dist = load_distribution()
        app = self.app()
        _s, _h, s = call(app, "POST", "/riggrade/v1/session",
                         {"workload_classes": ["inference"], "declared_runner": "host"})
        _s, _h, st = call(app, "POST", "/riggrade/v1/session/%s/start" % s["session_id"])
        self.assertEqual(st["params"]["inference"]["concurrency"],
                         draw(b"s3cret", s["nonce"], "inference.concurrency",
                              dist["inference"]["concurrency"]))
        self.assertIn(st["params"]["passes"], dist["passes"])

    def test_bad_requests(self):
        app = self.app()
        status, _h, _d = call(app, "POST", "/riggrade/v1/session",
                              {"workload_classes": ["mining"], "declared_runner": "buyer"})
        self.assertEqual(status, "409 Conflict")
        status, _h, _d = call(app, "POST", "/riggrade/v1/session",
                              {"workload_classes": ["inference"], "declared_runner": "x"})
        self.assertEqual(status, "409 Conflict")

    def test_bundle_checks(self):
        app = self.app()
        _s, _h, s = call(app, "POST", "/riggrade/v1/session",
                         {"workload_classes": ["inference"], "declared_runner": "buyer"})
        sid = s["session_id"]
        _s, _h, st = call(app, "POST", "/riggrade/v1/session/%s/start" % sid)
        bad = bundle_for(sid, "wrong", dict(st["params"], passes=99))
        bad["image_digest"] = "sha256:other"
        bad["outputs"]["ruler.json"]["content"] = "tampered"
        status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/bundle" % sid, bad)
        self.assertEqual(status, "422 Unprocessable Entity")
        self.assertEqual(sorted(doc["errors"]),
                         ["image_not_pinned", "nonce_mismatch",
                          "output_hash_mismatch:ruler.json", "params_mismatch"])
        self.assertFalse(doc["charged"])

    def test_window_expiry(self):
        app = self.app()
        _s, _h, s = call(app, "POST", "/riggrade/v1/session",
                         {"workload_classes": ["inference"], "declared_runner": "buyer"})
        sid = s["session_id"]
        _s, _h, st = call(app, "POST", "/riggrade/v1/session/%s/start" % sid)
        self.clock.t += 3 * 3600 + 1
        status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/bundle" % sid,
                               bundle_for(sid, s["nonce"], st["params"]))
        self.assertIn("window_expired", doc["errors"])


class PaidTests(Harness):
    def test_unpaid_gets_402_with_price_and_no_grade(self):
        app = self.app()
        sid, _st, _b = self.run_session(app)
        status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid)
        self.assertEqual(status, "402 Payment Required")
        self.assertEqual(doc["accepts"][0]["maxAmountRequired"], "3000000")
        self.assertEqual(doc["accepts"][0]["payTo"], WALLET)
        self.assertNotIn("labels", doc)
        self.assertEqual(self.adapter.calls, [])

    def test_data_centre_price(self):
        app = self.app()
        sid, _st, _b = self.run_session(app, device="NVIDIA H100 80GB HBM3", memory=79.6)
        _s, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid)
        self.assertEqual(doc["accepts"][0]["maxAmountRequired"], "5000000")

    def test_preview_warns_before_payment_while_reference_building(self):
        app = self.app()
        sid, _st, b = self.run_session(app)
        cls = b["preview"]["classes"]["inference"]
        self.assertEqual(cls["notice"], "reference-building session, speed not graded")
        _s, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid)
        self.assertEqual(doc["preview"]["classes"]["inference"]["notice"],
                         "reference-building session, speed not graded")

    def test_descriptor_publishes_reference_basis_and_appeals(self):
        app = self.app()
        _s, _h, doc = call(app, "GET", "/riggrade/v1")
        self.assertEqual(doc["references"]["rtx4090-24"]["inference"]["rigs"], 0)
        self.assertIn("30 days", doc["appeals"])

    def test_first_sessions_print_insufficient_speed_then_grades(self):
        app = self.app()
        for i in range(5):
            sid, _st, _b = self.run_session(app, rig_id="rig-%d" % i)
            status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid,
                                   payment="pay")
            self.assertEqual(status, "200 OK")
            self.assertEqual(doc["labels"][0]["grade"], "insufficient")
        sid, _st, _b = self.run_session(app, rig_id="rig-new")
        _s, headers, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid,
                                payment="pay")
        label = doc["labels"][0]
        # Speed ratio about 1.0 and steady passes earn Grade 1 on the pillars,
        # but unattested evidence caps the overall grade at 2 (spec 5.5).
        self.assertEqual(label["grade"], 2)
        self.assertEqual(label["capped_by"], "evidence")
        self.assertIn("provisional", label["evidence_tag"])
        self.assertEqual(label["pillars"]["reliability"], "not measured (point grade)")
        self.assertEqual(headers["X-PAYMENT-RESPONSE"], "eyJ0eCI6ICJvayJ9")
        self.assertEqual(doc["evidence_pack"]["paying_wallet"], PAYER)
        basis = doc["evidence_pack"]["classes"]["inference"]["reference_basis"]
        self.assertEqual(basis["rigs"], 5)
        self.assertIn("single session", doc["badges"][0])
        sales = salelog.read_chain(self.log)
        self.assertEqual(len(sales), 6)
        self.assertEqual(sales[-1]["amount_atomic"], "3000000")

    def test_refused_payment_charges_nothing_and_grades_nothing(self):
        app = self.app(adapter=FakeAdapter(ok=False))
        sid, _st, _b = self.run_session(app)
        status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid,
                               payment="pay")
        self.assertEqual(status, "402 Payment Required")
        self.assertFalse(doc["charged"])
        self.assertEqual(salelog.read_chain(self.log), [])

    def test_charged_without_grade_is_logged_for_reconciliation(self):
        class Unknown(FakeAdapter):
            def settle(self, header, requirement):
                self.calls.append((header, requirement))
                return {"ok": False, "charged": "unknown", "error": "settle got no answer"}
        app = self.app(adapter=Unknown())
        sid, _st, _b = self.run_session(app)
        status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid,
                               payment="pay")
        self.assertEqual(status, "402 Payment Required")
        self.assertEqual(doc["charged"], "unknown")
        (entry,) = salelog.read_chain(self.log)
        self.assertEqual(entry["status"], "charged_without_grade")
        self.assertEqual(entry["session_id"], sid)
        req = self.adapter.calls[0][1]
        self.assertEqual(req["extra"], {"name": "USD Coin", "version": "2"})
        self.assertEqual(req["maxTimeoutSeconds"], 300)
        # the session stays ungraded, so it is not marked sold
        status, _h, _d = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid)
        self.assertEqual(status, "402 Payment Required")

    def test_stable_grade_resource_and_bazaar_metadata(self):
        app = self.app()
        sid, _st, _b = self.run_session(app)
        status, _h, doc = call(app, "POST", "/riggrade/v1/grade", {"session_id": sid})
        self.assertEqual(status, "402 Payment Required")
        self.assertTrue(doc["accepts"][0]["resource"].endswith("/riggrade/v1/grade"))
        bazaar = doc["extensions"]["bazaar"]
        self.assertEqual(bazaar["schema"]["required"], ["session_id"])
        self.assertIn("gpu", bazaar["tags"])
        status, _h, doc = call(app, "POST", "/riggrade/v1/grade", {"session_id": sid},
                               payment="pay")
        self.assertEqual(status, "200 OK")
        self.assertTrue(self.adapter.calls[-1][1]["resource"].endswith("/riggrade/v1/grade"))
        status, _h, _d = call(app, "POST", "/riggrade/v1/grade", {})
        self.assertEqual(status, "400 Bad Request")

    def test_unknown_class_is_not_sold(self):
        app = self.app()
        sid, _st, _b = self.run_session(app, device="Tesla T4", memory=15)
        status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid,
                               payment="pay")
        self.assertEqual(status, "422 Unprocessable Entity")
        self.assertFalse(doc["charged"])
        self.assertIsNone(doc["preview"]["peer_class"])
        self.assertEqual(self.adapter.calls, [])

    def test_session_grades_once(self):
        app = self.app()
        sid, _st, _b = self.run_session(app)
        call(app, "POST", "/riggrade/v1/session/%s/grade" % sid, payment="pay")
        status, _h, _d = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid,
                              payment="pay")
        self.assertEqual(status, "409 Conflict")


class HeartbeatTests(Harness):
    def test_chain(self):
        app = self.app()
        _s, _h, reg = call(app, "POST", "/riggrade/v1/heartbeat",
                           {"rig_id": "rig-1", "register": True})
        self.clock.t += 300
        _s, _h, ok = call(app, "POST", "/riggrade/v1/heartbeat",
                          {"rig_id": "rig-1", "prev": reg["next"], "echo": reg["challenge"]})
        self.assertTrue(ok["accepted"])
        _s, _h, bad = call(app, "POST", "/riggrade/v1/heartbeat",
                           {"rig_id": "rig-1", "prev": reg["next"], "echo": reg["challenge"]})
        self.assertFalse(bad["accepted"])
        self.assertEqual(bad["error"], "chain_broken")


class SaleLogTests(unittest.TestCase):
    def test_tamper_breaks_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            salelog.append(path, {"a": 1})
            salelog.append(path, {"a": 2})
            self.assertEqual(salelog.read_chain(path), [{"a": 1}, {"a": 2}])
            lines = path.read_text().splitlines()
            lines[0] = lines[0].replace('"a": 1', '"a": 9')
            path.write_text("\n".join(lines) + "\n")
            with self.assertRaises(ValueError):
                salelog.read_chain(path)
            self.assertFalse(salelog.ready(path))


if __name__ == "__main__":
    unittest.main()
