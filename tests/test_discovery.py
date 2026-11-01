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
        self.assertEqual({s["id"] for s in card["skills"]}, {"gpu_preflight", "grade"})
        self.assertFalse(card["payments"]["on_sale"])

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


class Discovery402Tests(unittest.TestCase):
    def probe(self, app, header=None):
        environ = {"REQUEST_METHOD": "POST", "PATH_INFO": "/riggrade/v1/grade",
                   "CONTENT_LENGTH": "0", "wsgi.input": io.BytesIO(b""),
                   "HTTP_HOST": "grade.example", "wsgi.url_scheme": "https"}
        if header:
            environ["HTTP_X_PAYMENT"] = header
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
        self.assertEqual(req["resource"], "https://grade.example/riggrade/v1/grade")
        self.assertEqual(req["maxAmountRequired"], "3000000")
        self.assertIn("bazaar", doc["extensions"])
        self.assertFalse(doc["charged"])
        # A payment header with no session is a bad request, not a settle.
        status, doc = self.probe(app, header="eyJ...")
        self.assertTrue(status.startswith("400"), doc)
        self.assertEqual(adapter.calls, [])
