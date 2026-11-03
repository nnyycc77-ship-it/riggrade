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
import datetime
import json
from urllib.parse import urlsplit
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
from . import pricecheck
from . import acceptance
from . import index as price_index
from . import certificate_page
from . import checkout
from . import landing
from . import rental
from . import signing
from . import pool
from . import ledger
from . import hostcert
from . import record

PREFIX = "/riggrade/v1"
X402_VERSION = 2
NETWORK_BASE = "eip155:8453"
PAID_ROUTES = {("grade",): "grade", ("fixit",): "fixit", ("cluster", "acceptance"): "cluster",
               ("ledger",): "ledger", ("host", "certificate"): "hostcert", ("price", "check"): "pricecheck"}
V1_REFUSED = ("x402 v1 (X-PAYMENT) is not accepted; send PAYMENT-SIGNATURE with "
              "x402Version 2, as the PAYMENT-REQUIRED header of this answer describes")
# /.well-known/agent-card.json is the A2A card's current path (agent.json is
# the older one; both serve the same card). robots.txt allows everything.
AGENT_CARD_PATHS = ("/.well-known/agent.json", "/.well-known/agent-card.json")
DISCOVERY_PATHS = ("/", "/favicon.ico", "/favicon.png", "/favicon.svg", *AGENT_CARD_PATHS, "/robots.txt",
                   signing.KEYS_PATH, "/llms.txt", "/openapi.json", PREFIX + "/openapi.json")
MAX_BODY = 4 * 1024 * 1024
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
# Decided prices (plan Revision 5, decided items 1), USDC atomic units.
POINT_PRICE_ATOMIC = {DATA_CENTRE: 5_000_000, "consumer": 3_000_000}
# Products beyond the point grade (decided 1 Nov 2026; each is on
# sale only when RIGGRADE_PRODUCTS names it). USDC atomic units.
# The price check, 0.01 USDC: owner decision, 3 Nov 2026 (Adviser 1's chat).
PRODUCT_PRICE_ATOMIC = {"fixit": 2_000_000, "cluster": 25_000_000, "pricecheck": 10_000}
# Products a free trial may cover (owner decision 2 Nov 2026, set only once
# the coordinator records his confirmation): the host fix-it report and the
# point grade. Trial grades are verified on a 0 USDC authorisation, never
# settled, logged as free_trial, and never added to the reference set.
TRIAL_ELIGIBLE = ("fixit", "grade")


def _end_of_day(raw):
    """A UTC date string as the epoch second ending that day, or None."""
    try:
        day = datetime.date.fromisoformat(raw.strip())
    except (AttributeError, ValueError):
        return None
    return int(datetime.datetime(day.year, day.month, day.day, 23, 59, 59,
                                 tzinfo=datetime.timezone.utc).timestamp())
# Every paid resource description opens with a "Use when" line: catalogues
# show it as the one-line reason to call, and most listings lack one.
USE_WHEN = {
    "grade": "Use when a rented or hosted GPU rig must be graded against published bars for "
             "its peer class before you rely on it or list it.",
    "fixit": "Use when the free RigGrade preflight reported a warn or fail and you need the "
             "cause, the fix steps and who can act (host or renter).",
    "cluster": "Use when a rented multi-node GPU cluster must pass an independent acceptance "
               "test before a long run, a handover or a dispute.",
    "ledger": "Use when an agent needs the compute supply ledger's weekly listed-price series per "
              "GPU class, the derived figures with their bands, and the signal states, as one "
              "machine-readable document with its method and posture attached.",
    "hostcert": "Use when a host wants its own graded rig's measured facts re-issued as a signed, "
                "dated certificate with a public verify page and a badge for its own pages.",
    "pricecheck": "Use when you hold a quote for renting a GPU class and want to know where its hourly "
                  "price sits against this week's listed on-demand prices for that class.",
}
PRODUCT_NOTES = {
    "pricecheck": USE_WHEN["pricecheck"] + " Price check, 0.01 USDC: POST /riggrade/v1/price/check with "
                  "{peer_class, usd_per_gpu_hour}; returns the latest week's median, provider median, minimum "
                  "and maximum for the class, where the price sits among them, its gap to the median and the "
                  "last eight weekly medians. Listed asks, not paid prices; information, not advice. The same "
                  "weekly figures are free at GET /riggrade/v1/index.",
    "fixit": USE_WHEN["fixit"] + " Host fix-it report, 2 USDC: POST /riggrade/v1/fixit with "
             "{preflight_report}; for every warn or fail finding, the likely cause, the fix "
             "steps and who can act. Free preview first; nothing is sold when every finding passed.",
    "cluster": USE_WHEN["cluster"] + " Cluster acceptance certificate, 25 USDC: POST "
               "/riggrade/v1/cluster/acceptance with {evidence_pack} (the acceptance.json "
               "runner.cluster wrote); the server recomputes every check, refuses a pack that "
               "does not recompute, and issues a signed, stored certificate. GET "
               "/riggrade/v1/cluster/acceptance/{id} returns it. The certificate carries an "
               "Ed25519 signature verifiable offline with the public key at "
               "/.well-known/riggrade-keys.json (pip install riggrade; riggrade-verify).",
    "ledger": USE_WHEN["ledger"] + " Compute supply ledger feed: POST /riggrade/v1/ledger with "
              "{weeks} (1 to 52) and optional {classes}; returns the link 4 window per class, "
              "implied gross revenue per GPU-year at stated bands (arithmetic, listed not paid), "
              "every section 4 signal with its state and missing series, provenance, and the "
              "four posture statements first and last. Information, not advice, an offer, a rate "
              "or a forecast. Free weekly note in the repository; the feed is the per-call form.",
    "hostcert": USE_WHEN["hostcert"] + " Host certificate: POST /riggrade/v1/host/certificate with "
                "{session_id} of a graded session, paid by the wallet that paid the grade; where that "
                "wallet is not the session's registered host wallet, the host first consents with a "
                "0 USDC authorisation at POST /riggrade/v1/host/consent. GET /riggrade/v1/host/{id} "
                "returns the certificate (JSON, or the verify page), /badge.svg the badge. Measured, not "
                "promised; declared runner printed as declared, not observed; grade kind and evidence "
                "tag on certificate, page and badge. No marketplace is recorded as permitting the badge "
                "in its listings; it is for the host's own pages.",
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
        # The free price index refreshes from this URL (packaged copy otherwise).
        self.index_url = env.get("RIGGRADE_INDEX_URL", "").strip() or None
        # Pooled preflight results (owner decision 2 Nov 2026): collection on
        # when a path is set ("off" disables); publication of the aggregates
        # only when RIGGRADE_POOL_PUBLISH=1, after the method's review.
        raw_pool = env.get("RIGGRADE_PREFLIGHT_POOL", "").strip()
        self.preflight_pool = raw_pool if raw_pool and raw_pool.lower() != "off" else None
        self.pool_publish = env.get("RIGGRADE_POOL_PUBLISH", "").strip() == "1"
        # GPU record (docs/gpu-record-method.md): the append-only log path;
        # unset or "off" keeps the record closed until the method is accepted.
        raw_record = env.get("RIGGRADE_RECORD", "").strip()
        self.record_path = raw_record if raw_record and raw_record.lower() != "off" else None
        # Prior certificate-signing public keys (base64, comma-separated) stay
        # published after a secret rotation so older certificates still verify.
        self.prior_public_keys = [k for k in env.get("RIGGRADE_PRIOR_PUBLIC_KEYS", "").split(",") if k.strip()]
        self.products = {p.strip() for p in env.get("RIGGRADE_PRODUCTS", "").split(",") if p.strip()}
        # The ledger feed has no decided price: it sells only once the owner
        # sets one here (USDC atomic units) and names it in RIGGRADE_PRODUCTS.
        raw_price = env.get("RIGGRADE_LEDGER_PRICE_ATOMIC", "").strip()
        self.ledger_price = int(raw_price) if raw_price.isdigit() and int(raw_price) > 0 else None
        raw_hc = env.get("RIGGRADE_HOSTCERT_PRICE_ATOMIC", "").strip()
        self.hostcert_price = int(raw_hc) if raw_hc.isdigit() and int(raw_hc) > 0 else None
        # Launch window (owner decision, recorded by the coordinator before it
        # is set): price 0 with an end date means the certificate issues on a
        # 0 USDC authorisation, verified and never settled, until that date
        # (inclusive, UTC). After it the product is off again until a price
        # is set. Any other zero or missing price keeps the product off.
        raw_until = env.get("RIGGRADE_HOSTCERT_FREE_UNTIL", "").strip()
        self.hostcert_free_until = _end_of_day(raw_until) if raw_hc == "0" and raw_until else None
        # Free trial: named eligible products at 0 USDC until the end date
        # (inclusive, UTC); after it they return to their prices on their own.
        self.trial_products = {p.strip() for p in env.get("RIGGRADE_TRIAL_PRODUCTS", "").split(",")
                               if p.strip() in TRIAL_ELIGIBLE}
        self.trial_until = _end_of_day(env.get("RIGGRADE_TRIAL_UNTIL", "")) if self.trial_products else None

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
        self.index = price_index.Index(url=self.config.index_url)
        self.signer = signing.Signer.from_secret(self.config.secret)
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
        if self.product_price(name) is None:
            if name == "hostcert" and self.config.hostcert_free_until is not None:
                reasons.append("the free launch window for hostcert ended on "
                               f"{self.free_until_text()}; no price is set")
            else:
                reasons.append(f"no price is set for {name} (RIGGRADE_{name.upper()}_PRICE_ATOMIC)")
        return reasons

    def _listed(self, name):
        """A product enters the discovery documents only while it is on sale,
        so crawlers never probe a route that answers 503."""
        return not self.product_reasons_off(name)

    def trial_active(self, name):
        until = self.config.trial_until
        return name in self.config.trial_products and until is not None and self.clock() <= until

    def free_until_text(self, name="hostcert"):
        until = self.config.trial_until if name in TRIAL_ELIGIBLE else self.config.hostcert_free_until
        return datetime.datetime.fromtimestamp(until, datetime.timezone.utc).strftime("%Y-%m-%d") if until else None

    def free_note(self, name):
        """The sentence a 402 or the descriptor adds while a product is free."""
        if name == "hostcert":
            return (f"Free launch window until {self.free_until_text(name)} (UTC): "
                    "send a 0 USDC x402 authorisation; it is verified and never settled.")
        return (f"Free trial until {self.free_until_text(name)} (UTC): send a 0 USDC x402 "
                "authorisation; it is verified and never settled. Results during the trial are "
                "marked free_trial" + ("" if name != "grade" else " and are not added to the reference set") + ".")

    def product_price(self, name):
        """USDC atomic units, or None while the owner has set no price."""
        if name == "ledger":
            return self.config.ledger_price
        if name == "hostcert":
            until = self.config.hostcert_free_until
            if until is not None:
                return 0 if self.clock() <= until else None
            return self.config.hostcert_price
        if self.trial_active(name):
            return 0
        return PRODUCT_PRICE_ATOMIC.get(name)

    def priced_products(self):
        return sorted(name for name in list(PRODUCT_PRICE_ATOMIC) + ["ledger", "hostcert"]
                      if self.product_price(name) is not None)

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET")
        if method == "HEAD":
            # Same status and headers as the GET (Content-Length included),
            # no body: crawlers HEAD the favicon and documents.
            for _chunk in self(dict(environ, REQUEST_METHOD="GET"), start_response):
                pass
            return []
        if method == "GET" and path in DISCOVERY_PATHS:
            return self.discovery(start_response, environ, path)
        if not path.startswith(PREFIX):
            return _json(start_response, "404 Not Found", {"error": "not_found"})
        rest = path[len(PREFIX):].strip("/").split("/")
        try:
            if rest == [""] and method == "GET":
                return _json(start_response, "200 OK", self.descriptor())
            if rest == ["checkout"] and method == "GET":
                return self.checkout_page(start_response)
            if rest == ["index", "quality"] and method == "GET":
                return self.pool_quality(start_response)
            if method == "GET" and len(rest) == 2 and rest[0] == "record":
                return self.record_lookup(start_response, rest[1])
            if rest == ["index"] and method == "GET":
                # Free: the reconstructed weekly listed-price series per class.
                return _json(start_response, "200 OK", price_index.latest(self.index.current()),
                             [("Cache-Control", "public, max-age=3600")])
            if method == "GET" and len(rest) in (2, 3) and rest[0] == "host" and rest[1].startswith("rh_") \
                    and (len(rest) == 2 or rest[2] == "badge.svg"):
                return self.host_lookup(start_response, environ, rest[1], badge=len(rest) == 3)
            if method == "GET" and len(rest) == 3 and rest[:2] == ["cluster", "acceptance"]:
                return self.certificate_lookup(start_response, rest[2], environ)
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
            if rest == ["rental", "plan"]:
                return _json(start_response, "200 OK", rental.plan(body))
            if rest == ["rental", "check"]:
                rows = record.read(self.config.record_path) if self.config.record_path else []
                return _json(start_response, "200 OK", rental.check(body, rows, int(self.clock())))
            if rest == ["preflight", "share"]:
                return self.preflight_share(start_response, body)
            if rest == ["record"]:
                return self.record_submit(start_response, body)
            if rest == ["fixit"]:
                return self.fixit_report(start_response, environ, body)
            if rest == ["cluster", "acceptance"]:
                return self.cluster_acceptance(start_response, environ, body)
            if rest == ["ledger"]:
                return self.ledger_feed(start_response, environ, body)
            if rest == ["price", "check"]:
                return self.price_check(start_response, environ, body)
            if rest == ["host", "consent"]:
                return self.host_consent(start_response, environ, body)
            if rest == ["host", "certificate"]:
                return self.host_certificate(start_response, environ, body)
        except (BadRequest, rental.BadRequest, pricecheck.BadRequest) as exc:
            return _json(start_response, "400 Bad Request", {"error": str(exc), "charged": False})
        except SessionError as exc:
            return _json(start_response, "409 Conflict", {"error": str(exc), "charged": False})
        return _json(start_response, "404 Not Found", {"error": "not_found"})

    def discovery(self, start_response, environ, path):
        """Read-only documents for agents and crawlers (riggrade.discovery)."""
        base = _url(environ, "")
        if path in discovery.ICONS:
            # Crawlers ask for .ico, .png and .svg in turn; a 404 on any of
            # them is logged as a broken site by some directories.
            ctype, data = discovery.ICONS[path]
            start_response("200 OK", [("Content-Type", ctype), ("Content-Length", str(len(data))),
                                      ("Cache-Control", "public, max-age=86400")])
            return [data]
        if path == "/llms.txt":
            data = discovery.llms_txt(base, PREFIX, self.on_sale, self._listed("pricecheck")).encode()
            start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8"),
                                      ("Content-Length", str(len(data)))])
            return [data]
        if path in AGENT_CARD_PATHS:
            return _json(start_response, "200 OK", discovery.agent_card(base, PREFIX, self.on_sale, self._listed("pricecheck")))
        if path == "/robots.txt":
            data = discovery.robots_txt(base).encode()
            start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8"),
                                      ("Content-Length", str(len(data)))])
            return [data]
        if path == signing.KEYS_PATH:
            return _json(start_response, "200 OK",
                         signing.keys_document(self.signer, self.config.prior_public_keys),
                         [("Cache-Control", "public, max-age=3600")])
        if path in ("/openapi.json", PREFIX + "/openapi.json"):
            return _json(start_response, "200 OK",
                         discovery.openapi(base, PREFIX, self.config.contact_email,
                                           pricecheck=self._listed("pricecheck")))
        if path == "/" and certificate_page.wants_html(environ):
            return self.landing_page(start_response)
        return _json(start_response, "200 OK",
                     {"product": "RigGrade", "descriptor": base + PREFIX,
                      "openapi": base + PREFIX + "/openapi.json",
                      "agent_card": base + "/.well-known/agent.json", "llms": base + "/llms.txt"})

    def descriptor(self):
        sale = {"on_sale": self.on_sale}
        if not self.on_sale:
            sale["reasons_off"] = self.config.reasons_off(self.adapter)
        # Which facilitators the running service holds, hosts only, in call
        # order, so a deploy can be verified from outside. Never any key.
        bases = getattr(self.adapter, "facilitators", None)
        if bases:
            sale["facilitators"] = [urlsplit(b).netloc for b in bases]
        references = {cid: {w: self.references.basis(cid, w) for w in WORKLOAD_CLASSES}
                      for cid in PEER_CLASSES}
        return {"product": "RigGrade", "bench_version": BENCH_VERSION, "checkout": PREFIX + "/checkout",
                "spec": "docs/grading-spec.md", "statement": STATEMENT,
                "point_grade_price_usdc": {"data_centre": "5", "consumer": "3"},
                **({"point_grade_free_trial": {"until": self.free_until_text("grade"),
                                               "note": self.free_note("grade")}}
                   if self.trial_active("grade") else {}),
                "free_reference_sessions": FREE_REFERENCE_NOTE,
                "products": {name: dict({"price_usdc": _usdc(self.product_price(name)),
                                         "on_sale": not self.product_reasons_off(name),
                                         "note": PRODUCT_NOTES[name]},
                                        **({"free_until": self.free_until_text(name),
                                            "launch": "free launch window: issued on a 0 USDC "
                                                      "authorisation, verified and never settled"}
                                           if name == "hostcert" and self.product_price(name) == 0 else {}),
                                        **({"free_until": self.free_until_text(name),
                                            "trial": self.free_note(name)}
                                           if self.trial_active(name) else {}))
                             for name in self.priced_products()},
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
        # Decided 1 Nov 2026: while a class is still building its
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
        trial = self.trial_active("grade")
        amount = 0 if trial else POINT_PRICE_ATOMIC[tier] * len(payable)
        requirement = self._accept(amount)
        path = resource_path or PREFIX + "/session/%s/grade" % sid
        description = ("RigGrade point grade, %d workload class(es)%s"
                       % (len(payable), "; reference-building included free: "
                          + ", ".join(free) if free else ""))
        if trial:
            description = description + ". " + self.free_note("grade")
        header = self._payment_header(environ)
        if not header:
            return self._payment_required(start_response, environ, path, amount, description,
                                          bazaar=bazaar_extension(),
                                          error=self._v1_error(environ), preview=prev)
        if trial:
            # Verified like the free-session claim, never settled, screened.
            verify = getattr(self.adapter, "verify_only", None)
            result = verify(header, requirement) if verify else {"ok": False, "error": "claims not supported",
                                                                 "charged": False}
            if result.get("ok"):
                result = dict(result, charged=False)
        else:
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
                        "facilitator": result.get("facilitator"), "bazaar": result.get("bazaar"),
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
                                     host_paid=host_paid, operator=operator,
                                     record_reference=not trial)
        pack["paying_wallet"] = result.get("payer")
        if trial:
            pack["free_trial"] = True
        pack["operator"] = operator
        session["grade"] = {"labels": labels, "pack": pack, "payer": result.get("payer"),
                            "host_paid": host_paid, "at": self.clock()}
        badges = [badge_text(lab, "") for lab in labels]
        if lint(badges):
            raise RuntimeError("promise words in badge text: %s" % lint(badges))
        session["graded"] = True
        self.store.put(sid, session)
        salelog.append(self.config.sale_log,
                       {"product": "riggrade_point_grade", "session_id": sid,
                        "amount_atomic": requirement["amount"],
                        **({"status": "free_trial"} if trial else {}),
                        "paid_workloads": payable, "free_workloads": free,
                        "payer": result.get("payer"), "tx": result.get("tx"),
                        "facilitator": result.get("facilitator"), "bazaar": result.get("bazaar"),
                        "at": self.clock()})
        extra = [("PAYMENT-RESPONSE", result.get("receipt_header", ""))]
        return _json(start_response, "200 OK",
                     {"labels": labels, "badges": badges, "evidence_pack": pack},
                     extra)

    def discovery_402(self, start_response, environ):
        description = (USE_WHEN["grade"] + " RigGrade point grade: 3 USDC per workload class "
                       "for consumer cards, 5 USDC for data-centre cards. Open a session first "
                       "(POST /session), run the pinned container, submit the bundle, then POST "
                       "here with {session_id}; the 402 for that session states the exact amount.")
        if self.trial_active("grade"):
            description = description + " " + self.free_note("grade")
        return self._payment_required(start_response, environ, PREFIX + "/grade",
                                      0 if self.trial_active("grade") else POINT_PRICE_ATOMIC["consumer"],
                                      description,
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
        path = {"fixit": PREFIX + "/fixit", "cluster": PREFIX + "/cluster/acceptance",
                "ledger": PREFIX + "/ledger", "hostcert": PREFIX + "/host/certificate",
                "pricecheck": PREFIX + "/price/check"}[name]
        return self._payment_required(start_response, environ, path, self.product_price(name),
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
                # EIP-712 domain of USDC on Base, needed to check the signature,
                # and the transfer method stated rather than left to the
                # scheme default (exact EVM spec: absent means eip3009; a
                # Permit2-only client then skips this entry instead of
                # guessing). Permit2 payloads are refused before settlement
                # because the payer is read from authorization.from.
                "extra": {"name": "USD Coin", "version": "2", "assetTransferMethod": "eip3009"}}

    def _payment_required(self, start_response, environ, path, amount, description,
                          bazaar=None, error=None, **body_extra):
        """x402 v2 challenge: the PaymentRequired object goes base64 in the
        PAYMENT-REQUIRED header (the canonical carrier) and, for people and
        older tooling, as the JSON body too, with charged false."""
        # Service name and tags live on the resource object (bazaar extension
        # spec), per product, at most five tags.
        product = PAID_ROUTES.get(tuple(path[len(PREFIX):].strip("/").split("/")))
        doc = {"x402Version": X402_VERSION,
               "resource": {"url": _url(environ, path), "description": description,
                            "mimeType": "application/json", "serviceName": "RigGrade",
                            "tags": RESOURCE_TAGS.get(product, RESOURCE_TAGS["grade"])},
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
        amount = self.product_price(name)
        requirement = self._accept(amount)
        header = self._payment_header(environ)
        if amount == 0:
            description = description + " " + self.free_note(name)
        if not header:
            return self._payment_required(start_response, environ, path, amount,
                                          description, bazaar=bazaar_extension(name),
                                          error=self._v1_error(environ), preview=preview_doc)
        if amount == 0:
            # A free launch issue: verified like the free-session claim, never
            # settled, screened like a payer, logged with amount 0.
            verify = getattr(self.adapter, "verify_only", None)
            result = verify(header, requirement) if verify else {"ok": False, "error": "claims not supported",
                                                                 "charged": False}
            if result.get("ok"):
                result = dict(result, charged=False, free_launch=True)
        else:
            result = self.adapter.settle(header, requirement)
        product = {"fixit": fixit.PRODUCT, "cluster": acceptance.PRODUCT, "ledger": ledger.PRODUCT,
                   "hostcert": hostcert.PRODUCT, "pricecheck": pricecheck.PRODUCT}[name]
        if not result.get("ok") and result.get("charged") in (True, "unknown"):
            salelog.append(self.config.sale_log,
                           {"product": product, "status": "charged_without_delivery",
                            "charged": result.get("charged"),
                            "amount_atomic": requirement["amount"],
                            "payer": result.get("payer"), "tx": result.get("tx"),
                        "facilitator": result.get("facilitator"), "bazaar": result.get("bazaar"),
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
                        "facilitator": result.get("facilitator"), "bazaar": result.get("bazaar"),
                            "error": "delivery failed: %s: %s" % (type(exc).__name__, exc),
                            "at": self.clock()})
            return _json(start_response, "500 Internal Server Error",
                         {"error": "charged but delivery failed; recorded for refund by hand",
                          "charged": True, "tx": result.get("tx")})
        salelog.append(self.config.sale_log,
                       dict({"product": product, "amount_atomic": requirement["amount"],
                             **({"status": "free_launch" if name == "hostcert" else "free_trial"}
                                if result.get("free_launch") else {}),
                             "payer": result.get("payer"), "tx": result.get("tx"),
                        "facilitator": result.get("facilitator"), "bazaar": result.get("bazaar"),
                             "at": self.clock()}, **extra))
        return _json(start_response, "200 OK", doc,
                     [("PAYMENT-RESPONSE", result.get("receipt_header", ""))])

    def fixit_report(self, start_response, environ, body):
        report = body.get("preflight_report") if isinstance(body, dict) else None
        if (not isinstance(report, dict) or report.get("preflight") is not True
                or not isinstance(report.get("findings"), list)
                or not all(isinstance(f, dict) for f in report["findings"])):
            if not self._payment_header(environ):
                return self.probe(start_response, environ, "fixit")
            raise BadRequest("preflight_report (the preflight-report.json content) is required: "
                             "an object with preflight true and a findings list of "
                             "{check, status, detail} objects")
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
        role = (body.get("payer_role") if isinstance(body, dict) else None) or "undeclared"
        error = acceptance.validate(pack)
        if not error and role not in acceptance.PAYER_ROLES:
            error = "payer_role must be one of: " + ", ".join(acceptance.PAYER_ROLES)
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
            cert["payer_role"] = role
            cert["payer_role_note"] = acceptance.PAYER_ROLE_NOTE
            if self.signer is not None:
                cert["keys_url"] = _url(environ, signing.KEYS_PATH)   # the anchor, inside the signed body
            cert["signature"] = acceptance.sign(cert, self.config.secret)
            acceptance.attach_ed25519(cert, self.signer)
            self.store.put("cert:" + cert_id, cert)
            return ({"certificate": cert,
                     "lookup_url": _url(environ, PREFIX + "/cluster/acceptance/" + cert_id)},
                    {"certificate_id": cert_id, "verdict": result["verdict"]})
        return self._sell(start_response, environ, "cluster", PREFIX + "/cluster/acceptance",
                          description, prev, deliver)

    def ledger_feed(self, start_response, environ, body):
        """The compute supply ledger as one paid, machine-readable document
        (riggrade.ledger.feed). Reads the same index the free route serves;
        the free weekly note stays free. No price until the owner sets one."""
        weeks = body.get("weeks", ledger.DEFAULT_WEEKS) if isinstance(body, dict) else None
        classes = body.get("classes") if isinstance(body, dict) else None
        bad = (not isinstance(weeks, int) or isinstance(weeks, bool)
               or not 1 <= weeks <= ledger.MAX_WEEKS
               or (classes is not None and (not isinstance(classes, list)
                                            or not all(isinstance(c, str) for c in classes))))
        if bad:
            if not self._payment_header(environ):
                return self.probe(start_response, environ, "ledger")
            raise BadRequest(f"weeks must be an integer from 1 to {ledger.MAX_WEEKS}; classes, if "
                             "given, a list of peer class ids")
        doc = self.index.current()
        prev = ledger.preview(doc, weeks, classes)
        if prev["unknown_classes"] and not self.product_reasons_off("ledger"):
            return _json(start_response, "422 Unprocessable Entity",
                         {"error": "unknown peer class: " + ", ".join(prev["unknown_classes"]),
                          "known": sorted(doc.get("classes") or {}), "charged": False})
        description = (f"RigGrade compute supply ledger feed: {len(prev['classes'])} class(es), "
                       f"{weeks} week(s) of link 4, derived figures and signal states")

        def deliver(result):
            out = ledger.feed(doc, weeks, classes)
            out["paying_wallet"] = result.get("payer")
            return out, {"weeks": weeks, "classes": len(prev["classes"])}
        return self._sell(start_response, environ, "ledger", PREFIX + "/ledger", description,
                          prev, deliver)

    def price_check(self, start_response, environ, body):
        """0.01 USDC: where one asking price sits in the latest listed week
        for its class (riggrade.pricecheck). Bad input with no payment
        header is a probe; an uncovered class is refused before any charge."""
        try:
            cls, price = pricecheck.parse(body)
        except pricecheck.BadRequest:
            if not self._payment_header(environ):
                return self.probe(start_response, environ, "pricecheck")
            raise
        doc = self.index.current() or {}
        prev = pricecheck.preview(doc, cls)
        if not prev["covered"] and not self.product_reasons_off("pricecheck"):
            return _json(start_response, "422 Unprocessable Entity",
                         {"error": "no listed week for peer class " + cls, "preview": prev, "charged": False})
        description = f"RigGrade price check: {cls} at {price:g} USD per GPU-hour against week {prev['latest_week']}"

        def deliver(result):
            out = pricecheck.check(doc, cls, price)
            out["paying_wallet"] = result.get("payer")
            return out, {"peer_class": cls}
        return self._sell(start_response, environ, "pricecheck", PREFIX + "/price/check", description,
                          prev, deliver)

    # ------------------------------------------------------------ host certificate

    def _graded_session(self, body):
        sid = body.get("session_id") if isinstance(body, dict) else None
        if not isinstance(sid, str) or not sid:
            return None, None, "session_id is required"
        session = self.store.get(sid)
        if session is None or not isinstance(session.get("grade"), dict):
            return None, None, "no graded session with that id"
        return sid, session, None

    def host_consent(self, start_response, environ, body):
        """[261b-3] The registered host wallet consents to publication of a
        grade someone else paid for: a 0 USDC x402 authorisation naming the
        session, verified and never settled, screened like a payer."""
        sid, session, err = self._graded_session(body)
        if err:
            raise BadRequest(err)
        if not session.get("host_wallet"):
            return _json(start_response, "409 Conflict",
                         {"error": "this session registered no host wallet, so no host can consent",
                          "charged": False})
        wallet, refused = self._operator_claim(environ, sid)
        if wallet is None:
            return self._payment_required(start_response, environ, PREFIX + "/host/consent", "0",
                                          "RigGrade host consent: 0 USDC authorisation from the "
                                          "session's registered host wallet; verified, never settled.",
                                          error=refused)
        if wallet.lower() != session["host_wallet"].lower():
            return _json(start_response, "403 Forbidden",
                         {"error": "the authorisation is not from the session's registered host wallet",
                          "charged": False})
        session["host_consent"] = wallet
        self.store.put(sid, session)
        return _json(start_response, "200 OK", {"consented": True, "session_id": sid,
                                                "host_wallet": wallet, "charged": False})

    def host_certificate(self, start_response, environ, body):
        """Paid: the grade's facts re-issued as a signed certificate the
        host may publish (docs/host-certificate-method.md). Off until the
        owner sets a price; one certificate per session; consent rule
        before any settlement."""
        sid, session, err = self._graded_session(body)
        if err:
            if not self._payment_header(environ):
                return self.probe(start_response, environ, "hostcert")
            raise BadRequest(err)
        if self.product_reasons_off("hostcert"):
            return self._sell(start_response, environ, "hostcert", PREFIX + "/host/certificate",
                              "", {"session_id": sid}, None)
        if session.get("hostcert_id"):
            return _json(start_response, "409 Conflict",
                         {"error": "a certificate was already issued for this session: one per session",
                          "certificate_id": session["hostcert_id"], "charged": False})
        state = hostcert.consent_state(session, session["grade"])
        if state == "impossible":
            return _json(start_response, "403 Forbidden",
                         {"error": "this session registered no host wallet, so the rig's host cannot "
                                   "consent and the grade is not published", "charged": False})
        if state == "needed":
            return _json(start_response, "403 Forbidden",
                         {"error": "the grade was not paid by the session's registered host wallet and "
                                   "that wallet has not consented: POST /riggrade/v1/host/consent "
                                   "with a 0 USDC authorisation from it first", "charged": False})
        grade = session["grade"]
        claimed = self._header_payer(environ)
        if claimed and claimed.lower() != (grade.get("payer") or "").lower():
            return _json(start_response, "403 Forbidden",
                         {"error": "the certificate must be paid by the wallet that paid the grade",
                          "charged": False})
        description = ("RigGrade host certificate: the graded session's measured facts, signed, with "
                       "a verify page and a badge; declared runner printed as declared")

        def deliver(result):
            payer = (result.get("payer") or "").lower()
            if payer != (grade.get("payer") or "").lower():
                raise RuntimeError("the certificate must be paid by the wallet that paid the grade")
            cert_id = "rh_" + secrets.token_hex(8)
            keys_url = _url(environ, signing.KEYS_PATH) if self.signer is not None else None
            cert = hostcert.build(session, grade, self.clock(), cert_id, keys_url,
                                  self.config.secret, self.signer, paying_wallet=result.get("payer"))
            self.store.put("hostcert:" + cert_id, cert)
            ident = cert["rig_identity"]
            index = self.store.get("hostcert_rig:" + ident[0]) or []
            index.append({"certificate_id": cert_id, "issued_at": cert["issued_at"], "identity": ident})
            self.store.put("hostcert_rig:" + ident[0], index)
            session["hostcert_id"] = cert_id
            self.store.put(sid, session)
            base = _url(environ, PREFIX + "/host/" + cert_id)
            return ({"certificate": cert, "lookup_url": base, "badge_url": base + "/badge.svg"},
                    {"certificate_id": cert_id, "session_id": sid, "consent": state})
        return self._sell(start_response, environ, "hostcert", PREFIX + "/host/certificate",
                          description, {"session_id": sid, "consent": state}, deliver)

    @staticmethod
    def _header_payer(environ):
        """The payer a PAYMENT-SIGNATURE header names (authorization.from),
        read locally before any settlement; None when unreadable."""
        header = environ.get("HTTP_PAYMENT_SIGNATURE")
        if not header:
            return None
        try:
            payload = json.loads(base64.b64decode(header))
            payer = ((payload.get("payload") or {}).get("authorization") or {}).get("from")
        except (ValueError, AttributeError, TypeError):
            return None
        return payer if isinstance(payer, str) and WALLET.match(payer) else None

    def _superseded_by(self, environ, cert):
        """[261b-4] A later certificate for the same recorded rig identity,
        else None. Identity differing in any field: no link, nothing said."""
        ident = cert.get("rig_identity") or []
        if not ident or ident[0] == "rg_unknown":
            return None
        later = [e for e in (self.store.get("hostcert_rig:" + ident[0]) or [])
                 if e.get("identity") == ident and e.get("issued_at", 0) > cert.get("issued_at", 0)
                 and e.get("certificate_id") != cert.get("certificate_id")]
        if not later:
            return None
        newest = max(later, key=lambda e: e["issued_at"])
        return {"certificate_id": newest["certificate_id"],
                "url": _url(environ, PREFIX + "/host/" + newest["certificate_id"])}

    def host_lookup(self, start_response, environ, cert_id, badge=False):
        cert = self.store.get("hostcert:" + cert_id)
        if cert is None:
            return _json(start_response, "404 Not Found", {"error": "no such certificate"})
        base = _url(environ, PREFIX + "/host/" + cert_id)
        if badge:
            date_text = certificate_page._when((cert.get("run") or {}).get("graded_at"))
            data = hostcert.badge_svg(cert, base, date_text).encode("utf-8")
            start_response("200 OK", [("Content-Type", "image/svg+xml"), ("Content-Length", str(len(data))),
                                      ("Cache-Control", "public, max-age=3600")])
            return [data]
        valid = acceptance.verify(cert, self.config.secret)
        ed_valid = acceptance.verify_ed25519(cert)
        keys_url = _url(environ, signing.KEYS_PATH)
        sup = self._superseded_by(environ, cert)
        if certificate_page.wants_html(environ):
            data = hostcert.render(cert, valid, base + "?format=json", ed_valid, keys_url, sup).encode("utf-8")
            start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"),
                                      ("Content-Length", str(len(data)))])
            return [data]
        return _json(start_response, "200 OK", {"certificate": cert, "signature_valid": valid,
                                                "ed25519_valid": ed_valid, "public_keys_url": keys_url,
                                                "superseded_by": sup, "badge_url": base + "/badge.svg"})

    def _price_line(self, name, normal):
        """The price as a person should read it today, from the live configuration."""
        off = (not self.on_sale) if name == "grade" else bool(self.product_reasons_off(name))
        if name == "hostcert" and self.config.hostcert_free_until is not None:
            if off:
                return "not on sale right now"
            return f"free until {self.free_until_text('hostcert')} (UTC); price after that not set yet"
        if off:
            return "not on sale right now"
        if self.trial_active(name):
            return f"free until {self.free_until_text(name)} (UTC), then {normal}"
        return normal

    def landing_page(self, start_response):
        """The human page at "/" (riggrade.landing); prices from live config."""
        def usd(atomic):
            return f"{atomic / 1_000_000:g} USDC"
        offers = [
            ("Fix-it report", self._price_line("fixit", usd(PRODUCT_PRICE_ATOMIC["fixit"])),
             "For each warn or fail: the likely cause, the fix steps and who can act (host or renter).",
             "Upload preflight-report.json at the checkout."),
            ("Cluster acceptance certificate", self._price_line("cluster", usd(PRODUCT_PRICE_ATOMIC["cluster"])),
             ("The service recomputes every check from your multi-node test and issues a certificate with an "
              "Ed25519 signature anyone can check offline against the published key."),
             "Run the acceptance test, then upload acceptance.json at the checkout."),
            ("Point grade of a rig", self._price_line(
                "grade", f"{usd(POINT_PRICE_ATOMIC['consumer'])} per workload class (consumer cards), "
                         f"{usd(POINT_PRICE_ATOMIC[DATA_CENTRE])} (data-centre cards)"),
             "A label against fixed, published bars for the card's class, with its evidence pack.",
             "Run the pinned benchmark container (see the grading spec)."),
            ("Host certificate", self._price_line("hostcert", "price not set"),
             ("For hosts: a graded rig's measured facts as a certificate with a public verify page and a badge "
              "for your own pages."), "After a graded session."),
        ]
        page, csp = landing.render(PREFIX, offers)
        data = page.encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(data))),
                                  ("Content-Security-Policy", csp), ("X-Content-Type-Options", "nosniff"),
                                  ("Cache-Control", "public, max-age=300")])
        return [data]

    def checkout_page(self, start_response):
        """The browser checkout (riggrade.checkout): the same paid routes, paid
        from a person's own wallet. Holds no price or address of its own."""
        page, csp = checkout.render(PREFIX)
        data = page.encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(data))),
                                  ("Content-Security-Policy", csp), ("X-Content-Type-Options", "nosniff"),
                                  ("Referrer-Policy", "no-referrer"), ("Cache-Control", "no-store")])
        return [data]

    def preflight_share(self, start_response, body):
        """Opt-in intake of an anonymised preflight summary. Stores the
        cleaned record only (riggrade.pool.clean); never the sender."""
        if not self.config.preflight_pool:
            return _json(start_response, "503 Service Unavailable",
                         {"accepted": False, "error": "the preflight pool is closed on this service"})
        try:
            rec = pool.clean(body, self.clock() // 3600 * 3600)
        except ValueError as exc:
            raise BadRequest("not a shareable preflight summary: %s" % exc)
        pool.append(self.config.preflight_pool, rec)
        return _json(start_response, "200 OK",
                     {"accepted": True, "stored": rec,
                      "consent": pool.CONSENT,
                      "note": "Only this record is kept: no identifier of the box, the host or you. "
                              "Aggregates per peer class publish at GET /index/quality once the "
                              "class has %d records and the method is reviewed." % pool.MIN_N})

    def record_submit(self, start_response, body):
        """Consented entry for the GPU record (riggrade.record.clean); free;
        closed until RIGGRADE_RECORD names the log."""
        if not self.config.record_path:
            return _json(start_response, "503 Service Unavailable",
                         {"accepted": False, "error": "the GPU record is closed on this service: "
                                                      "its method is under review"})
        try:
            entry = record.clean(body, self.clock())
        except ValueError as exc:
            raise BadRequest("not a record entry: %s" % exc)
        record.append(self.config.record_path, entry)
        return _json(start_response, "200 OK",
                     {"accepted": True, "held": entry["held"], "stored": entry, "consent": record.CONSENT,
                      "lookup": [PREFIX + "/record/" + c["card_id"] for c in entry["cards"]]})

    def record_lookup(self, start_response, card_id):
        if not self.config.record_path:
            return _json(start_response, "503 Service Unavailable",
                         {"error": "the GPU record is closed on this service: its method is under review"})
        if not record.CARD_ID.match(card_id):
            return _json(start_response, "404 Not Found", {"error": "no such card"})
        rows = record.read(self.config.record_path)
        doc = record.history(rows, card_id)
        if not doc["records"] and not doc["held"]:
            return _json(start_response, "404 Not Found", {"error": "no record for this card", "card_id": card_id})
        return _json(start_response, "200 OK", dict(doc, method=record.METHOD_VERSION),
                     [("Cache-Control", "public, max-age=300")])

    def pool_quality(self, start_response):
        """Per-class aggregates of the pooled preflights; gated until the
        method is reviewed (RIGGRADE_POOL_PUBLISH=1)."""
        if not self.config.pool_publish:
            return _json(start_response, "503 Service Unavailable",
                         {"error": "the preflight quality series is not yet published: its method is under review",
                          "method": "https://github.com/nnyycc77-ship-it/riggrade/blob/main/docs/preflight-pool-method.md"})
        rows = pool.read(self.config.preflight_pool) if self.config.preflight_pool else []
        return _json(start_response, "200 OK",
                     {"series": "preflight-pool", "min_n": pool.MIN_N, "unit": pool.UNIT,
                      "collection_began": pool.COLLECTION_BEGAN,
                      "classes": pool.aggregate(rows),
                      "note": "Counts are records, not rigs or hosts: rows carry no identifier, so repeat "
                              "shares from one sender cannot be told apart. Self-reported, opt-in, subject "
                              "to opt-in bias. Information, not a grade; nothing here enters a grade rule."},
                     [("Cache-Control", "public, max-age=3600")])

    def certificate_lookup(self, start_response, cert_id, environ=None):
        """JSON by default; a readable, printable page (the dispute copy)
        for a browser or ?format=html. Same stored object either way."""
        cert = self.store.get("cert:" + cert_id) if cert_id.startswith("rc_") else None
        if cert is None:
            return _json(start_response, "404 Not Found", {"error": "no such certificate"})
        valid = acceptance.verify(cert, self.config.secret)
        ed_valid = acceptance.verify_ed25519(cert)
        keys_url = _url(environ, signing.KEYS_PATH) if environ is not None else signing.KEYS_PATH
        if environ is not None and certificate_page.wants_html(environ):
            json_url = _url(environ, PREFIX + "/cluster/acceptance/" + cert_id) + "?format=json"
            data = certificate_page.render(cert, valid, json_url, ed_valid, keys_url).encode("utf-8")
            start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"),
                                      ("Content-Length", str(len(data)))])
            return [data]
        return _json(start_response, "200 OK", {"certificate": cert, "signature_valid": valid,
                                                "ed25519_valid": ed_valid, "public_keys_url": keys_url})

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
        session["grade"] = {"labels": labels, "pack": pack, "payer": operator,
                            "host_paid": bool(session.get("host_wallet"))
                            and operator.lower() == session["host_wallet"].lower(),
                            "at": self.clock()}
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


RESOURCE_TAGS = {"grade": ["gpu", "benchmark", "verification", "grading", "x402"],
                 "fixit": ["gpu", "diagnosis", "host", "preflight", "x402"],
                 "cluster": ["gpu", "cluster", "acceptance", "verification", "x402"],
                 "ledger": ["gpu", "pricing", "market-data", "compute", "x402"],
                 "hostcert": ["gpu", "host", "certificate", "verification", "x402"],
                 "pricecheck": ["gpu", "pricing", "price-check", "market-data", "x402"]}


def _usdc(atomic):
    """Atomic units as a plain USDC string: 2000000 -> "2", 500000 -> "0.5"."""
    whole, frac = divmod(int(atomic), 1_000_000)
    return str(whole) if not frac else f"{whole}.{frac:06d}".rstrip("0")


def bazaar_extension(resource="grade"):
    """x402 Bazaar discovery metadata for a stable paid resource. A
    catalogue indexes it only after a paid call settles and echoes it, so
    shipping it lists nothing by itself; listing stays an operator decision."""
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
        "ledger": ({"weeks": 8},
                   {"weeks": {"type": "integer", "minimum": 1, "maximum": 52,
                              "description": "Weeks of the listed-price series per class to "
                                             "return, newest last."}},
                   {"read_this_first": ["..."], "classes": {"rtx4090-24": {"link4": ["..."],
                    "implied_gross_usd_per_gpu_year": {"bands": {}}}}, "signals": {},
                    "read_this_last": ["..."]},
                   ["gpu", "pricing", "market-data", "compute"]),
        "hostcert": ({"session_id": "rs_..."},
                     {"session_id": {"type": "string",
                                     "description": "A graded session of the host's own rig, paid by "
                                                    "this wallet; the host wallet consents first "
                                                    "where it is not the payer."}},
                     {"certificate": {"certificate_id": "rh_...", "grades": ["..."],
                                      "evidence_tag": "declared:host attestation:unattested"},
                      "lookup_url": "...", "badge_url": "..."},
                     ["gpu", "host", "certificate", "verification"]),
    }
    shapes["pricecheck"] = (
        {"peer_class": "h100-sxm-80", "usd_per_gpu_hour": 2.4},
        {"peer_class": {"type": "string", "description": "A peer class id from GET /riggrade/v1/index."},
         "usd_per_gpu_hour": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000,
                              "description": "The hourly price per GPU you were quoted."}},
        {"read_this_first": ["..."], "peer_class": "h100-sxm-80", "latest_week": "2026-10-26",
         "week": {"usd_per_gpu_hour_median": 2.5}, "position": "at the median (within 5%)",
         "vs_median_pct": -4.0, "recent_weeks": ["..."]},
        ["gpu", "pricing", "price-check", "market-data"])
    example_in, props, example_out, tags = shapes[resource]
    # "schema" is the JSON Schema of the "info" object (x402 bazaar extension):
    # readers take input.properties.body as the request schema and
    # output.properties.example as the response schema.
    body_schema = {"type": "object", "properties": props, "required": list(props),
                   "additionalProperties": False}
    return {
        "info": {"input": {"type": "http", "method": "POST", "bodyType": "json",
                           "body": example_in},
                 "output": {"type": "json", "example": example_out}},
        # Draft 2020-12, as the bazaar extension spec requires; the listed
        # items carry the $schema key, so this does too.
        "schema": {"$schema": "https://json-schema.org/draft/2020-12/schema",
                   "type": "object",
                   "properties": {
                       "input": {"type": "object",
                                 "properties": {"type": {"const": "http"},
                                                "method": {"const": "POST"},
                                                "bodyType": {"const": "json"},
                                                "body": body_schema},
                                 "required": ["type", "method", "bodyType", "body"]},
                       "output": {"type": "object",
                                  "properties": {"type": {"const": "json"},
                                                 "example": _schema_of(example_out)},
                                  "required": ["type", "example"]}},
                   "required": ["input", "output"]},
    }


def _schema_of(example):
    """A loose JSON Schema describing an example value's shape."""
    if isinstance(example, dict):
        return {"type": "object", "properties": {k: _schema_of(v) for k, v in example.items()}}
    if isinstance(example, list):
        return {"type": "array", **({"items": _schema_of(example[0])} if example else {})}
    if isinstance(example, bool):
        return {"type": "boolean"}
    if isinstance(example, int):
        return {"type": "integer"}
    if isinstance(example, float):
        return {"type": "number"}
    return {"type": "string"}


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
