"""RigGrade MCP server: stdio transport, standard library only.

    python3 -m riggrade.mcp

For an agent that has just rented a GPU box and runs this server on that
box. Two tools:

    gpu_preflight          run the free one-minute preflight check on this
                           machine and return its verdict and findings.
                           No results are sent anywhere; the download check
                           only reads a public file to time it.
    riggrade_service_info  how a RigGrade grade works, its prices, and,
                           when RIGGRADE_ENDPOINT is set, the live service
                           descriptor (including whether the sale is on).

A grade itself is not a tool here: it needs the full benchmark container
run and an x402 payment the agent's own wallet signs, so this server
explains the steps instead of taking them.

Protocol: JSON-RPC 2.0, one message per line on stdin and stdout, as the
MCP stdio transport specifies. Methods served: initialize, ping,
tools/list, tools/call. Notifications get no answer. Anything else gets
"method not found". Diagnostics go to stderr only. Fail closed: a tool
that cannot run returns isError true with the reason, never a partial
result presented as clean.

    RIGGRADE_ENDPOINT   optional base URL, for example
                        https://example.org/riggrade/v1
"""

import io
import json
import os
import sys
import urllib.request

SERVER_NAME = "riggrade"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602

INSTRUCTIONS = (
    "RigGrade checks rented or hosted GPU boxes. gpu_preflight runs a free "
    "one-minute check on the machine this server runs on: clocks under load "
    "and slowdown reasons, memory or compute already in use (exclusive box "
    "assumed; pass shared=true on a shared or sliced box), driver against "
    "CUDA build, PCIe link, download and disk speed. Use it right after "
    "renting, before committing to a long run; on fail, consider swapping "
    "the host. It is not a grade. The largest buyers pay for GPU capacity "
    "only once it passes acceptance testing; preflight is that first check "
    "for everyone else. A RigGrade grade is the paid next step: a full "
    "session measured against published bars for the GPU's peer class. "
    "riggrade_service_info explains how to get one.")

HOW_TO_GRADE = (
    "How a grade works: 1) POST {endpoint}/session with workload_classes "
    "(inference, training) and declared_runner. 2) POST "
    "{endpoint}/session/{id}/start to get drawn parameters. 3) Run the open "
    "benchmark container on the rig and POST the result bundle to "
    "{endpoint}/session/{id}/bundle; a free preview says what is gradable. "
    "4) POST {endpoint}/grade with the session_id; it answers 402 with an "
    "x402 v2 payment requirement (USDC on Base). Pay it with your own x402 "
    "client and repeat the call with the PAYMENT-SIGNATURE header. Point grade "
    "prices: 3 USDC per workload class for consumer cards, 5 USDC for "
    "data-centre cards. A session that is not gradable is not sold.")

TOOLS = [
    {"name": "gpu_preflight",
     "description": "Run RigGrade's free one-minute preflight check on this machine's GPUs and "
                    "return pass, warn or fail with each finding. Not a grade. No results are "
                    "sent anywhere. Takes about a minute.",
     "inputSchema": {
         "type": "object",
         "properties": {
             "seconds": {"type": "number", "minimum": 5, "maximum": 300,
                         "description": "Load phase length, default 30."},
             "skip_download": {"type": "boolean",
                               "description": "Skip the download speed check."},
             "shared": {"type": "boolean",
                        "description": "The box is shared or sliced: skip the foreign-use checks."},
         },
         "additionalProperties": False}},
    {"name": "gpu_price_index",
     "description": "The going listed on-demand price per GPU peer class (USD per GPU-hour, "
                    "latest week), from RigGrade's free index: a weekly series reconstructed "
                    "from GetDeploying's CC BY 4.0 record. Information, not an offer. Reads the "
                    "published copy; falls back to the copy shipped in this package.",
     "inputSchema": {"type": "object", "properties": {
         "class_id": {"type": "string", "description": "One peer class, e.g. rtx4090-24, or a chip family such as "
                                                       "chip:h100 once published; all when omitted."}},
         "additionalProperties": False}},
    {"name": "verify_certificate",
     "description": "Verify a RigGrade cluster acceptance certificate offline: checks its Ed25519 "
                    "signature with the public key embedded in it and, when the published key "
                    "document is given, that the key is in the service's published set. Nothing is "
                    "sent anywhere. Returns valid or not, the key id and the evidence-pack hash to "
                    "compare after recomputing with runner.cluster evaluate.",
     "inputSchema": {"type": "object", "properties": {
         "certificate": {"type": "object", "description": "The certificate JSON, or the lookup response that wraps it."},
         "keys_document": {"type": "object", "description": "Optional: the JSON from /.well-known/riggrade-keys.json."},
         "public_key": {"type": "string", "description": "Optional: a base64 Ed25519 public key to use instead of the embedded one."}},
         "required": ["certificate"], "additionalProperties": False}},
    {"name": "riggrade_service_info",
     "description": "How a RigGrade grade works and what it costs; the live service descriptor "
                    "when RIGGRADE_ENDPOINT is configured.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
]


class ToolNotFound(KeyError):
    pass


def _text(text, error=False):
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _preflight(args, probes=None):
    from runner import preflight
    seconds = float(args.get("seconds", 30))
    if not 5 <= seconds <= 300:
        return _text("seconds must be between 5 and 300", error=True)
    out = io.StringIO()
    report = preflight.run(probes or preflight.Probes(), seconds=seconds,
                           download=not args.get("skip_download"),
                           out=out, shared=bool(args.get("shared")))
    summary = {k: report[k] for k in ("verdict", "advice", "problems", "note",
                                      "exclusive_box_assumed")}
    summary["findings"] = report["findings"]
    summary["errors"] = report["errors"]
    return _text(out.getvalue() + "\n" + json.dumps(summary, indent=1, sort_keys=True))


def _service_info(fetch=None):
    endpoint = os.environ.get("RIGGRADE_ENDPOINT", "").rstrip("/")
    text = HOW_TO_GRADE.replace("{endpoint}", endpoint or "<RigGrade endpoint>")
    if not endpoint:
        return _text(text + "\n\nNo RIGGRADE_ENDPOINT is configured, so live status is unknown.")
    fetch = fetch or (lambda url: json.loads(urllib.request.urlopen(url, timeout=20).read()))
    try:
        descriptor = fetch(endpoint)
    except Exception as exc:  # noqa: BLE001 - reported, not hidden
        return _text("Could not read the service descriptor at %s: %s" % (endpoint, exc), error=True)
    return _text(text + "\n\nLive descriptor:\n" + json.dumps(descriptor, indent=1, sort_keys=True))


def _price_index(args, fetch=None):
    from riggrade import index as price_index
    url = os.environ.get("RIGGRADE_INDEX_URL", price_index.DEFAULT_URL)
    idx = price_index.Index(url=url, fetch=fetch)
    doc = price_index.latest(idx.current())
    if idx.last_error:
        doc["refresh_note"] = "published copy unreachable (%s); packaged copy shown" % idx.last_error
    wanted = args.get("class_id")
    if wanted:
        families = doc.get("chip_families") or {}
        if wanted in doc["classes"]:
            doc["classes"] = {wanted: doc["classes"][wanted]}
            doc["chip_families"] = {}
        elif wanted in families:
            doc["classes"] = {}
            doc["chip_families"] = {wanted: families[wanted]}
        else:
            known = sorted(doc["classes"]) + sorted(families)
            return _text("unknown class %r; known: %s" % (wanted, ", ".join(known)), error=True)
    return _text(json.dumps(doc, indent=1, sort_keys=True))


def _verify_certificate(args):
    from riggrade import verify
    cert = args.get("certificate")
    if not isinstance(cert, dict):
        return _text("certificate must be a JSON object", error=True)
    if isinstance(cert.get("certificate"), dict):   # the lookup response wraps it
        cert = cert["certificate"]
    keys_doc = args.get("keys_document") if isinstance(args.get("keys_document"), dict) else None
    ok, lines = verify.check(cert, keys_doc, args.get("public_key") or None)
    return _text(json.dumps({"valid": ok, "certificate_id": cert.get("certificate_id"),
                             "verdict": cert.get("verdict"), "detail": lines}, indent=1))


def call_tool(name, args, probes=None, fetch=None):
    args = args if isinstance(args, dict) else {}
    if name == "gpu_preflight":
        return _preflight(args, probes)
    if name == "gpu_price_index":
        return _price_index(args, fetch)
    if name == "riggrade_service_info":
        return _service_info(fetch)
    if name == "verify_certificate":
        return _verify_certificate(args)
    raise ToolNotFound(name)


def rpc_result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_request(message, probes=None, fetch=None):
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
        if isinstance(message, dict) and "id" in message:
            return rpc_error(message.get("id"), JSONRPC_INVALID_REQUEST, "invalid request")
        return None
    method, params = message["method"], message.get("params") or {}
    if method.startswith("notifications/") or "id" not in message:
        return None
    req_id = message["id"]
    if method == "initialize":
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        return rpc_result(req_id, {
            "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS})
    if method == "ping":
        return rpc_result(req_id, {})
    if method == "tools/list":
        return rpc_result(req_id, {"tools": TOOLS})
    if method == "tools/call":
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return rpc_error(req_id, JSONRPC_INVALID_PARAMS, "tools/call needs params.name")
        try:
            return rpc_result(req_id, call_tool(params["name"], params.get("arguments"),
                                                probes, fetch))
        except ToolNotFound as err:
            return rpc_error(req_id, JSONRPC_INVALID_PARAMS, "unknown tool: %s" % err)
        except Exception as err:  # noqa: BLE001 - fail closed as a tool error
            return rpc_result(req_id, _text("%s: %s" % (type(err).__name__, err), error=True))
    return rpc_error(req_id, JSONRPC_METHOD_NOT_FOUND, "method not found: %s" % method)


def serve(stdin, stdout, stderr=None, probes=None, fetch=None):
    stderr = stderr or sys.stderr
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            response = rpc_error(None, JSONRPC_PARSE_ERROR, "parse error")
        else:
            try:
                response = handle_request(message, probes, fetch)
            except Exception as err:  # noqa: BLE001
                stderr.write("riggrade.mcp: internal error: %s\n" % err)
                req_id = message.get("id") if isinstance(message, dict) else None
                response = rpc_error(req_id, -32603, "internal error") if req_id is not None else None
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


def main(argv=None):
    sys.stderr.write("%s %s: MCP over stdio\n" % (SERVER_NAME, SERVER_VERSION))
    try:
        serve(sys.stdin, sys.stdout)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
