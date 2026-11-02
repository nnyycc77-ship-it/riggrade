"""x402 payment adapter: verify, screen the payer, then settle, through a
keyless public facilitator with an automatic backup.

    RIGGRADE_FACILITATORS=https://x402.dexter.cash,https://facilitator.payai.network
    RIGGRADE_SCREEN_LIST=/path/to/blocked-addresses.txt

Funds move from the payer straight to the payout address in the payment
requirement. A facilitator only checks the signed USDC authorisation and
submits it on-chain; it never holds funds, and this module holds no key.

Order, per the endpoint's adapter contract:
1. Decode the PAYMENT-SIGNATURE header (x402 v2 PaymentPayload) and check
   it locally against the requirement: version, scheme, network, recipient,
   amount. A mismatch is refused before any network call. x402 v1 payloads
   (X-PAYMENT) are refused: both facilitators advertise v2 exact on Base
   (eip155:8453), and Dexter v2 only.
2. Screen the payer against the blocked-address list. A blocked payer is
   refused before anything is verified or settled.
3. Verify with the first facilitator that answers. Only a facilitator that
   cannot be reached moves the call on to the next one. A payment one
   facilitator calls invalid is refused, not retried elsewhere.
4. Settle with the same facilitator that verified it. If the settle call
   gets no answer, the result is charged "unknown" and nothing is retried:
   the authorisation may already be on-chain, and a second submission must
   never be risked.

The /verify and /settle paths and body shape follow the x402 v2 facilitator
specification ({x402Version, paymentPayload, paymentRequirements}). Both
hosts' GET /supported were read on 1 Nov 2026: Dexter lists x402Version 2
exact on eip155:8453; PayAI lists v1 "base" and v2 "eip155:8453".
"""

import base64
import json
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path

from .endpoint import WALLET

# The Coinbase (CDP) facilitator authenticates every call with a bearer JWT
# signed by a CDP API key; the other facilitators are keyless.
CDP_FACILITATOR = "https://api.cdp.coinbase.com/platform/v2/x402"
CDP_HOST = "api.cdp.coinbase.com"


class Unreachable(Exception):
    """The facilitator could not be reached or did not answer in time."""


def http_post(url, body, timeout=20, headers=None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "riggrade/0.1 (+https://github.com/nnyycc77-ship-it/riggrade)",
                                          **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        # The facilitator answered; an error body is still its answer.
        try:
            return json.loads(exc.read().decode())
        except ValueError:
            raise Unreachable("HTTP %d with no JSON body" % exc.code)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise Unreachable(str(exc))


X402_VERSION = 2


def decode_payment(header):
    try:
        payload = json.loads(base64.b64decode(header, validate=True))
    except (ValueError, TypeError):
        raise ValueError("PAYMENT-SIGNATURE is not base64 JSON")
    if not isinstance(payload, dict):
        raise ValueError("PAYMENT-SIGNATURE is not a JSON object")
    if payload.get("x402Version") != X402_VERSION:
        raise ValueError("unsupported x402 version %r: send x402Version 2"
                         % payload.get("x402Version"))
    return payload


def local_check(payload, requirement):
    """Returns (payer, None) or (None, reason). No network. The payload's
    "accepted" entry must be the requirement we issued; the signed
    authorisation must pay our address at least the amount."""
    accepted = payload.get("accepted") or {}
    if accepted.get("scheme") != requirement["scheme"]:
        return None, "scheme mismatch"
    if accepted.get("network") != requirement["network"]:
        return None, "network mismatch"
    if (accepted.get("payTo") or "").lower() != requirement["payTo"].lower():
        return None, "accepted requirement is not to the payout address"
    auth = (payload.get("payload") or {}).get("authorization") or {}
    payer, to = auth.get("from", ""), auth.get("to", "")
    if not WALLET.match(payer or ""):
        return None, "no payer address in authorisation"
    if (to or "").lower() != requirement["payTo"].lower():
        return None, "payment is not to the payout address"
    try:
        value = int(auth.get("value", "0"))
    except (TypeError, ValueError):
        return None, "bad amount"
    if value < int(requirement["amount"]):
        return None, "amount below price"
    return payer, None


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def bazaar_outcome(payload, verify_response, settle_response):
    """What the Bazaar catalogue saw. Listing happens only when the paying
    client echoes the 402's bazaar extension in its payload (x402 bazaar
    extension spec); the facilitator may report the cataloguing outcome in
    the extensions sidechannel of its verify or settle response. Recorded
    in the sale log so a missing listing can be traced to its cause."""
    extensions = payload.get("extensions") if isinstance(payload, dict) else None
    echoed = isinstance(extensions, dict) and "bazaar" in extensions
    outcome = None
    for resp in (settle_response, verify_response):
        ext = resp.get("extensions") if isinstance(resp, dict) else None
        if isinstance(ext, dict) and ext.get("bazaar") is not None:
            outcome = ext.get("bazaar")
            break
    return {"echoed_by_client": echoed, "facilitator_outcome": outcome}


class CdpAuth:
    """Bearer JWT for the Coinbase (CDP) facilitator, as the cdp-sdk signs it
    (python/cdp/auth/utils/jwt.py): header {alg EdDSA, kid, typ JWT, nonce},
    claims {sub, iss "cdp", nbf, exp (120 s), uris ["POST host/path"]},
    signed with the key's Ed25519 seed. The secret is the base64 the CDP
    portal shows (64 bytes: seed then public key) and is held only in
    memory, read from the environment at start. Signing uses the
    `cryptography` package; without it the CDP facilitator is unusable
    and from_env says so."""

    EXPIRES_IN = 120

    def __init__(self, key_id, key_secret, now=time.time):
        from cryptography.hazmat.primitives.asymmetric import ed25519
        key_id, key_secret = self.unpack(key_id, key_secret)
        raw = base64.b64decode(key_secret.strip(), validate=True)
        if len(raw) == 64:
            raw = raw[:32]
        if len(raw) != 32:
            raise ValueError("CDP key secret is not a base64 Ed25519 key")
        self.key_id = key_id
        self._key = ed25519.Ed25519PrivateKey.from_private_bytes(raw)
        self._now = now

    @staticmethod
    def unpack(key_id, key_secret):
        """The secret may be the key file the CDP portal downloads, as is:
        JSON with "privateKey" (and "id"). Then the file's id is the key ID
        unless one was given separately. Otherwise the secret is the bare
        base64 key."""
        text = (key_secret or "").strip()
        if text.startswith("{"):
            try:
                doc = json.loads(text)
            except ValueError:
                raise ValueError("CDP key file is not valid JSON")
            secret = doc.get("privateKey") or doc.get("secret") or ""
            if not secret:
                raise ValueError("CDP key file has no privateKey")
            return (key_id or doc.get("id") or doc.get("name") or "").strip(), secret
        return (key_id or "").strip(), text

    def token(self, method, path, host=CDP_HOST):
        now = int(self._now())
        header = {"alg": "EdDSA", "kid": self.key_id, "typ": "JWT",
                  "nonce": "".join(secrets.choice("0123456789") for _ in range(16))}
        claims = {"sub": self.key_id, "iss": "cdp", "nbf": now, "exp": now + self.EXPIRES_IN,
                  "uris": ["%s %s%s" % (method, host, path)]}
        signing_input = (_b64url(json.dumps(header, separators=(",", ":")).encode()) + "."
                         + _b64url(json.dumps(claims, separators=(",", ":")).encode()))
        signature = self._key.sign(signing_input.encode())
        return signing_input + "." + _b64url(signature)

    def headers(self, method, url):
        path = url.split(CDP_HOST, 1)[1] if CDP_HOST in url else url
        return {"Authorization": "Bearer " + self.token(method, path),
                "Correlation-Context": "sdk_language=python,source=riggrade,source_version=0.1"}


class AddressListScreen:
    """Blocked-address screen from a plain file: one 0x address per line,
    '#' starts a comment. The list itself comes from the program's
    compliance source; this class only applies it.

    Fails closed: a missing or unreadable file refuses every payer."""

    def __init__(self, path):
        self.path = Path(path)
        self.blocked, self.error = set(), None
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                if line:
                    if not WALLET.match(line):
                        raise ValueError("not an address: %r" % line[:60])
                    self.blocked.add(line.lower())
        except (OSError, ValueError) as exc:
            self.error = str(exc)

    def __call__(self, payer):
        if self.error:
            return False, "screen list unavailable: %s" % self.error
        if payer.lower() in self.blocked:
            return False, "payer refused by screen"
        return True, None


class FacilitatorAdapter:
    def __init__(self, facilitators, screen, post=http_post, auth=None):
        """auth: {facilitator base URL: object with headers(method, url)};
        a facilitator without an entry is called keyless."""
        self.facilitators = [f.rstrip("/") for f in facilitators if f.strip()]
        if not self.facilitators:
            raise ValueError("at least one facilitator URL is required")
        if screen is None:
            raise ValueError("a payer screen is required")
        self.screen, self.post = screen, post
        self.auth = {k.rstrip("/"): v for k, v in (auth or {}).items()}

    def _post(self, base, path, body):
        url = base + path
        signer = self.auth.get(base)
        if signer is None:
            return self.post(url, body)
        return self.post(url, body, headers=signer.headers("POST", url))

    @staticmethod
    def _refuse(error, charged=False, **extra):
        return dict({"ok": False, "error": error, "charged": charged}, **extra)

    def verify_only(self, header, requirement):
        """Verify a signed authorisation without settling it: used for the
        zero-amount operator claim on a free reference session (spec 13).
        Returns {"ok": True, "payer": addr} or a refusal. No money moves."""
        try:
            payload = decode_payment(header)
        except ValueError as exc:
            return self._refuse(str(exc))
        payer, reason = local_check(payload, requirement)
        if reason:
            return self._refuse(reason)
        allowed, reason = self.screen(payer)
        if not allowed:
            return self._refuse(reason)
        body = {"x402Version": X402_VERSION,
                "paymentPayload": payload, "paymentRequirements": requirement}
        tried = []
        for base in self.facilitators:
            try:
                verdict = self._post(base, "/verify", body)
            except Unreachable as exc:
                tried.append("%s: %s" % (base, exc))
                continue
            if not verdict.get("isValid"):
                return self._refuse("claim invalid: %s"
                                    % verdict.get("invalidReason", "no reason given"),
                                    facilitator=base)
            return {"ok": True, "payer": payer, "charged": False, "facilitator": base}
        return self._refuse("no facilitator reachable: " + "; ".join(tried))

    def settle(self, header, requirement):
        try:
            payload = decode_payment(header)
        except ValueError as exc:
            return self._refuse(str(exc))
        payer, reason = local_check(payload, requirement)
        if reason:
            return self._refuse(reason)
        allowed, reason = self.screen(payer)
        if not allowed:
            return self._refuse(reason)

        body = {"x402Version": X402_VERSION,
                "paymentPayload": payload, "paymentRequirements": requirement}
        tried = []
        for base in self.facilitators:
            try:
                verdict = self._post(base, "/verify", body)
            except Unreachable as exc:
                tried.append("%s: %s" % (base, exc))
                continue
            if not verdict.get("isValid"):
                return self._refuse("payment invalid: %s"
                                    % verdict.get("invalidReason", "no reason given"),
                                    facilitator=base)
            try:
                result = self._post(base, "/settle", body)
            except Unreachable as exc:
                return self._refuse("settle got no answer: %s" % exc,
                                    charged="unknown", facilitator=base)
            if result.get("errorReason") == "settlement_pending":
                # v2: non-terminal; the transaction may land later. Never resubmit.
                return self._refuse("settlement pending at the facilitator",
                                    charged="unknown", tx=result.get("transaction"),
                                    facilitator=base)
            if not result.get("success"):
                return self._refuse("settle failed: %s"
                                    % result.get("errorReason", "no reason given"),
                                    facilitator=base)
            settled_payer = result.get("payer") or payer
            if settled_payer.lower() != payer.lower():
                # Paid, but not by the screened address: record it as
                # charged and refuse the grade, so it can be refunded by hand.
                return self._refuse("settled payer differs from screened payer",
                                    charged=True, tx=result.get("transaction"),
                                    facilitator=base)
            receipt = base64.b64encode(json.dumps(result, sort_keys=True).encode()).decode()
            return {"ok": True, "payer": payer, "tx": result.get("transaction"),
                    "receipt_header": receipt, "charged": True, "facilitator": base,
                    "bazaar": bazaar_outcome(payload, verdict, result)}
        return self._refuse("no facilitator reachable: " + "; ".join(tried))


def from_env(env, post=http_post):
    """The adapter the environment describes, or None. None keeps the sale
    off: the endpoint lists "no payment adapter" among its reasons. A CDP
    facilitator in the list needs RIGGRADE_CDP_KEY_ID and
    RIGGRADE_CDP_KEY_SECRET; without them, or without the cryptography
    package, it is dropped from the list and the others stay."""
    urls = [u.strip().rstrip("/") for u in env.get("RIGGRADE_FACILITATORS", "").split(",") if u.strip()]
    screen_path = env.get("RIGGRADE_SCREEN_LIST", "")
    if not urls or not screen_path:
        return None
    auth = {}
    cdp = [u for u in urls if CDP_HOST in u]
    if cdp:
        key_id, key_secret = env.get("RIGGRADE_CDP_KEY_ID", "").strip(), env.get("RIGGRADE_CDP_KEY_SECRET", "")
        try:
            key_id, key_secret = CdpAuth.unpack(key_id, key_secret)
            signer = CdpAuth(key_id, key_secret) if key_id and key_secret else None
        except (ImportError, ValueError):
            signer = None
        if signer is None:
            urls = [u for u in urls if u not in cdp]
            if not urls:
                return None
        else:
            auth = {u: signer for u in cdp}
    return FacilitatorAdapter(urls, AddressListScreen(screen_path), post=post, auth=auth)
