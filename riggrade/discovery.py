"""Discovery documents agents and crawlers read before the x402 flow:
an A2A agent card at /.well-known/agent.json, /llms.txt, and a minimal
OpenAPI document at /riggrade/v1/openapi.json. Static apart from the
base URL and the live sale state; nothing here changes a price or a rule.
"""

from . import BENCH_VERSION

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
        ],
        "payments": {"protocol": "x402", "network": "base", "asset": "USDC",
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
        "- Service descriptor (JSON, includes reasons the sale is off): " + api,
        "- OpenAPI: " + api + "/openapi.json",
        "- Agent card: " + base + "/.well-known/agent.json",
        "",
        "## Code and docs",
        "- Repository (Apache-2.0): " + REPO,
        "- Grading spec: " + REPO + "/blob/main/docs/grading-spec.md",
        "- Payments: " + REPO + "/blob/main/docs/payments.md",
        "- Benchmark container: " + BENCH_IMAGE + " (run by digest; see deploy/bench-image.env)",
        "- MCP server (stdio): python3 -m riggrade.mcp, tools gpu_preflight and riggrade_service_info",
        "",
    ])


def openapi(base, prefix):
    api = base + prefix
    sess = {"type": "object", "properties": {
        "workload_classes": {"type": "array", "items": {"type": "string", "enum": ["inference", "training"]}},
        "declared_runner": {"type": "string", "enum": ["buyer", "host"]}},
        "required": ["workload_classes", "declared_runner"]}
    post = lambda summary, body=None, extra=None: {"post": {  # noqa: E731
        "summary": summary,
        **({"requestBody": {"required": True, "content": {"application/json": {"schema": body}}}} if body else {}),
        "responses": {"200": {"description": "OK"}, "400": {"description": "bad request"},
                      "409": {"description": "session state does not allow this"},
                      **(extra or {})}}}
    return {
        "openapi": "3.1.0",
        "info": {"title": "RigGrade grading API", "version": BENCH_VERSION,
                 "description": DESCRIPTION, "license": {"name": "Apache-2.0"}},
        "servers": [{"url": api}],
        "paths": {
            "/": {"get": {"summary": "Service descriptor: prices, references per class, sale state",
                          "responses": {"200": {"description": "OK"}}}},
            "/openapi.json": {"get": {"summary": "This document",
                                      "responses": {"200": {"description": "OK"}}}},
            "/session": post("Open a session", sess),
            "/session/{id}/start": post("Draw the run parameters (revealed only now)"),
            "/session/{id}/bundle": post("Submit the result bundle; a free preview says what is gradable",
                                         {"type": "object"}),
            "/grade": post("Request the grade; answers 402 with an x402 payment requirement, "
                           "then the grade on a paid retry with the X-PAYMENT header",
                           {"type": "object", "properties": {"session_id": {"type": "string"}},
                            "required": ["session_id"]},
                           {"402": {"description": "x402 payment required (USDC on Base)"},
                            "422": {"description": "nothing gradable in this session"},
                            "503": {"description": "sale off; nothing charged"}}),
            "/heartbeat": post("Subscription heartbeat chain", {"type": "object"}),
        },
    }
