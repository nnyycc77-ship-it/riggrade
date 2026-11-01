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
            {"id": "gpu_preflight", "name": "GPU preflight check",
             "description": "Free. Run on the rented box itself: `python3 -m runner.preflight` "
                            "or the MCP tool gpu_preflight. Clocks under load, foreign use, "
                            "driver vs CUDA, PCIe link, download and disk speed.",
             "tags": ["gpu", "free", "preflight"]},
            {"id": "grade", "name": "Paid GPU rig grade",
             "description": "Open a session, run the pinned benchmark container (%s), submit "
                            "the bundle, then POST %s/grade and pay the x402 requirement "
                            "(3 USDC consumer card, 5 USDC data-centre card). A session that "
                            "is not gradable is never sold." % (BENCH_IMAGE, api),
             "tags": ["gpu", "benchmark", "x402", "usdc", "base"]},
            {"id": "fixit", "name": "Host fix-it report",
             "description": "Paid (2 USDC, x402). POST %s/fixit with the preflight-report.json the "
                            "free check wrote: for every warn or fail finding, the likely cause, "
                            "the fix steps and who can act. Free preview first." % api,
             "tags": ["gpu", "diagnosis", "host", "x402"]},
            {"id": "cluster_acceptance", "name": "Cluster acceptance certificate",
             "description": "Paid (25 USDC, x402). Run runner.cluster on the rented nodes, then "
                            "POST %s/cluster/acceptance with the evidence pack: the server recomputes "
                            "every check and issues a signed, stored certificate. The fetch URL "
                            "returns it with the service's own signature check (HMAC, not "
                            "independently verifiable)." % api,
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
        "- OpenAPI: " + api + "/openapi.json",
        "- Agent card: " + base + "/.well-known/agent.json",
        "",
        "## Code and docs",
        "- Repository (Apache-2.0): " + REPO,
        "- Grading spec: " + REPO + "/blob/main/docs/grading-spec.md",
        "- Payments: " + REPO + "/blob/main/docs/payments.md",
        "- Benchmark container: " + BENCH_IMAGE + " (run by digest; see container/bench-image.env)",
        "- MCP server (stdio): python3 -m riggrade.mcp, tools gpu_preflight and riggrade_service_info",
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


def openapi(base, prefix):
    api = base + prefix
    sess = {"type": "object", "properties": {
        "workload_classes": {"type": "array", "items": {"type": "string", "enum": ["inference", "training"]}},
        "declared_runner": {"type": "string", "enum": ["buyer", "host"]}},
        "required": ["workload_classes", "declared_runner"]}
    post = lambda summary, body=None, extra=None, paid=None: {"post": {  # noqa: E731
        "summary": summary,
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
                                "payment. Measured, not promised.")},
        "servers": [{"url": api}],
        # Guidance for agents: short inline, and the llms.txt pointer the
        # discovery profile names (x-agentcash-guidance.llmsTxtUrl).
        "x-agentcash-guidance": {"llmsTxtUrl": base + "/llms.txt"},
        "paths": {
            "/": {"get": {"summary": "Service descriptor: prices, references per class, sale state",
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
                                         "properties": {"evidence_pack": {"type": "object"}},
                                         "required": ["evidence_pack"]},
                                        {"402": {"description": "x402 payment required (USDC on Base)"},
                                         "422": {"description": "evidence pack does not recompute"},
                                         "503": {"description": "product off; nothing charged"}}, paid="cluster"),
            "/cluster/acceptance/{id}": {"get": {"summary": "Fetch an issued certificate; the service checks its signature",
                                                 "security": [], "responses": {"200": {"description": "OK"},
                                                               "404": {"description": "no such certificate"}}}},
        },
    }
