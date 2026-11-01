"""Discovery documents and listing-readiness files."""

import io
import json
import re
import unittest
from pathlib import Path

from riggrade import serve

ROOT = Path(__file__).resolve().parents[1]


def get(app, path):
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "HTTP_HOST": "grade.example",
               "wsgi.url_scheme": "https", "wsgi.input": io.BytesIO(b"")}
    status, headers = [], []
    out = b"".join(app(environ, lambda s, h: (status.append(s), headers.extend(h))))
    return status[0], dict(headers), out


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.app = serve.build({})

    def test_agent_card_names_the_api_and_the_sale_state(self):
        status, headers, out = get(self.app, "/.well-known/agent.json")
        self.assertTrue(status.startswith("200"))
        card = json.loads(out)
        self.assertEqual(card["url"], "https://grade.example/riggrade/v1")
        self.assertEqual({s["id"] for s in card["skills"]},
                         {"gpu_price_index", "gpu_preflight", "grade", "fixit", "cluster_acceptance"})
        self.assertFalse(card["payments"]["on_sale"])

    def test_favicon_is_served_at_the_root(self):
        status, headers, out = get(self.app, "/favicon.ico")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(headers["Content-Type"], "image/x-icon")
        self.assertEqual(out[:4], b"\x00\x00\x01\x00")  # ICO header
        self.assertEqual(int(headers["Content-Length"]), len(out))

    def test_png_and_svg_icons_are_served_too(self):
        status, headers, out = get(self.app, "/favicon.png")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(out[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(int(headers["Content-Length"]), len(out))
        status, headers, out = get(self.app, "/favicon.svg")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(headers["Content-Type"], "image/svg+xml")
        self.assertTrue(out.startswith(b"<svg"))

    def test_every_paid_listing_opens_with_a_use_when_line(self):
        from riggrade import discovery, endpoint

        doc = discovery.openapi("https://grade.example", endpoint.PREFIX)
        for path in ("/grade", "/fixit", "/cluster/acceptance"):
            self.assertTrue(doc["paths"][path]["post"]["description"].startswith("Use when "), path)
        card = discovery.agent_card("https://grade.example", endpoint.PREFIX, True)
        for skill in card["skills"]:
            self.assertTrue(skill["description"].startswith("Use when "), skill["id"])
        for name, note in endpoint.PRODUCT_NOTES.items():
            self.assertTrue(note.startswith("Use when "), name)

    def test_head_answers_like_get_without_a_body(self):
        for path, ctype in (("/favicon.ico", "image/x-icon"), ("/openapi.json", "application/json")):
            environ = {"REQUEST_METHOD": "HEAD", "PATH_INFO": path, "HTTP_HOST": "grade.example",
                       "wsgi.url_scheme": "https", "wsgi.input": io.BytesIO(b"")}
            status, headers = [], []
            out = b"".join(self.app(environ, lambda s, h: (status.append(s), headers.extend(h))))
            self.assertTrue(status[0].startswith("200"), path)
            self.assertEqual(dict(headers)["Content-Type"], ctype)
            self.assertGreater(int(dict(headers)["Content-Length"]), 0)
            self.assertEqual(out, b"")

    def test_openapi_declares_security_on_every_operation_and_payment_separately(self):
        status, _h, out = get(self.app, "/openapi.json")
        doc = json.loads(out)
        # Discovery profile: payment is independent of API authentication, so
        # every operation carries security [] and only paid ones x-payment-info.
        self.assertNotIn("components", doc)
        paid = {p for p, ops in doc["paths"].items()
                for op in ops.values() if "x-payment-info" in op}
        self.assertEqual(paid, {"/grade", "/fixit", "/cluster/acceptance"})
        for ops in doc["paths"].values():
            for op in ops.values():
                self.assertEqual(op["security"], [])
                self.assertTrue(op.get("summary") or op.get("description"))
                self.assertIn("responses", op)
        self.assertEqual(doc["x-agentcash-guidance"], {"llmsTxtUrl": "https://grade.example/llms.txt"})
        self.assertIn("PAYMENT-REQUIRED", doc["info"]["x-guidance"])
        self.assertNotIn("contact", doc["info"])  # absent unless the owner sets one

    def test_contact_email_is_published_only_when_configured(self):
        app = serve.build({"RIGGRADE_CONTACT_EMAIL": "ops@example.org"})
        _s, _h, out = get(app, "/openapi.json")
        self.assertEqual(json.loads(out)["info"]["contact"], {"email": "ops@example.org"})
        _s, _h, out = get(serve.build({"RIGGRADE_CONTACT_EMAIL": "  "}), "/openapi.json")
        self.assertNotIn("contact", json.loads(out)["info"])

    def test_paid_operations_declare_x_payment_info_matching_the_atomic_prices(self):
        from riggrade import discovery, endpoint
        _s, _h, out = get(self.app, "/openapi.json")
        doc = json.loads(out)
        info = {p: doc["paths"][p]["post"]["x-payment-info"] for p in ("/grade", "/fixit", "/cluster/acceptance")}
        for p, block in info.items():
            self.assertEqual(block["protocols"], discovery.PAYMENT_PROTOCOLS)
            self.assertIn("x402", block["protocols"][0])
            self.assertIn("402", doc["paths"][p]["post"]["responses"])
        self.assertEqual(info["/fixit"]["price"]["amount"], "2.00")
        self.assertEqual(info["/cluster/acceptance"]["price"]["amount"], "25.00")
        self.assertEqual((info["/grade"]["price"]["mode"], info["/grade"]["price"]["min"],
                          info["/grade"]["price"]["max"]), ("dynamic", "3.00", "10.00"))
        usd = lambda atomic: "%.2f" % (atomic / 1e6)  # noqa: E731
        self.assertEqual(usd(endpoint.PRODUCT_PRICE_ATOMIC["fixit"]), info["/fixit"]["price"]["amount"])
        self.assertEqual(usd(endpoint.PRODUCT_PRICE_ATOMIC["cluster"]), info["/cluster/acceptance"]["price"]["amount"])
        self.assertEqual(usd(min(endpoint.POINT_PRICE_ATOMIC.values())), info["/grade"]["price"]["min"])
        self.assertEqual(usd(max(endpoint.POINT_PRICE_ATOMIC.values()) * 2), info["/grade"]["price"]["max"])
        for p, ops in doc["paths"].items():
            if p not in info:
                self.assertNotIn("x-payment-info", ops.get("post", ops.get("get")))

    def test_legacy_well_known_x402_is_not_served(self):
        for path in ("/.well-known/x402", "/.well-known/x402.json"):
            status, _h, _out = get(self.app, path)
            self.assertTrue(status.startswith("404"), path)

    def test_llms_txt_is_plain_text_with_the_descriptor(self):
        status, headers, out = get(self.app, "/llms.txt")
        self.assertTrue(status.startswith("200"))
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        self.assertIn("https://grade.example/riggrade/v1", out.decode())
        self.assertIn("Sale: off", out.decode())

    def test_openapi_lists_the_paid_resource(self):
        status, _h, out = get(self.app, "/riggrade/v1/openapi.json")
        self.assertTrue(status.startswith("200"))
        doc = json.loads(out)
        self.assertEqual(doc["openapi"], "3.1.0")
        self.assertIn("402", doc["paths"]["/grade"]["post"]["responses"])

    def test_root_openapi_alias_for_crawlers(self):
        status, _h, out = get(self.app, "/openapi.json")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(json.loads(out)["openapi"], "3.1.0")

    def test_root_points_at_the_documents_and_posts_still_404(self):
        status, _h, out = get(self.app, "/")
        self.assertTrue(status.startswith("200"))
        self.assertIn("agent_card", json.loads(out))
        environ = {"REQUEST_METHOD": "POST", "PATH_INFO": "/llms.txt", "CONTENT_LENGTH": "0",
                   "wsgi.input": io.BytesIO(b"")}
        status = []
        b"".join(self.app(environ, lambda s, h: status.append(s)))
        self.assertTrue(status[0].startswith("404"))


class ListingFilesTests(unittest.TestCase):
    def test_pyproject_scripts_point_at_real_mains(self):
        text = (ROOT / "pyproject.toml").read_text()
        for script, target in (("riggrade-mcp", "riggrade.mcp:main"),
                               ("riggrade-preflight", "runner.preflight:main")):
            self.assertIn('%s = "%s"' % (script, target), text)
            module, func = target.split(":")
            mod = __import__(module, fromlist=[func])
            self.assertTrue(callable(getattr(mod, func)))

    def test_manifests_have_no_secrets_and_name_stdio(self):
        glama = json.loads((ROOT / "glama.json").read_text())
        self.assertEqual(glama["maintainers"], ["nnyycc77-ship-it"])
        smithery = (ROOT / "smithery.yaml").read_text()
        self.assertIn("type: stdio", smithery)
        self.assertIn("riggrade.mcp", smithery)
        for text in (smithery, (ROOT / "pyproject.toml").read_text()):
            self.assertIsNone(re.search(r"(?i)((secret|token|password|api_key)\s*[:=]\s*\S|0x[0-9a-fA-F]{40})", text))


class PackageLayoutTests(unittest.TestCase):
    def test_parameter_distribution_ships_inside_the_package_and_draws(self):
        from riggrade import session
        self.assertEqual(session.DISTRIBUTION_FILE.parent.parent.name, "riggrade")
        dist = session.load_distribution()
        params = session.draw_parameters(b"s", "nonce", ["inference"], dist)
        self.assertIn("inference", params)
        self.assertIn(params["passes"], dist["passes"])
        text = (ROOT / "pyproject.toml").read_text()
        self.assertIn('riggrade = ["spec/*.json", "index/*.json", "index/*.csv"]', text)
        self.assertNotIn("../spec", text)


class PublishWorkflowTests(unittest.TestCase):
    def test_pypi_publish_is_keyless_and_tests_first(self):
        text = (ROOT / ".github" / "workflows" / "publish-pypi.yml").read_text()
        self.assertIn("id-token: write", text)
        self.assertIn("pypa/gh-action-pypi-publish", text)
        self.assertIn("environment: pypi", text)
        self.assertIn("python -m unittest discover", text)
        self.assertIn("if: github.event_name == 'release'", text)  # a manual run only builds
        for word in ("password", "secrets.", "PYPI_API_TOKEN", "TWINE"):
            self.assertNotIn(word, text)
        self.assertIsNone(re.search(r"pypi-[A-Za-z0-9_-]{20,}", text))


class Discovery402Tests(unittest.TestCase):
    def probe(self, app, header=None):
        environ = {"REQUEST_METHOD": "POST", "PATH_INFO": "/riggrade/v1/grade",
                   "CONTENT_LENGTH": "0", "wsgi.input": io.BytesIO(b""),
                   "HTTP_HOST": "grade.example", "wsgi.url_scheme": "https"}
        if header:
            environ["HTTP_PAYMENT_SIGNATURE"] = header
        status = []
        out = b"".join(app(environ, lambda s, h: status.append(s)))
        return status[0], json.loads(out)

    def test_sale_off_keeps_the_400(self):
        status, doc = self.probe(serve.build({}))
        self.assertTrue(status.startswith("400"), doc)

    def test_sale_on_answers_an_informational_402_and_never_settles(self):
        import tempfile
        from riggrade.endpoint import GradingApp
        from tests.test_endpoint import FakeAdapter, IMAGE, WALLET
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        adapter = FakeAdapter()
        app = GradingApp({"RIGGRADE_ENABLED": "1", "PAYOUT_WALLET_ADDRESS": WALLET,
                          "RIGGRADE_SALE_LOG": tmp.name + "/sales.jsonl",
                          "RIGGRADE_SECRET": "s", "RIGGRADE_IMAGE_DIGESTS": IMAGE},
                         lambda: 1_800_000_000, adapter=adapter)
        self.assertTrue(app.on_sale)
        status, doc = self.probe(app)
        self.assertTrue(status.startswith("402"), doc)
        req = doc["accepts"][0]
        self.assertEqual(doc["resource"]["url"], "https://grade.example/riggrade/v1/grade")
        self.assertEqual((doc["x402Version"], req["amount"], req["network"]),
                         (2, "3000000", "eip155:8453"))
        self.assertIn("bazaar", doc["extensions"])
        self.assertFalse(doc["charged"])
        # A payment header with no session is a bad request, not a settle.
        status, doc = self.probe(app, header="eyJ...")
        self.assertTrue(status.startswith("400"), doc)
        self.assertEqual(adapter.calls, [])
