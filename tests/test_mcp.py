import io
import json
import os
import tempfile
import unittest
from unittest import mock

from riggrade import mcp
from riggrade.label import lint
from tests.test_preflight import FakeProbes


def rpc(method, params=None, id_=1):
    m = {"jsonrpc": "2.0", "method": method, "id": id_}
    if params is not None:
        m["params"] = params
    return m


class McpTests(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_initialize_and_list(self):
        r = mcp.handle_request(rpc("initialize", {"protocolVersion": "2025-03-26"}))
        self.assertEqual(r["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(r["result"]["serverInfo"]["name"], "riggrade")
        names = [t["name"] for t in mcp.handle_request(rpc("tools/list"))["result"]["tools"]]
        self.assertEqual(names, ["gpu_preflight", "riggrade_service_info"])
        self.assertIsNone(mcp.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_preflight_tool(self):
        r = mcp.handle_request(rpc("tools/call", {"name": "gpu_preflight",
                                                  "arguments": {"seconds": 5}}),
                               probes=FakeProbes())
        result = r["result"]
        self.assertFalse(result["isError"])
        self.assertIn("VERDICT: PASS", result["content"][0]["text"])
        bad = mcp.handle_request(rpc("tools/call", {"name": "gpu_preflight",
                                                    "arguments": {"seconds": 1}}),
                                 probes=FakeProbes())
        self.assertTrue(bad["result"]["isError"])

    def test_service_info_without_and_with_endpoint(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            text = mcp.call_tool("riggrade_service_info", {})["content"][0]["text"]
            self.assertIn("live status is unknown", text)
        with mock.patch.dict(os.environ, {"RIGGRADE_ENDPOINT": "https://x.example/riggrade/v1"}):
            ok = mcp.call_tool("riggrade_service_info", {},
                               fetch=lambda url: {"sale": {"on_sale": False}})
            self.assertIn('"on_sale": false', ok["content"][0]["text"])
            self.assertIn("https://x.example/riggrade/v1/grade", ok["content"][0]["text"])

            def down(url):
                raise OSError("unreachable")
            self.assertTrue(mcp.call_tool("riggrade_service_info", {}, fetch=down)["isError"])

    def test_errors_and_stdio(self):
        self.assertEqual(mcp.handle_request(rpc("tools/call", {"name": "nope"}))["error"]["code"],
                         mcp.JSONRPC_INVALID_PARAMS)
        self.assertEqual(mcp.handle_request(rpc("bogus"))["error"]["code"],
                         mcp.JSONRPC_METHOD_NOT_FOUND)
        out = io.StringIO()
        mcp.serve(io.StringIO("not json\n" + json.dumps(rpc("ping", id_=7)) + "\n"), out)
        lines = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(lines[0]["error"]["code"], mcp.JSONRPC_PARSE_ERROR)
        self.assertEqual(lines[1], {"jsonrpc": "2.0", "id": 7, "result": {}})

    def test_texts_are_promise_free(self):
        texts = [mcp.INSTRUCTIONS, mcp.HOW_TO_GRADE] + [t["description"] for t in mcp.TOOLS]
        self.assertEqual(lint(texts), [])


if __name__ == "__main__":
    unittest.main()
