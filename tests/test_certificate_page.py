"""The certificate page: escaped, printable, same object as the JSON."""

import io
import json
import unittest

from riggrade import certificate_page as page


class RenderTests(unittest.TestCase):
    def test_sample_renders_with_verdict_claims_and_checks(self):
        html = page.render(page.sample(), True, "https://x/json")
        self.assertIn("Verdict: accept with exceptions", html)
        self.assertIn("NVIDIA H100 80GB HBM3", html)
        self.assertIn("<code>fabric</code>", html)
        self.assertIn("fabric: 62% of the claimed inter-node link", html)
        self.assertIn("valid by this service's own check (HMAC)", html)
        self.assertIn("SAMPLE (fictional values", html)
        self.assertIn("not measured", html)
        self.assertIn('href="https://x/json"', html)

    def test_every_value_is_escaped(self):
        cert = page.sample()
        cert["claims"]["gpu_model"] = "<script>alert(1)</script>"
        cert["checks"][0]["detail"] = "a & b <i>"
        cert["certificate_id"] = 'rc_"><img src=x>'
        html = page.render(cert, False)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("a &amp; b &lt;i&gt;", html)
        self.assertNotIn('<img src=x>', html)
        self.assertIn("INVALID", html)

    def test_wants_html(self):
        self.assertTrue(page.wants_html({"QUERY_STRING": "format=html"}))
        self.assertFalse(page.wants_html({"QUERY_STRING": "format=json", "HTTP_ACCEPT": "text/html"}))
        self.assertTrue(page.wants_html({"HTTP_ACCEPT": "text/html,application/xhtml+xml,*/*;q=0.8"}))
        self.assertFalse(page.wants_html({"HTTP_ACCEPT": "application/json"}))
        self.assertFalse(page.wants_html({}))
        self.assertFalse(page.wants_html({"HTTP_ACCEPT": "application/json, text/html"}))


class RouteTests(unittest.TestCase):
    def test_lookup_serves_html_for_a_browser_and_json_otherwise(self):
        from riggrade import acceptance, endpoint
        app = endpoint.GradingApp({"RIGGRADE_SECRET": "s"}, lambda: 1_800_000_000)
        cert = page.sample()
        cert["signature"] = acceptance.sign(cert, b"s")
        app.store.put("cert:" + cert["certificate_id"], cert)
        path = endpoint.PREFIX + "/cluster/acceptance/" + cert["certificate_id"]
        for accept, query, ctype in (("text/html", "", "text/html; charset=utf-8"),
                                     ("application/json", "", "application/json"),
                                     ("application/json", "format=html", "text/html; charset=utf-8")):
            status, headers = [], []
            environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": query,
                       "HTTP_ACCEPT": accept, "HTTP_HOST": "grade.example", "wsgi.url_scheme": "https",
                       "wsgi.input": io.BytesIO(b"")}
            out = b"".join(app(environ, lambda s, h: (status.append(s), headers.extend(h))))
            self.assertTrue(status[0].startswith("200"), (accept, query))
            self.assertEqual(dict(headers)["Content-Type"], ctype, (accept, query))
            if ctype.startswith("text/html"):
                self.assertIn(b"valid by this service's own check (HMAC)", out)
                self.assertIn(b"?format=json", out)
            else:
                self.assertTrue(json.loads(out)["signature_valid"])


if __name__ == "__main__":
    unittest.main()
