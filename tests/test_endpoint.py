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
# Five distinct operator wallets for reference-building tests.
OPS = ["0x" + ("%02x" % (0x40 + k)) * 20 for k in range(5)]


class Clock:
    def __init__(self, t=1_800_000_000):
        self.t = t

    def __call__(self):
        return self.t


class FakeAdapter:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    @staticmethod
    def payer_for(header):
        # "pay" -> the default payer; "pay:0x..." -> that address.
        return header[4:] if header.startswith("pay:") else PAYER

    def verify_only(self, header, requirement):
        self.calls.append(("verify", header, requirement))
        if not self.ok:
            return {"ok": False, "error": "payer refused by screen", "charged": False}
        return {"ok": True, "payer": self.payer_for(header), "charged": False}

    def settle(self, header, requirement):
        self.calls.append((header, requirement))
        if not self.ok:
            return {"ok": False, "error": "payer refused by screen", "charged": False}
        return {"ok": True, "payer": self.payer_for(header), "tx": "0x" + "ef" * 32,
                "receipt_header": "eyJ0eCI6ICJvayJ9", "facilitator": "https://fac.example"}


def call(app, method, path, body=None, payment=None):
    data = json.dumps(body).encode() if body is not None else b""
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path,
               "CONTENT_LENGTH": str(len(data)), "wsgi.input": io.BytesIO(data)}
    if payment:
        environ["HTTP_PAYMENT_SIGNATURE"] = payment
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

    references = None  # None: an empty book, every class still reference-building

    def app(self, env=None, adapter="default"):
        self.adapter = FakeAdapter() if adapter == "default" else adapter
        return GradingApp(self.env if env is None else env, self.clock,
                          adapter=self.adapter,
                          references=self.references() if self.references else None)

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


def _with_references():
    from riggrade.grading import ReferenceBook
    book = ReferenceBook()
    book.set_controlled("rtx4090-24", "inference", 100.0)
    book.set_controlled("h100-sxm-80", "inference", 100.0)
    return book


class PaidTests(Harness):
    """Classes whose reference is set: grades are paid."""
    references = staticmethod(_with_references)

    def test_unpaid_gets_402_with_price_and_no_grade(self):
        app = self.app()
        sid, _st, _b = self.run_session(app)
        status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid)
        self.assertEqual(status, "402 Payment Required")
        self.assertEqual(doc["accepts"][0]["amount"], "3000000")
        self.assertEqual(doc["accepts"][0]["payTo"], WALLET)
        self.assertNotIn("labels", doc)
        self.assertEqual(self.adapter.calls, [])

    def test_data_centre_price(self):
        app = self.app()
        sid, _st, _b = self.run_session(app, device="NVIDIA H100 80GB HBM3", memory=79.6)
        _s, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid)
        self.assertEqual(doc["accepts"][0]["amount"], "5000000")


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
        self.assertTrue(doc["resource"]["url"].endswith("/riggrade/v1/grade"))
        # The PAYMENT-REQUIRED header carries the same PaymentRequired object (x402 v2).
        import base64
        carried = json.loads(base64.b64decode(_h["PAYMENT-REQUIRED"]))
        self.assertEqual(carried["x402Version"], 2)
        self.assertEqual(carried["accepts"], doc["accepts"])
        self.assertEqual(carried["resource"], doc["resource"])
        self.assertNotIn("charged", carried)
        self.assertEqual(set(doc["accepts"][0]), {"scheme", "network", "amount", "asset", "payTo",
                                                   "maxTimeoutSeconds", "extra"})
        bazaar = doc["extensions"]["bazaar"]
        # The schema describes the info object; readers take input.body as the
        # request schema and output.example as the response schema.
        body_schema = bazaar["schema"]["properties"]["input"]["properties"]["body"]
        self.assertEqual(body_schema["required"], ["session_id"])
        self.assertEqual(bazaar["schema"]["properties"]["output"]["properties"]["example"]["type"], "object")
        self.assertIn("labels", bazaar["schema"]["properties"]["output"]["properties"]["example"]["properties"])
        self.assertEqual(bazaar["info"]["input"]["body"], {"session_id": "rg_s_..."})
        # Spec shape: serviceName and tags on the resource, not in the extension;
        # the schema names Draft 2020-12; the output type is "json".
        self.assertNotIn("tags", bazaar)
        self.assertNotIn("serviceName", bazaar)
        self.assertEqual(bazaar["schema"]["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(bazaar["info"]["output"]["type"], "json")
        self.assertIn("gpu", doc["resource"]["tags"])
        self.assertEqual(doc["resource"]["serviceName"], "RigGrade")
        self.assertLessEqual(len(doc["resource"]["tags"]), 5)
        # An x402 v1 header is refused with a 402 that names the version; no settle.
        calls = len(self.adapter.calls)
        environ_v1 = {"REQUEST_METHOD": "POST", "PATH_INFO": "/riggrade/v1/grade",
                      "HTTP_X_PAYMENT": "pay"}
        data = json.dumps({"session_id": sid}).encode()
        environ_v1.update({"CONTENT_LENGTH": str(len(data)), "wsgi.input": io.BytesIO(data)})
        got = {}
        out = b"".join(app(environ_v1, lambda st, h: got.update(status=st)))
        self.assertEqual(got["status"], "402 Payment Required")
        self.assertIn("x402 v1", json.loads(out)["error"])
        self.assertEqual(len(self.adapter.calls), calls)
        status, _h, doc = call(app, "POST", "/riggrade/v1/grade", {"session_id": sid},
                               payment="pay")
        self.assertEqual(status, "200 OK")
        self.assertEqual(self.adapter.calls[-1][1]["amount"], "3000000")
        # No session: the informational discovery 402 (price only), never a settle.
        calls = len(self.adapter.calls)
        status, _h, doc = call(app, "POST", "/riggrade/v1/grade", {})
        self.assertEqual(status, "402 Payment Required")
        self.assertIn("session_id is required", doc["error"])
        self.assertEqual(len(self.adapter.calls), calls)

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


class FreeReferenceTests(Harness):
    """Decided 1 Nov 2026: a rig's first session in a class that is
    still building its reference is free."""

    def test_first_session_per_rig_is_free_and_counts(self):
        app = self.app()
        sid, _st, b = self.run_session(app)
        self.assertEqual(b["preview"]["classes"]["inference"]["notice"],
                         "reference-building session, speed not graded")
        # Without the 0 USDC claim the free session is not graded yet: the 402
        # names the claim, charges nothing, and no adapter call is made.
        status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid)
        self.assertEqual(status, "402 Payment Required")
        self.assertEqual(doc["accepts"][0]["amount"], "0")
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(app.references.basis("rtx4090-24", "inference")["rigs"], 0)
        status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid,
                               payment="pay:" + OPS[0])
        self.assertEqual(status, "200 OK")
        self.assertTrue(doc["free_reference_session"])
        self.assertFalse(doc["charged"])
        # Verified, never settled.
        self.assertEqual([c[0] for c in self.adapter.calls], ["verify"])
        self.assertEqual(doc["evidence_pack"]["operator"], OPS[0])
        self.assertEqual(app.references.basis("rtx4090-24", "inference")["rigs"], 1)
        self.assertEqual(app.references.basis("rtx4090-24", "inference")["operators"], 1)
        (entry,) = salelog.read_chain(self.log)
        self.assertEqual((entry["status"], entry["amount_atomic"]),
                         ("free_reference_session", "0"))

    def test_repeat_session_from_same_rig_is_not_sold(self):
        app = self.app()
        sid, _st, _b = self.run_session(app, rig_id="rig-x")
        call(app, "POST", "/riggrade/v1/session/%s/grade" % sid, payment="pay:" + OPS[0])
        sid2, _st, _b = self.run_session(app, rig_id="rig-x")
        status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid2,
                               payment="pay")
        self.assertEqual(status, "422 Unprocessable Entity")
        self.assertFalse(doc["charged"])
        # The only adapter call was the free session's claim verification; no settle.
        self.assertEqual([c for c in self.adapter.calls if c[0] != "verify"], [])

    def test_free_until_five_rigs_then_paid(self):
        app = self.app()
        for k in range(5):
            sid, _st, _b = self.run_session(app, rig_id="rig-%d" % k)
            # No claim: the 402 asks for the 0 USDC operator claim, nothing settles.
            status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid)
            self.assertEqual(status, "402 Payment Required")
            self.assertEqual(doc["accepts"][0]["amount"], "0")
            self.assertTrue(doc["free_reference_session"])
            _s, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid,
                               payment="pay:" + OPS[k])
            self.assertTrue(doc["free_reference_session"])
            self.assertEqual(doc["evidence_pack"]["operator"], OPS[k])
        self.assertEqual([c[0] for c in self.adapter.calls].count("verify"), 5)
        sid, _st, _b = self.run_session(app, rig_id="rig-new")
        status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid)
        self.assertEqual(status, "402 Payment Required")
        self.assertEqual(doc["accepts"][0]["amount"], "3000000")

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
                                   payment="pay:" + OPS[i])
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
        self.assertEqual(headers["PAYMENT-RESPONSE"], "eyJ0eCI6ICJvayJ9")
        self.assertEqual(doc["evidence_pack"]["paying_wallet"], PAYER)
        basis = doc["evidence_pack"]["classes"]["inference"]["reference_basis"]
        self.assertEqual(basis["rigs"], 5)
        self.assertEqual(basis["operators"], 5)
        self.assertEqual(doc["evidence_pack"]["operator"], PAYER)
        self.assertIn("single session", doc["badges"][0])
        sales = salelog.read_chain(self.log)
        self.assertEqual(len(sales), 6)
        self.assertEqual(sales[-1]["amount_atomic"], "3000000")

    def test_descriptor_states_the_rule(self):
        _s, _h, doc = call(self.app(), "GET", "/riggrade/v1")
        note = doc["free_reference_sessions"]
        self.assertIn("first session there is free", note)
        # One scope wording (spec 13): descriptor and spec agree.
        self.assertIn("no reference", note)
        self.assertIn("fewer than 5 rigs", note)
        spec = (Path(__file__).resolve().parents[1] / "docs" / "grading-spec.md").read_text()
        self.assertIn('"No reference yet" means', spec)
        self.assertNotIn("first paid sessions", spec)


if __name__ == "__main__":
    unittest.main()


class OperatorRulesTests(Harness):
    """Spec 13 operator concentration at the endpoint (spec 13)."""

    def seed(self, app):
        # Five claimed free sessions from three operators (cap 2 per operator).
        for k, op in enumerate([OPS[0], OPS[0], OPS[1], OPS[1], OPS[2]]):
            sid, _st, _b = self.run_session(app, rig_id="seed-%d" % k)
            status, _h, _d = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid,
                                  payment="pay:" + op)
            self.assertEqual(status, "200 OK")
        basis = app.references.basis("rtx4090-24", "inference")
        self.assertEqual((basis["rigs"], basis["operators"]), (5, 3))
        return basis

    def test_claim_refused_by_screen_is_a_402_with_the_reason(self):
        app = self.app(adapter=FakeAdapter(ok=False))
        sid, _st, _b = self.run_session(app, rig_id="rig-s")
        status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid,
                               payment="pay:" + OPS[0])
        self.assertEqual(status, "402 Payment Required")
        self.assertIn("refused by screen", doc["error"])
        self.assertFalse(doc["charged"])
        self.assertEqual(app.references.basis("rtx4090-24", "inference")["rigs"], 0)

    def test_buyer_paying_for_a_host_run_rig_is_not_its_operator(self):
        app = self.app()
        self.seed(app)
        host = "0x" + "55" * 20
        _s, _h, sess = call(app, "POST", "/riggrade/v1/session",
                            {"workload_classes": ["inference"], "declared_runner": "host",
                             "host_wallet": host})
        _s, _h, st = call(app, "POST", "/riggrade/v1/session/%s/start" % sess["session_id"])
        call(app, "POST", "/riggrade/v1/session/%s/bundle" % sess["session_id"],
             bundle_for(sess["session_id"], sess["nonce"], st["params"], rig_id="host-rig"))
        status, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sess["session_id"],
                               payment="pay")  # PAYER is not the host
        self.assertEqual(status, "200 OK")
        self.assertIsNone(doc["evidence_pack"]["operator"])
        self.assertEqual(doc["evidence_pack"]["paying_wallet"], PAYER)
        # The host-run rig counts as a rig, not as an operator.
        basis = app.references.basis("rtx4090-24", "inference")
        self.assertEqual((basis["rigs"], basis["operators"], basis["unclaimed_rigs"]), (6, 3, 1))

    def test_host_paying_for_its_own_rig_is_the_operator(self):
        app = self.app()
        self.seed(app)
        _s, _h, sess = call(app, "POST", "/riggrade/v1/session",
                            {"workload_classes": ["inference"], "declared_runner": "host",
                             "host_wallet": PAYER})
        _s, _h, st = call(app, "POST", "/riggrade/v1/session/%s/start" % sess["session_id"])
        call(app, "POST", "/riggrade/v1/session/%s/bundle" % sess["session_id"],
             bundle_for(sess["session_id"], sess["nonce"], st["params"], rig_id="host-rig"))
        _s, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sess["session_id"],
                           payment="pay")
        self.assertEqual(doc["evidence_pack"]["operator"], PAYER)
        self.assertTrue(doc["evidence_pack"]["host_paid"])

    def test_payer_behind_too_much_of_the_reference_gets_no_speed_grade(self):
        from riggrade import grading
        app = self.app()
        self.seed(app)
        # Under the v0 numbers (cap 2, 5 rigs) no operator can exceed 40%, so the
        # rule is a backstop; lower the limit to exercise it.
        saved = grading.MAX_OPERATOR_SHARE
        grading.MAX_OPERATOR_SHARE = 0.3
        try:
            sid, _st, _b = self.run_session(app, rig_id="rig-op0")
            _s, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid,
                               payment="pay:" + OPS[0])  # OPS[0] stands behind 40%
            entry = doc["evidence_pack"]["classes"]["inference"]
            self.assertIn("40%", entry["speed_withheld"])
            self.assertIsNone(entry["speed_ratio"])
            self.assertEqual(doc["labels"][0]["pillars"]["speed"], "insufficient")
            sid, _st, _b = self.run_session(app, rig_id="rig-op3")
            _s, _h, doc = call(app, "POST", "/riggrade/v1/session/%s/grade" % sid,
                               payment="pay:" + OPS[3])
            self.assertIsNone(doc["evidence_pack"]["classes"]["inference"]["speed_withheld"])
            self.assertIsNotNone(doc["evidence_pack"]["classes"]["inference"]["speed_ratio"])
        finally:
            grading.MAX_OPERATOR_SHARE = saved

    def test_descriptor_prints_the_operator_rules_and_counts(self):
        app = self.app()
        self.seed(app)
        _s, _h, doc = call(app, "GET", "/riggrade/v1")
        rules = doc["operator_rules"]
        self.assertEqual((rules["cap_per_operator"], rules["min_operators"],
                          rules["max_payer_share"]), (2, 3, 0.4))
        ref = doc["references"]["rtx4090-24"]["inference"]
        self.assertEqual((ref["rigs"], ref["operators"]), (5, 3))
        self.assertAlmostEqual(ref["largest_operator_share"], 0.4)
