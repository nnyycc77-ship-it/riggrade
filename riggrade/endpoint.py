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

import base64
import json
import re
import secrets

from . import BENCH_VERSION
from .classes import DATA_CENTRE, PEER_CLASSES, WORKLOAD_CLASSES, price_tier
from .engine import grade_session, preview
from .grading import CAP_PER_OPERATOR, MAX_OPERATOR_SHARE, MIN_OPERATORS, ReferenceBook
from .heartbeat import Heartbeats
from .label import STATEMENT, badge_text, lint
from .session import MemoryStore, SessionError, Sessions
from . import salelog
from . import discovery
from . import fixit
from . import acceptance

PREFIX = "/riggrade/v1"
X402_VERSION = 2
NETWORK_BASE = "eip155:8453"
PAID_ROUTES = {("grade",): "grade", ("fixit",): "fixit", ("cluster", "acceptance"): "cluster"}
V1_REFUSED = ("x402 v1 (X-PAYMENT) is not accepted; send PAYMENT-SIGNATURE with "
              "x402Version 2, as the PAYMENT-REQUIRED header of this answer describes")
DISCOVERY_PATHS = ("/", "/favicon.ico", "/.well-known/agent.json", "/llms.txt", "/openapi.json",
                   PREFIX + "/openapi.json")
MAX_BODY = 4 * 1024 * 1024
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
# Decided prices (plan Revision 5, decided items 1), USDC atomic units.
POINT_PRICE_ATOMIC = {DATA_CENTRE: 5_000_000, "consumer": 3_000_000}
# Products beyond the point grade (owner decision 1 Nov 2026; each is on
# sale only when RIGGRADE_PRODUCTS names it). USDC atomic units.
PRODUCT_PRICE_ATOMIC = {"fixit": 2_000_000, "cluster": 25_000_000}
PRODUCT_NOTES = {
    "fixit": "Host fix-it report, 2 USDC: POST /riggrade/v1/fixit with {preflight_report}; for "
             "every warn or fail finding, the likely cause, the fix steps and who can act. "
             "Free preview first; nothing is sold when every finding passed.",
    "cluster": "Cluster acceptance certificate, 25 USDC: POST /riggrade/v1/cluster/acceptance "
               "with {evidence_pack} (the acceptance.json runner.cluster wrote); the server "
               "recomputes every check, refuses a pack that does not recompute, and issues a "
               "signed, stored certificate. GET /riggrade/v1/cluster/acceptance/{id} returns "
               "it with the service's own signature check (HMAC: not independently verifiable).",
}
WALLET = re.compile(r"^0x[0-9a-fA-F]{40}$")
OPERATOR_CLAIM_NOTE = ("A free reference session is claimed with a PAYMENT-SIGNATURE authorisation "
                       "of 0 USDC to the payout address (the 402 states it): the facilitator "
                       "verifies the signature, nothing is settled, and the signer becomes "
                       "the rig's operator. At most 2 rigs per operator count toward a class "
                       "reference, speed is graded only once 3 operators stand behind it, and "
                       "a payer behind more than 40% of a reference gets no speed grade from it.")
FREE_REFERENCE_NOTE = ("Free reference-building session: while a GPU class has no reference "
                       "yet for a workload (fewer than 5 rigs behind it and no controlled "
                       "run), each rig's first session there is free. It counts toward the "
                       "reference; speed is not graded until the reference exists.")
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
        # CAIP-2 chain id (x402 v2): Base mainnet.
        self.network = env.get("RIGGRADE_NETWORK", NETWORK_BASE)
        # Published in the OpenAPI info block only when set (owner's choice).
        self.contact_email = env.get("RIGGRADE_CONTACT_EMAIL", "").strip()
        self.products = {p.strip() for p in env.get("RIGGRADE_PRODUCTS", "").split(",") if p.strip()}

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

    def product_reasons_off(self, name):
        reasons = list(self.config.reasons_off(self.adapter))
        if name not in self.config.products:
            reasons.append("RIGGRADE_PRODUCTS does not include %s" % name)
        return reasons

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET")
        if method == "GET" and path in DISCOVERY_PATHS:
            return self.discovery(start_response, environ, path)
        if not path.startswith(PREFIX):
            return _json(start_response, "404 Not Found", {"error": "not_found"})
        rest = path[len(PREFIX):].strip("/").split("/")
        try:
            if rest == [""] and method == "GET":
                return _json(start_response, "200 OK", self.descriptor())
            if method == "GET" and len(rest) == 3 and rest[:2] == ["cluster", "acceptance"]:
                return self.certificate_lookup(start_response, rest[2])
            if method != "POST" and tuple(rest) in PAID_ROUTES:
                # A bare probe of a paid route (x402 crawlers GET it): the 402
                # challenge comes before any request validation.
                return self.probe(start_response, environ, PAID_ROUTES[tuple(rest)])
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
                    if self.on_sale and not self._payment_header(environ):
                        # Discovery probe (x402 crawlers POST the resource with
                        # no body): the standard 402 challenge, informational.
                        # Nothing paid against it is ever settled, because a
                        # settle needs a session with an accepted bundle.
                        return self.discovery_402(start_response, environ)
                    raise BadRequest("session_id is required")
                return self.grade(start_response, str(body["session_id"]), environ,
                                  resource_path=PREFIX + "/grade")
            if rest == ["heartbeat"]:
                return self.heartbeat(start_response, body)
            if rest == ["fixit"]:
                return self.fixit_report(start_response, environ, body)
            if rest == ["cluster", "acceptance"]:
                return self.cluster_acceptance(start_response, environ, body)
        except BadRequest as exc:
            return _json(start_response, "400 Bad Request", {"error": str(exc), "charged": False})
        except SessionError as exc:
            return _json(start_response, "409 Conflict", {"error": str(exc), "charged": False})
        return _json(start_response, "404 Not Found", {"error": "not_found"})

    def discovery(self, start_response, environ, path):
        """Read-only documents for agents and crawlers (riggrade.discovery)."""
        base = _url(environ, "")
        if path == "/favicon.ico":
            start_response("200 OK", [("Content-Type", "image/x-icon"),
                                      ("Content-Length", str(len(discovery.FAVICON))),
                                      ("Cache-Control", "public, max-age=86400")])
            return [discovery.FAVICON]
        if path == "/llms.txt":
            data = discovery.llms_txt(base, PREFIX, self.on_sale).encode()
            start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8"),
                                      ("Content-Length", str(len(data)))])
            return [data]
        if path == "/.well-known/agent.json":
            return _json(start_response, "200 OK", discovery.agent_card(base, PREFIX, self.on_sale))
        if path in ("/openapi.json", PREFIX + "/openapi.json"):
            return _json(start_response, "200 OK",
                         discovery.openapi(base, PREFIX, self.config.contact_email))
        return _json(start_response, "200 OK",
                     {"product": "RigGrade", "descriptor": base + PREFIX,
                      "openapi": base + PREFIX + "/openapi.json",
                      "agent_card": base + "/.well-known/agent.json", "llms": base + "/llms.txt"})

    def descriptor(self):
        sale = {"on_sale": self.on_sale}
        if not self.on_sale:
            sale["reasons_off"] = self.config.reasons_off(self.adapter)
        references = {cid: {w: self.references.basis(cid, w) for w in WORKLOAD_CLASSES}
                      for cid in PEER_CLASSES}
        return {"product": "RigGrade", "bench_version": BENCH_VERSION,
                "spec": "docs/grading-spec.md", "statement": STATEMENT,
                "point_grade_price_usdc": {"data_centre": "5", "consumer": "3"},
                "free_reference_sessions": FREE_REFERENCE_NOTE,
                "products": {name: {"price_usdc": str(PRODUCT_PRICE_ATOMIC[name] // 1_000_000),
                                    "on_sale": not self.product_reasons_off(name),
                                    "note": PRODUCT_NOTES[name]}
                             for name in sorted(PRODUCT_PRICE_ATOMIC)},
                "operator_rules": {"cap_per_operator": CAP_PER_OPERATOR,
                                   "min_operators": MIN_OPERATORS,
                                   "max_payer_share": MAX_OPERATOR_SHARE,
                                   "free_session_claim": OPERATOR_CLAIM_NOTE},
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
        # Owner decision (1 Nov 2026): while a class is still building its
        # reference, each rig's first session in it is free and sets the bar.
        # A rig's repeat session in such a class cannot be speed graded, so it
        # is not sold. Only workloads with a reference are charged.
        rig_id = (session["bundle"].get("identity") or {}).get("rig_id") or "rg_unknown"
        free, payable, blocked = [], [], []
        for workload, entry in prev["classes"].items():
            if not entry.get("gate_passed"):
                continue
            if self.references.reference(class_id, workload)[0] is not None:
                payable.append(workload)
            elif self.references.has_rig(class_id, workload, rig_id):
                blocked.append(workload)
            else:
                free.append(workload)
        if not free and not payable:
            return _json(start_response, "422 Unprocessable Entity",
                         {"error": "nothing gradable in this session: the class is still "
                                   "building its reference and this rig's free first "
                                   "session there is already used",
                          "preview": prev, "charged": False})
        if not payable:
            return self._free_reference_grade(start_response, sid, session, free, environ)
        tier = price_tier(class_id)
        amount = POINT_PRICE_ATOMIC[tier] * len(payable)
        requirement = self._accept(amount)
        path = resource_path or PREFIX + "/session/%s/grade" % sid
        description = ("RigGrade point grade, %d workload class(es)%s"
                       % (len(payable), "; reference-building included free: "
                          + ", ".join(free) if free else ""))
        header = self._payment_header(environ)
        if not header:
            return self._payment_required(start_response, environ, path, amount, description,
                                          bazaar=bazaar_extension(),
                                          error=self._v1_error(environ), preview=prev)
        result = self.adapter.settle(header, requirement)
        if not result.get("ok") and result.get("charged") in (True, "unknown"):
            # Money may have moved without a grade: log it for reconciliation
            # and a refund by hand. The session stays ungraded.
            salelog.append(self.config.sale_log,
                           {"product": "riggrade_point_grade", "session_id": sid,
                            "status": "charged_without_grade",
                            "charged": result.get("charged"),
                            "amount_atomic": requirement["amount"],
                            "payer": result.get("payer"), "tx": result.get("tx"),
                            "error": result.get("error"), "at": self.clock()})
        if not result.get("ok"):
            return _json(start_response, "402 Payment Required",
                         {"error": result.get("error", "payment failed"),
                          "charged": result.get("charged", False)})
        attested = bool(self.verify_attestation(session["bundle"]))
        host_paid = bool(session.get("host_wallet")) and \
            result.get("payer", "").lower() == session["host_wallet"].lower()
        # Operator (spec 13): the payer counts as the rig's operator only when
        # the payer is the host or the declared runner; a buyer paying to look
        # at someone else's host-run rig is not its operator.
        operator = result.get("payer") if (host_paid or session["declared_runner"] == "buyer") else None
        labels, pack = grade_session(session, self.references, self.envelopes,
                                     attested=attested, now=self.clock(),
                                     host_paid=host_paid, operator=operator)
        pack["paying_wallet"] = result.get("payer")
        pack["operator"] = operator
        badges = [badge_text(lab, "") for lab in labels]
        if lint(badges):
            raise RuntimeError("promise words in badge text: %s" % lint(badges))
        session["graded"] = True
        self.store.put(sid, session)
        salelog.append(self.config.sale_log,
                       {"product": "riggrade_point_grade", "session_id": sid,
                        "amount_atomic": requirement["amount"],
                        "paid_workloads": payable, "free_workloads": free,
                        "payer": result.get("payer"), "tx": result.get("tx"),
                        "at": self.clock()})
        extra = [("PAYMENT-RESPONSE", result.get("receipt_header", ""))]
        return _json(start_response, "200 OK",
                     {"labels": labels, "badges": badges, "evidence_pack": pack},
                     extra)

    def discovery_402(self, start_response, environ):
        description = ("RigGrade point grade: 3 USDC per workload class for consumer cards, "
                       "5 USDC for data-centre cards. Open a session first (POST /session), run "
                       "the pinned container, submit the bundle, then POST here with "
                       "{session_id}; the 402 for that session states the exact amount.")
        return self._payment_required(start_response, environ, PREFIX + "/grade",
                                      POINT_PRICE_ATOMIC["consumer"], description,
                                      bazaar=bazaar_extension(),
                                      error=self._v1_error(environ)
                                      or "session_id is required; this 402 describes the price only")

    def probe(self, start_response, environ, name):
        """A bare request to a paid route, any method, no body: the 402
        challenge (or 503 with reasons while off), before any validation."""
        if name == "grade":
            if not self.on_sale:
                return _json(start_response, "503 Service Unavailable",
                             {"charged": False, "reasons_off": self.config.reasons_off(self.adapter)})
            return self.discovery_402(start_response, environ)
        off = self.product_reasons_off(name)
        if off:
            return _json(start_response, "503 Service Unavailable",
                         {"charged": False, "reasons_off": off})
        path = {"fixit": PREFIX + "/fixit", "cluster": PREFIX + "/cluster/acceptance"}[name]
        return self._payment_required(start_response, environ, path, PRODUCT_PRICE_ATOMIC[name],
                                      PRODUCT_NOTES[name], bazaar=bazaar_extension(name),
                                      error=self._v1_error(environ)
                                      or "POST the JSON body the description names; this 402 "
                                         "describes the price only")

    # ------------------------------------------------------------ x402 v2
    @staticmethod
    def _payment_header(environ):
        return environ.get("HTTP_PAYMENT_SIGNATURE")

    @staticmethod
    def _v1_error(environ):
        return V1_REFUSED if environ.get("HTTP_X_PAYMENT") else None

    def _accept(self, amount):
        """One x402 v2 PaymentRequirements entry: exact USDC on Base."""
        return {"scheme": "exact", "network": self.config.network, "amount": str(amount),
                "asset": USDC_BASE, "payTo": self.config.wallet, "maxTimeoutSeconds": 300,
                # EIP-712 domain of USDC on Base, needed to check the signature.
                "extra": {"name": "USD Coin", "version": "2"}}

    def _payment_required(self, start_response, environ, path, amount, description,
                          bazaar=None, error=None, **body_extra):
        """x402 v2 challenge: the PaymentRequired object goes base64 in the
        PAYMENT-REQUIRED header (the canonical carrier) and, for people and
        older tooling, as the JSON body too, with charged false."""
        doc = {"x402Version": X402_VERSION,
               "resource": {"url": _url(environ, path), "description": description,
                            "mimeType": "application/json", "serviceName": "RigGrade",
                            "tags": ["gpu", "benchmark", "grading", "x402", "usdc"]},
               "accepts": [self._accept(amount)]}
        if error:
            doc["error"] = error
        if bazaar is not None:
            doc["extensions"] = {"bazaar": bazaar}
        header = base64.b64encode(json.dumps(doc, sort_keys=True).encode()).decode()
        return _json(start_response, "402 Payment Required", dict(doc, charged=False, **body_extra),
                     [("PAYMENT-REQUIRED", header)])

    # ------------------------------------------------------------ products

    def _sell(self, start_response, environ, name, path, description, preview_doc, deliver):
        """Shared paid path for a product: 503 with reasons while off, the
        402 challenge without a payment header, settle through the adapter,
        then deliver(result) -> (response doc, sale-log extra). A charge
        without a delivery is logged for reconciliation, as for grades."""
        off = self.product_reasons_off(name)
        if off:
            return _json(start_response, "503 Service Unavailable",
                         {"charged": False, "preview": preview_doc, "reasons_off": off})
        requirement = self._accept(PRODUCT_PRICE_ATOMIC[name])
        header = self._payment_header(environ)
        if not header:
            return self._payment_required(start_response, environ, path, PRODUCT_PRICE_ATOMIC[name],
                                          description, bazaar=bazaar_extension(name),
                                          error=self._v1_error(environ), preview=preview_doc)
        result = self.adapter.settle(header, requirement)
        product = {"fixit": fixit.PRODUCT, "cluster": acceptance.PRODUCT}[name]
        if not result.get("ok") and result.get("charged") in (True, "unknown"):
            salelog.append(self.config.sale_log,
                           {"product": product, "status": "charged_without_delivery",
                            "charged": result.get("charged"),
                            "amount_atomic": requirement["amount"],
                            "payer": result.get("payer"), "tx": result.get("tx"),
                            "error": result.get("error"), "at": self.clock()})
        if not result.get("ok"):
            return _json(start_response, "402 Payment Required",
                         {"error": result.get("error", "payment failed"),
                          "charged": result.get("charged", False)})
        try:
            doc, extra = deliver(result)
        except Exception as exc:  # noqa: BLE001 - a settled charge must never go unrecorded
            salelog.append(self.config.sale_log,
                           {"product": product, "status": "charged_without_delivery",
                            "charged": True, "amount_atomic": requirement["amount"],
                            "payer": result.get("payer"), "tx": result.get("tx"),
                            "error": "delivery failed: %s: %s" % (type(exc).__name__, exc),
                            "at": self.clock()})
            return _json(start_response, "500 Internal Server Error",
                         {"error": "charged but delivery failed; recorded for refund by hand",
                          "charged": True, "tx": result.get("tx")})
        salelog.append(self.config.sale_log,
                       dict({"product": product, "amount_atomic": requirement["amount"],
                             "payer": result.get("payer"), "tx": result.get("tx"),
                             "at": self.clock()}, **extra))
        return _json(start_response, "200 OK", doc,
                     [("PAYMENT-RESPONSE", result.get("receipt_header", ""))])

    def fixit_report(self, start_response, environ, body):
        report = body.get("preflight_report") if isinstance(body, dict) else None
        if (not isinstance(report, dict) or report.get("preflight") is not True
                or not isinstance(report.get("findings"), list)):
            if not self._payment_header(environ):
                return self.probe(start_response, environ, "fixit")
            raise BadRequest("preflight_report (the preflight-report.json content) is required")
        prev = fixit.preview(report)
        if prev["fixable"] == 0 and not self.product_reasons_off("fixit"):
            return _json(start_response, "422 Unprocessable Entity",
                         {"error": "nothing to fix: no finding warned or failed",
                          "preview": prev, "charged": False})
        description = ("RigGrade host fix-it report: %d finding(s) with cause, fix and who can act"
                       % prev["fixable"])

        def deliver(result):
            doc = fixit.diagnose(report)
            doc["paying_wallet"] = result.get("payer")
            return {"report": doc, "preview": prev}, {"findings": prev["fixable"]}
        return self._sell(start_response, environ, "fixit", PREFIX + "/fixit", description,
                          prev, deliver)

    def cluster_acceptance(self, start_response, environ, body):
        pack = body.get("evidence_pack") if isinstance(body, dict) else None
        error = acceptance.validate(pack)
        if error:
            if not self._payment_header(environ):
                return self.probe(start_response, environ, "cluster")
            raise BadRequest(error)
        checks, result = acceptance.recompute(pack)
        if result["verdict"] != pack.get("verdict"):
            return _json(start_response, "422 Unprocessable Entity",
                         {"error": "evidence pack does not recompute: submitted verdict %r, "
                                   "recomputed %r" % (pack.get("verdict"), result["verdict"]),
                          "recomputed": result, "charged": False})
        prev = dict(result, nodes=len(pack["nodes"]),
                    images=sorted({n.get("image") or "unrecorded" for n in pack["nodes"]}),
                    checks=[{"check": c["check"], "status": c["status"]} for c in checks])
        description = ("RigGrade cluster acceptance certificate: %d node(s), verdict %s, signed and stored"
                       % (len(pack["nodes"]), result["verdict"]))

        def deliver(settled):
            cert_id = "rc_" + secrets.token_hex(8)
            cert = acceptance.certificate(pack, checks, result, self.config.secret,
                                          self.clock(), cert_id)
            cert["paying_wallet"] = settled.get("payer")
            cert["signature"] = acceptance.sign(cert, self.config.secret)
            self.store.put("cert:" + cert_id, cert)
            return ({"certificate": cert,
                     "lookup_url": _url(environ, PREFIX + "/cluster/acceptance/" + cert_id)},
                    {"certificate_id": cert_id, "verdict": result["verdict"]})
        return self._sell(start_response, environ, "cluster", PREFIX + "/cluster/acceptance",
                          description, prev, deliver)

    def certificate_lookup(self, start_response, cert_id):
        cert = self.store.get("cert:" + cert_id) if cert_id.startswith("rc_") else None
        if cert is None:
            return _json(start_response, "404 Not Found", {"error": "no such certificate"})
        return _json(start_response, "200 OK",
                     {"certificate": cert,
                      "signature_valid": acceptance.verify(cert, self.config.secret)})

    CLAIM_DESCRIPTION = ("RigGrade free reference session: operator claim, 0 USDC. "
                         "Verified, never settled; the signer becomes the rig's operator.")

    def _claim_requirement(self, environ, sid):
        return self._accept("0")

    def _operator_claim(self, environ, sid):
        """Free-session operator claim: a zero-amount x402 authorisation in
        PAYMENT-SIGNATURE, verified (never settled) by the facilitator and
        screened. Returns (operator, None) or (None, refusal text)."""
        header = self._payment_header(environ)
        if not header:
            return None, self._v1_error(environ) or "no claim attached"
        verify = getattr(self.adapter, "verify_only", None)
        if verify is None:
            return None, "claims not supported"
        result = verify(header, self._claim_requirement(environ, sid))
        if not result.get("ok"):
            return None, result.get("error", "claim refused")
        return result["payer"], None

    def _free_reference_grade(self, start_response, sid, session, free, environ):
        # Spec 13: a free reference session is claimed with a 0 USDC x402
        # authorisation, so every counted rig has a verified operator. The
        # 402 below asks for exactly that; nothing is ever settled.
        operator, refused = self._operator_claim(environ, sid)
        if operator is None:
            prev = preview(session["bundle"], session["workloads"], self.references)
            return self._payment_required(start_response, environ,
                                          PREFIX + "/session/%s/grade" % sid, "0",
                                          self.CLAIM_DESCRIPTION, error=refused,
                                          free_reference_session=True,
                                          note=OPERATOR_CLAIM_NOTE, preview=prev)
        attested = bool(self.verify_attestation(session["bundle"]))
        labels, pack = grade_session(session, self.references, self.envelopes,
                                     attested=attested, now=self.clock(),
                                     operator=operator)
        pack["free_reference_session"] = True
        pack["operator"] = operator
        badges = [badge_text(lab, "") for lab in labels]
        if lint(badges):
            raise RuntimeError("promise words in badge text: %s" % lint(badges))
        session["graded"] = True
        self.store.put(sid, session)
        salelog.append(self.config.sale_log,
                       {"product": "riggrade_point_grade", "session_id": sid,
                        "status": "free_reference_session", "amount_atomic": "0",
                        "free_workloads": free, "at": self.clock()})
        return _json(start_response, "200 OK",
                     {"labels": labels, "badges": badges, "evidence_pack": pack,
                      "charged": False, "free_reference_session": True,
                      "note": FREE_REFERENCE_NOTE})

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


def bazaar_extension(resource="grade"):
    """x402 Bazaar discovery metadata for a stable paid resource. A
    catalogue indexes it only after a paid call settles and echoes it, so
    shipping it lists nothing by itself; listing stays an owner decision."""
    shapes = {
        "grade": ({"session_id": "rg_s_..."},
                  {"session_id": {"type": "string",
                                  "description": "A session whose result bundle was accepted: "
                                                 "POST /riggrade/v1/session, run the open "
                                                 "container, POST the bundle, then pay here."}},
                  {"labels": [{"grade": 2, "peer_class": "rtx4090-24", "workload": "inference",
                               "capped_by": "consistency"}],
                   "badges": ["Grade 2 \u00b7 RTX 4090 class \u00b7 Inference \u00b7 ..."],
                   "evidence_pack": {}},
                  ["gpu", "benchmark", "verification", "grading"]),
        "fixit": ({"preflight_report": {"preflight": True, "findings": ["..."]}},
                  {"preflight_report": {"type": "object",
                                        "description": "The preflight-report.json the free "
                                                       "RigGrade preflight wrote on the box."}},
                  {"report": {"items": [{"check": "gpu0.clocks_under_load", "cause": "...",
                                         "fix": ["..."], "who": "host"}]}},
                  ["gpu", "diagnosis", "host", "preflight"]),
        "cluster": ({"evidence_pack": {"claims": {}, "nodes": ["..."], "checks": ["..."],
                                       "verdict": "accept"}},
                    {"evidence_pack": {"type": "object",
                                       "description": "The acceptance.json runner.cluster "
                                                      "evaluate wrote for the rented nodes."}},
                    {"certificate": {"certificate_id": "rc_...", "verdict": "accept",
                                     "signature": "..."}, "lookup_url": "..."},
                    ["gpu", "cluster", "acceptance", "verification"]),
    }
    example_in, props, example_out, tags = shapes[resource]
    return {
        "info": {"input": {"type": "http", "method": "POST", "bodyType": "json",
                           "body": example_in},
                 "output": {"type": "application/json", "example": example_out}},
        "schema": {"type": "object", "properties": props,
                   "required": list(props), "additionalProperties": False},
        "serviceName": "RigGrade",
        "tags": tags,
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
