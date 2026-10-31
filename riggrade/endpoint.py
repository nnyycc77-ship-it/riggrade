"""WSGI grading endpoint (spec section 6), shipped with the sale off.

The sale is on only when every switch below is set AND a payment adapter is
given. Verifying and settling an x402 payment, and screening the payer,
belong to the adapter (riggrade.payment), which deploy code passes in at
step 2. Until then every grade request answers 503 and charges nothing.

    RIGGRADE_ENABLED=1
    PAYOUT_WALLET_ADDRESS=0x...       (40 hex digits)
    RIGGRADE_SALE_LOG=/path/to/sales.jsonl   (chain must read clean)
    RIGGRADE_SECRET=...               (session parameter seed)
    RIGGRADE_IMAGE_DIGESTS=sha256:...,sha256:...   (pinned images accepted)

riggrade.payment.FacilitatorAdapter is the adapter for keyless public
facilitators; deploy code passes it in. Nothing here builds one by itself.

Adapter contract: adapter.settle(x_payment_header, requirement) returns a
dict {"ok": bool, "payer": str, "tx": str, "receipt_header": str,
"charged": bool|"unknown", "error": str}. It must verify, screen the payer
and only then settle. Attestation is verified by attestation_verifier
(bundle) -> bool; without one, every session is unattested.
"""

import json
import re
import secrets

from . import BENCH_VERSION
from .classes import DATA_CENTRE, PEER_CLASSES, WORKLOAD_CLASSES, price_tier
from .engine import grade_session, preview
from .grading import ReferenceBook
from .heartbeat import Heartbeats
from .label import STATEMENT, badge_text, lint
from .session import MemoryStore, SessionError, Sessions
from . import salelog

PREFIX = "/riggrade/v1"
MAX_BODY = 4 * 1024 * 1024
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
# Decided prices (plan Revision 5, decided items 1), USDC atomic units.
POINT_PRICE_ATOMIC = {DATA_CENTRE: 5_000_000, "consumer": 3_000_000}
WALLET = re.compile(r"^0x[0-9a-fA-F]{40}$")
# Plan Decision 15, restated for payers (spec section 10a).
APPEALS = ("Appeals within 30 days of the grade, decided on the evidence already "
           "held, with no paid retest. A proven error is corrected by an appended "
           "correction; the original entry stays visible.")


class Config:
    def __init__(self, env):
        self.wallet = env.get("PAYOUT_WALLET_ADDRESS", "")
        self.sale_log = env.get("RIGGRADE_SALE_LOG", "")
        self.secret = env.get("RIGGRADE_SECRET", "").encode()
        self.images = [d for d in env.get("RIGGRADE_IMAGE_DIGESTS", "").split(",") if d]
        self.enabled = env.get("RIGGRADE_ENABLED") == "1"
        self.network = env.get("RIGGRADE_NETWORK", "base")

    def reasons_off(self, adapter):
        reasons = []
        if not self.enabled:
            reasons.append("RIGGRADE_ENABLED is not 1")
        if not WALLET.match(self.wallet):
            reasons.append("PAYOUT_WALLET_ADDRESS is not a 0x address")
        if not self.sale_log or not salelog.ready(self.sale_log):
            reasons.append("RIGGRADE_SALE_LOG is unset or not ready")
        if not self.secret:
            reasons.append("RIGGRADE_SECRET is unset")
        if not self.images:
            reasons.append("RIGGRADE_IMAGE_DIGESTS is unset")
        if adapter is None:
            reasons.append("no payment adapter")
        return reasons


class GradingApp:
    def __init__(self, env, clock, adapter=None, store=None, references=None,
                 attestation_verifier=None, envelopes=None, secret=None):
        self.config = Config(env)
        self.clock = clock
        self.adapter = adapter
        self.store = store or MemoryStore()
        self.references = references or ReferenceBook()
        self.verify_attestation = attestation_verifier or (lambda bundle: False)
        self.envelopes = envelopes or {}
        # Without a configured secret (the dark default) the seed is random per
        # process, so parameters can never be predicted from the source.
        seed = secret or self.config.secret or secrets.token_bytes(32)
        self.sessions = Sessions(seed, self.store, self.config.images, clock=clock)
        self.heartbeats = Heartbeats(self.store, clock)

    @property
    def on_sale(self):
        return not self.config.reasons_off(self.adapter)

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET")
        if not path.startswith(PREFIX):
            return _json(start_response, "404 Not Found", {"error": "not_found"})
        rest = path[len(PREFIX):].strip("/").split("/")
        try:
            if rest == [""] and method == "GET":
                return _json(start_response, "200 OK", self.descriptor())
            if method != "POST":
                return _json(start_response, "405 Method Not Allowed", {"error": "use POST"})
            body = _read(environ)
            if rest == ["session"]:
                return self.new_session(start_response, body)
            if len(rest) == 3 and rest[0] == "session":
                sid, action = rest[1], rest[2]
                if action == "start":
                    return self.start(start_response, sid)
                if action == "bundle":
                    return self.bundle(start_response, sid, body)
                if action == "grade":
                    return self.grade(start_response, sid, environ)
            if rest == ["grade"]:
                # One stable paid resource for every session, so a discovery
                # catalogue lists RigGrade once rather than once per sale.
                if not isinstance(body, dict) or not body.get("session_id"):
                    raise BadRequest("session_id is required")
                return self.grade(start_response, str(body["session_id"]), environ,
                                  resource_path=PREFIX + "/grade")
            if rest == ["heartbeat"]:
                return self.heartbeat(start_response, body)
        except BadRequest as exc:
            return _json(start_response, "400 Bad Request", {"error": str(exc), "charged": False})
        except SessionError as exc:
            return _json(start_response, "409 Conflict", {"error": str(exc), "charged": False})
        return _json(start_response, "404 Not Found", {"error": "not_found"})

    def descriptor(self):
        sale = {"on_sale": self.on_sale}
        if not self.on_sale:
            sale["reasons_off"] = self.config.reasons_off(self.adapter)
        references = {cid: {w: self.references.basis(cid, w) for w in WORKLOAD_CLASSES}
                      for cid in PEER_CLASSES}
        return {"product": "RigGrade", "bench_version": BENCH_VERSION,
                "spec": "docs/grading-spec.md", "statement": STATEMENT,
                "point_grade_price_usdc": {"data_centre": "5", "consumer": "3"},
                "references": references,
                "appeals": APPEALS,
                "sale": sale}

    def new_session(self, start_response, body):
        if not isinstance(body, dict):
            raise BadRequest("body must be a JSON object")
        session = self.sessions.issue(body.get("workload_classes") or [],
                                      body.get("declared_runner"),
                                      body.get("host_wallet"))
        return _json(start_response, "201 Created",
                     {k: session[k] for k in ("session_id", "nonce", "expires_at")})

    def start(self, start_response, sid):
        session = self.sessions.start(sid)
        return _json(start_response, "200 OK",
                     {"session_id": sid, "params": session["params"],
                      "expires_at": session["expires_at"]})

    def bundle(self, start_response, sid, body):
        if not isinstance(body, dict):
            raise BadRequest("bundle must be a JSON object")
        errors = self.sessions.submit(sid, body)
        if errors:
            return _json(start_response, "422 Unprocessable Entity",
                         {"accepted": False, "errors": errors, "charged": False})
        session = self.store.get(sid)
        return _json(start_response, "200 OK",
                     {"accepted": True,
                      "preview": preview(body, session["workloads"], self.references)})

    def grade(self, start_response, sid, environ, resource_path=None):
        session = self.store.get(sid)
        if session is None or session.get("bundle") is None:
            return _json(start_response, "409 Conflict",
                         {"error": "no accepted bundle for this session", "charged": False})
        if session.get("graded"):
            return _json(start_response, "409 Conflict",
                         {"error": "session already graded", "charged": False})
        prev = preview(session["bundle"], session["workloads"], self.references)
        if not self.on_sale:
            return _json(start_response, "503 Service Unavailable",
                         {"charged": False, "preview": prev,
                          "reasons_off": self.config.reasons_off(self.adapter)})
        class_id = prev["peer_class"]
        if class_id is None or not any(c["gate_passed"] for c in prev["classes"].values()):
            # Nothing gradable: the free preview already says so, and a
            # grade that can only read "unrated" is not sold.
            return _json(start_response, "422 Unprocessable Entity",
                         {"error": "nothing gradable in this session",
                          "preview": prev, "charged": False})
        tier = price_tier(class_id)
        amount = POINT_PRICE_ATOMIC[tier] * len(session["workloads"])
        requirement = {"scheme": "exact", "network": self.config.network,
                       "asset": USDC_BASE, "payTo": self.config.wallet,
                       "maxAmountRequired": str(amount),
                       "resource": _url(environ, resource_path or PREFIX + "/session/%s/grade" % sid),
                       "description": "RigGrade point grade, %d workload class(es)"
                                      % len(session["workloads"]),
                       "mimeType": "application/json",
                       "maxTimeoutSeconds": 300,
                       # EIP-712 domain of USDC on Base, needed to check the signature.
                       "extra": {"name": "USD Coin", "version": "2"}}
        header = environ.get("HTTP_X_PAYMENT")
        if not header:
            return _json(start_response, "402 Payment Required",
                         {"x402Version": 1, "accepts": [requirement],
                          "extensions": {"bazaar": bazaar_extension()},
                          "preview": prev})
        result = self.adapter.settle(header, requirement)
        if not result.get("ok") and result.get("charged") in (True, "unknown"):
            # Money may have moved without a grade: log it for reconciliation
            # and a refund by hand. The session stays ungraded.
            salelog.append(self.config.sale_log,
                           {"product": "riggrade_point_grade", "session_id": sid,
                            "status": "charged_without_grade",
                            "charged": result.get("charged"),
                            "amount_atomic": requirement["maxAmountRequired"],
                            "payer": result.get("payer"), "tx": result.get("tx"),
                            "error": result.get("error"), "at": self.clock()})
        if not result.get("ok"):
            return _json(start_response, "402 Payment Required",
                         {"error": result.get("error", "payment failed"),
                          "charged": result.get("charged", False)})
        attested = bool(self.verify_attestation(session["bundle"]))
        host_paid = bool(session.get("host_wallet")) and \
            result.get("payer", "").lower() == session["host_wallet"].lower()
        labels, pack = grade_session(session, self.references, self.envelopes,
                                     attested=attested, now=self.clock(),
                                     host_paid=host_paid)
        pack["paying_wallet"] = result.get("payer")
        badges = [badge_text(lab, "") for lab in labels]
        if lint(badges):
            raise RuntimeError("promise words in badge text: %s" % lint(badges))
        session["graded"] = True
        self.store.put(sid, session)
        salelog.append(self.config.sale_log,
                       {"product": "riggrade_point_grade", "session_id": sid,
                        "amount_atomic": requirement["maxAmountRequired"],
                        "payer": result.get("payer"), "tx": result.get("tx"),
                        "at": self.clock()})
        extra = [("X-PAYMENT-RESPONSE", result.get("receipt_header", ""))]
        return _json(start_response, "200 OK",
                     {"labels": labels, "badges": badges, "evidence_pack": pack},
                     extra)

    def heartbeat(self, start_response, body):
        if not self.on_sale:
            return _json(start_response, "503 Service Unavailable",
                         {"error": "heartbeat opens with the subscription",
                          "charged": False})
        if not isinstance(body, dict) or not body.get("rig_id"):
            raise BadRequest("rig_id is required")
        if body.get("register"):
            return _json(start_response, "200 OK", self.heartbeats.register(body["rig_id"]))
        return _json(start_response, "200 OK",
                     self.heartbeats.beat(body["rig_id"], body.get("prev"), body.get("echo")))


def bazaar_extension():
    """x402 Bazaar discovery metadata for the stable grade resource. A
    catalogue indexes it only after a paid call settles and echoes it, so
    shipping it lists nothing by itself; listing stays an owner decision."""
    return {
        "info": {
            "input": {"type": "http", "method": "POST", "bodyType": "json",
                      "body": {"session_id": "rg_s_..."}},
            "output": {"type": "application/json",
                       "example": {"labels": [{"grade": 2, "peer_class": "rtx4090-24",
                                               "workload": "inference",
                                               "capped_by": "consistency"}],
                                   "badges": ["Grade 2 · RTX 4090 class · Inference · ..."],
                                   "evidence_pack": {}}},
        },
        "schema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string",
                               "description": "A session whose result bundle was accepted: "
                                              "POST /riggrade/v1/session, run the open "
                                              "container, POST the bundle, then pay here."},
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
        "serviceName": "RigGrade",
        "tags": ["gpu", "benchmark", "verification", "grading"],
    }


class BadRequest(ValueError):
    pass


def _url(environ, path):
    """Absolute URL of a path on this server, as x402 resources are named.
    Behind a proxy, X-Forwarded-Proto gives the scheme the client used."""
    scheme = environ.get("HTTP_X_FORWARDED_PROTO") or environ.get("wsgi.url_scheme", "http")
    host = environ.get("HTTP_HOST") or environ.get("SERVER_NAME", "localhost")
    return "%s://%s%s" % (scheme.split(",")[0].strip(), host, path)


def _read(environ):
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        raise BadRequest("bad Content-Length")
    if length > MAX_BODY:
        raise BadRequest("body too large")
    raw = environ["wsgi.input"].read(length) if length else b""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        raise BadRequest("body is not JSON")


def _json(start_response, status, payload, extra_headers=()):
    data = json.dumps(payload, sort_keys=True).encode()
    start_response(status, [("Content-Type", "application/json"),
                            ("Content-Length", str(len(data)))] + list(extra_headers))
    return [data]
