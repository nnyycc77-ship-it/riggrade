"""Discovery documents agents and crawlers read before the x402 flow:
an A2A agent card at /.well-known/agent.json, /llms.txt, and a minimal
OpenAPI document at /riggrade/v1/openapi.json. Static apart from the
base URL and the live sale state; nothing here changes a price or a rule.
"""

import base64

from . import BENCH_VERSION

# 16x16 icon served at /favicon.ico (crawlers and directories ask for one).
FAVICON = base64.b64decode(
    "AAABAAEAEBAAAAEAIABoBAAAFgAAACgAAAAQAAAAIAAAAAEAIAAAAAAAQAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA3KR//Nykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/8AAAAAAAAAAAAAAAA3KR//Nykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/wAAAAAAAAAANykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/8AAAAAAAAAADcpH/83KR//Nykf/zcpH/+o017/qNNe/zcpH/83KR//Nykf/6jTXv+o017/Nykf/zcpH/83KR//AAAAAAAAAAA3KR//Nykf/zcpH/83KR//qNNe/6jTXv83KR//Nykf/6jTXv+o017/Nykf/zcpH/83KR//Nykf/wAAAAAAAAAANykf/zcpH/83KR//Nykf/6jTXv+o017/Nykf/6jTXv+o017/Nykf/zcpH/83KR//Nykf/zcpH/8AAAAAAAAAADcpH/83KR//Nykf/zcpH/+o017/qNNe/6jTXv+o017/Nykf/zcpH/83KR//Nykf/zcpH/83KR//AAAAAAAAAAA3KR//Nykf/zcpH/83KR//qNNe/6jTXv+o017/qNNe/6jTXv83KR//Nykf/zcpH/83KR//Nykf/wAAAAAAAAAANykf/zcpH/83KR//Nykf/6jTXv+o017/Nykf/zcpH/83KR//qNNe/zcpH/83KR//Nykf/zcpH/8AAAAAAAAAADcpH/83KR//Nykf/zcpH/+o017/qNNe/zcpH/83KR//Nykf/6jTXv83KR//Nykf/zcpH/83KR//AAAAAAAAAAA3KR//Nykf/zcpH/83KR//qNNe/6jTXv+o017/qNNe/6jTXv83KR//Nykf/zcpH/83KR//Nykf/wAAAAAAAAAANykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/8AAAAAAAAAADcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/83KR//AAAAAAAAAAAAAAAANykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/83KR//Nykf/zcpH/83KR//AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==")



def _png_16(rgb_rows):
    """A 16x16 RGB PNG from 16 rows of 16 (r, g, b) tuples; stdlib only."""
    import struct
    import zlib

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in rgb_rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _icon_rows():
    """The same mark as the ICO: a dark square with a lighter inner bar."""
    dark, light = (24, 40, 72), (120, 200, 255)
    rows = []
    for y in range(16):
        rows.append([light if (3 <= x <= 12 and 6 <= y <= 9) else dark for x in range(16)])
    return rows


FAVICON_PNG = _png_16(_icon_rows())
FAVICON_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
               b'<rect width="16" height="16" fill="#182848"/>'
               b'<rect x="3" y="6" width="10" height="4" fill="#78c8ff"/></svg>')
ICONS = {"/favicon.ico": ("image/x-icon", FAVICON),
         "/favicon.png": ("image/png", FAVICON_PNG),
         "/favicon.svg": ("image/svg+xml", FAVICON_SVG)}

REPO = "https://github.com/nnyycc77-ship-it/riggrade"
BENCH_IMAGE = "ghcr.io/nnyycc77-ship-it/riggrade-bench"
DESCRIPTION = ("Independent grading of rented or hosted GPU rigs. A free one-minute "
               "preflight check, and a paid grade (x402, USDC on Base) measured against "
               "published bars for the GPU's peer class. Measured, not promised.")


def agent_card(base, prefix, on_sale):
    """A2A-style agent card. `base` is scheme://host, `prefix` the API path."""
    api = base + prefix
    return {
        "name": "RigGrade",
        "description": DESCRIPTION,
        "url": api,
        "version": BENCH_VERSION,
        "provider": {"organization": "RigGrade", "url": REPO},
        "documentationUrl": REPO + "#readme",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {"id": "gpu_price_index", "name": "GPU price index (free)",
         "description": "Use when you want the going listed on-demand price for a GPU class before "
                        "renting or listing. GET %s/index: latest week per peer class, weekly series, "
                        "reconstructed from GetDeploying's CC BY 4.0 record. Information, not an "
                        "offer." % api,
         "tags": ["gpu", "price", "index", "free"]},
        {"id": "gpu_preflight", "name": "GPU preflight check",
             "description": "Use when you have just rented a GPU box, or before listing your own, "
                            "and want to know in a minute whether it behaves like the card it "
                            "reports. Free. Run on the rented box itself: `pip install riggrade` then "
                            "`riggrade-preflight` (or `python3 -m runner.preflight`) "
                            "or the MCP tool gpu_preflight. Clocks under load, foreign use, "
                            "driver vs CUDA, PCIe link, download and disk speed.",
             "tags": ["gpu", "free", "preflight"]},
            {"id": "grade", "name": "Paid GPU rig grade",
             "description": "Use when a rented or hosted rig must be graded against published "
                            "bars for its peer class. Open a session, run the pinned benchmark container (%s), submit "
                            "the bundle, then POST %s/grade and pay the x402 requirement "
                            "(3 USDC consumer card, 5 USDC data-centre card). A session that "
                            "is not gradable is never sold." % (BENCH_IMAGE, api),
             "tags": ["gpu", "benchmark", "x402", "usdc", "base"]},
            {"id": "fixit", "name": "Host fix-it report",
             "description": "Use when the free preflight reported a warn or fail. Paid (2 USDC, "
                            "x402). POST %s/fixit with the preflight-report.json the "
                            "free check wrote: for every warn or fail finding, the likely cause, "
                            "the fix steps and who can act. Free preview first." % api,
             "tags": ["gpu", "diagnosis", "host", "x402"]},
            {"id": "cluster_acceptance", "name": "Cluster acceptance certificate",
             "description": "Use when a rented multi-node cluster must pass an independent "
                            "acceptance test before a long run, a handover or a dispute. Paid "
                            "(25 USDC, x402). Run runner.cluster on the rented nodes, then "
                            "POST %s/cluster/acceptance with the evidence pack: the server recomputes "
                            "every check and issues a signed, stored certificate. The fetch URL "
                            "returns it; its Ed25519 signature verifies offline with the public "
                            "key at /.well-known/riggrade-keys.json." % api,
             "tags": ["gpu", "cluster", "acceptance", "x402"]},
        ],
        "payments": {"protocol": "x402", "x402Version": 2, "network": "eip155:8453", "asset": "USDC",
                     "on_sale": bool(on_sale), "descriptor": api},
    }


def llms_txt(base, prefix, on_sale):
    api = base + prefix
    return "\n".join([
        "# RigGrade",
        "",
        "> " + DESCRIPTION,
        "",
        "Sale: %s. Prices: 3 USDC per workload class for consumer cards, 5 USDC for "
        "data-centre cards, paid by x402 (USDC on Base) from the buyer's own wallet. "
        "While a GPU class has no reference yet (fewer than 5 rigs), each rig's first "
        "session there is free." % ("on" if on_sale else "off"),
        "",
        "## Endpoints",
        "- Service descriptor (JSON, includes reasons the sale is off and which products are on): " + api,
        "- Host fix-it report, 2 USDC: POST " + api + "/fixit with {preflight_report}",
        "- Cluster acceptance certificate, 25 USDC: POST " + api + "/cluster/acceptance with {evidence_pack}",
        "- Browser checkout for people (the same routes, paid from your own wallet): " + api + "/checkout",
        "- Rental check, free: POST " + api + "/rental/plan before renting, then run the preflight and POST its report to " + api + "/rental/check for keep, keep with warnings or cancel",
        "- Free GPU price index (latest listed on-demand price per class, weekly): GET " + api + "/index",
        "- Pooled preflight results per class (opt in with --share; aggregates at GET " + api + "/index/quality once published)",
        "- OpenAPI: " + api + "/openapi.json",
        "- Agent card: " + base + "/.well-known/agent.json",
        "- Certificate signing keys (Ed25519, verify offline): " + base + "/.well-known/riggrade-keys.json",
        "",
        "## Code and docs",
        "- Repository (Apache-2.0): " + REPO,
        "- Grading spec: " + REPO + "/blob/main/docs/grading-spec.md",
        "- Payments: " + REPO + "/blob/main/docs/payments.md",
        "- Benchmark container: " + BENCH_IMAGE + " (run by digest; see container/bench-image.env)",
        "- Install: pip install riggrade (PyPI); commands riggrade-preflight, riggrade-mcp, riggrade-verify",
        "- MCP server (stdio): riggrade-mcp (python3 -m riggrade.mcp), tools gpu_preflight, gpu_price_index, verify_certificate and riggrade_service_info",
        "",
    ])


def robots_txt(base):
    """Everything may be crawled; the machine-readable documents are named."""
    return "\n".join([
        "User-agent: *",
        "Allow: /",
        "",
        "# RigGrade: " + base + "/llms.txt, " + base + "/openapi.json, " + base + "/.well-known/agent.json",
        "",
    ])


# Discovery profile read by x402scan (its parser is @agentcash/discovery;
# docs/SPECIFICATION.md there): every operation declares security, and a
# paid one is "security: []" plus x-payment-info with protocols and a USD
# price. Payment is declared independently of API authentication, so no
# security scheme is used for it. The runtime 402 stays authoritative. USD
# strings here must equal the atomic prices in riggrade.endpoint (a test
# holds them together). The legacy /.well-known/x402 file is not served:
# the same parser no longer reads it and flags it as a migration warning.
PAYMENT_PROTOCOLS = [{"x402": {}}]
PRICES_USD = {"grade": {"mode": "dynamic", "currency": "USD", "min": "3.00", "max": "10.00"},
              "fixit": {"mode": "fixed", "currency": "USD", "amount": "2.00"},
              "cluster": {"mode": "fixed", "currency": "USD", "amount": "25.00"}}
PAID_PATHS = {"grade": "/grade", "fixit": "/fixit", "cluster": "/cluster/acceptance"}


def payment_info(product):
    return {"protocols": PAYMENT_PROTOCOLS, "price": PRICES_USD[product]}


def openapi(base, prefix, contact_email=None):
    """contact_email: published as info.contact.email when the service is
    configured with one (RIGGRADE_CONTACT_EMAIL); registries use it for
    ownership verification. Never defaulted to anything."""
    api = base + prefix
    sess = {"type": "object", "properties": {
        "workload_classes": {"type": "array", "items": {"type": "string", "enum": ["inference", "training"]}},
        "declared_runner": {"type": "string", "enum": ["buyer", "host"]}},
        "required": ["workload_classes", "declared_runner"]}
    use_when = {"grade": "Use when a rented or hosted GPU rig must be graded against published bars "
                         "for its peer class before you rely on it or list it.",
                "fixit": "Use when the free RigGrade preflight reported a warn or fail and you need the "
                         "cause, the fix steps and who can act.",
                "cluster": "Use when a rented multi-node GPU cluster must pass an independent "
                           "acceptance test before a long run, a handover or a dispute."}
    post = lambda summary, body=None, extra=None, paid=None: {"post": {  # noqa: E731
        "summary": summary,
        **({"description": use_when[paid]} if paid else {}),
        # No API authentication on any operation (security []); a paid one
        # also declares x-payment-info and is challenged with 402 on a bare
        # request. A free one is then listed as unprotected, not probed.
        "security": [],
        **({"x-payment-info": payment_info(paid)} if paid else {}),
        **({"requestBody": {"required": True, "content": {"application/json": {"schema": body}}}} if body else {}),
        "responses": {"200": {"description": "OK"}, "400": {"description": "bad request"},
                      "409": {"description": "session state does not allow this"},
                      **(extra or {})}}}
    return {
        "openapi": "3.1.0",
        "info": {"title": "RigGrade grading API", "version": BENCH_VERSION,
                 "description": DESCRIPTION, "license": {"name": "Apache-2.0"},
                 "x-guidance": ("Paid operations answer a bare request with 402: the x402 v2 "
                                "PaymentRequired is in the PAYMENT-REQUIRED header (USDC on Base, "
                                "eip155:8453); retry with the signed PaymentPayload in "
                                "PAYMENT-SIGNATURE. Free preflight and session steps need no "
                                "payment. Measured, not promised."),
                 **({"contact": {"email": contact_email}} if contact_email else {})},
        "servers": [{"url": api}],
        # Guidance for agents: short inline, and the llms.txt pointer the
        # discovery profile names (x-agentcash-guidance.llmsTxtUrl).
        "x-agentcash-guidance": {"llmsTxtUrl": base + "/llms.txt"},
        "paths": {
            "/": {"get": {"summary": "Service descriptor: prices, references per class, sale state",
                          "security": [], "responses": {"200": {"description": "OK"}}}},
            "/index": {"get": {"summary": "Free GPU price index: latest listed on-demand price per peer class, "
                                          "weekly, reconstructed from a CC BY 4.0 third-party record",
                               "description": "Use when you want the going listed price for a GPU class "
                                              "before renting or listing. Information, not an offer.",
                               "security": [], "responses": {"200": {"description": "OK"}}}},
            "/openapi.json": {"get": {"summary": "This document", "security": [],
                                      "responses": {"200": {"description": "OK"}}}},
            "/session": post("Open a session", sess),
            "/session/{id}/start": post("Draw the run parameters (revealed only now)"),
            "/session/{id}/bundle": post("Submit the result bundle; a free preview says what is gradable",
                                         {"type": "object"}),
            "/grade": post("Request the grade; answers 402 with an x402 v2 payment requirement, "
                           "then the grade on a paid retry with the PAYMENT-SIGNATURE header",
                           {"type": "object", "properties": {"session_id": {"type": "string"}},
                            "required": ["session_id"]},
                           {"402": {"description": "x402 payment required (USDC on Base)"},
                            "422": {"description": "nothing gradable in this session"},
                            "503": {"description": "sale off; nothing charged"}}, paid="grade"),
            "/heartbeat": post("Subscription heartbeat chain", {"type": "object"}),
            "/rental/plan": post("Free, before renting a GPU: what to require for the workload, which preflight "
                                 "check confirms each requirement, and the keep rules. Names no marketplace, host "
                                 "or price", {"type": "object",
                                              "properties": {"workload": {"type": "string", "enum": ["inference", "training"]},
                                                             "gpus": {"type": "object"},
                                                             "needs": {"type": "array", "items": {"type": "string"}}},
                                              "required": ["workload"]}),
            "/rental/check": post("Free, in the first minutes after renting: keep, keep with warnings or cancel, "
                                  "from the preflight report, with each card's record history. Advice at that "
                                  "moment; nothing stored", {"type": "object",
                                                             "properties": {"preflight_report": {"type": "object"},
                                                                            "plan": {"type": "object"}},
                                                             "required": ["preflight_report"]}),
            "/preflight/share": post("Opt in: share an anonymised preflight summary with the pool (free; "
                                     "no identifiers stored; the runner's --share flag or the MCP tool's "
                                     "share flag sends it)", {"type": "object"},
                                     {"503": {"description": "pool closed"}}),
            "/index/quality": {"get": {"summary": "Pooled preflight results aggregated per peer class (free; "
                                                  "published once the method is reviewed and a class has "
                                                  "five records)",
                                       "security": [], "responses": {"200": {"description": "OK"},
                                                                     "503": {"description": "not yet published"}}}},
            "/fixit": post("Host fix-it report (2 USDC): causes and fixes for every warn or fail "
                           "finding in a preflight report; answers 402 with the x402 requirement",
                           {"type": "object", "properties": {"preflight_report": {"type": "object"}},
                            "required": ["preflight_report"]},
                           {"402": {"description": "x402 payment required (USDC on Base)"},
                            "422": {"description": "nothing to fix"},
                            "503": {"description": "product off; nothing charged"}}, paid="fixit"),
            "/cluster/acceptance": post("Cluster acceptance certificate (25 USDC): recomputes the "
                                        "evidence pack and issues a signed, stored certificate",
                                        {"type": "object",
                                         "properties": {"evidence_pack": {"type": "object"},
                                                        "payer_role": {"type": "string",
                                                                       "enum": ["buyer", "provider", "lender",
                                                                                "insurer", "other", "undeclared"],
                                                                       "description": "who is paying, declared "
                                                                                      "and printed, not verified"}},
                                         "required": ["evidence_pack"]},
                                        {"402": {"description": "x402 payment required (USDC on Base)"},
                                         "422": {"description": "evidence pack does not recompute"},
                                         "503": {"description": "product off; nothing charged"}}, paid="cluster"),
            "/cluster/acceptance/{id}": {"get": {"summary": "Fetch an issued certificate; the service checks its signature",
                                                 "security": [], "responses": {"200": {"description": "OK"},
                                                               "404": {"description": "no such certificate"}}}},
        },
    }
