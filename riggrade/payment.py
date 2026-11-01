"""x402 payment adapter: verify, screen the payer, then settle, through a
keyless public facilitator with an automatic backup.

    RIGGRADE_FACILITATORS=https://x402.dexter.cash,https://facilitator.payai.network
    RIGGRADE_SCREEN_LIST=/path/to/blocked-addresses.txt

Funds move from the payer straight to the payout address in the payment
requirement. A facilitator only checks the signed USDC authorisation and
submits it on-chain; it never holds funds, and this module holds no key.

Order, per the endpoint's adapter contract:
1. Decode the X-PAYMENT header and check it locally against the requirement:
   scheme, network, recipient, amount. A mismatch is refused before any
   network call.
2. Screen the payer against the blocked-address list. A blocked payer is
   refused before anything is verified or settled.
3. Verify with the first facilitator that answers. Only a facilitator that
   cannot be reached moves the call on to the next one. A payment one
   facilitator calls invalid is refused, not retried elsewhere.
4. Settle with the same facilitator that verified it. If the settle call
   gets no answer, the result is charged "unknown" and nothing is retried:
   the authorisation may already be on-chain, and a second submission must
   never be risked.

UNVERIFIED: both facilitator hosts were unreachable from the build
environment. The /verify and /settle paths and body shape follow the x402
v1 facilitator specification; a live check against each host's /supported
endpoint comes before the sale switch.
"""

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

from .endpoint import WALLET


class Unreachable(Exception):
    """The facilitator could not be reached or did not answer in time."""


def http_post(url, body, timeout=20):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "riggrade/0.1 (+https://github.com/nnyycc77-ship-it/riggrade)"})
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


def decode_payment(header):
    try:
        payload = json.loads(base64.b64decode(header, validate=True))
    except (ValueError, TypeError):
        raise ValueError("X-PAYMENT is not base64 JSON")
    if not isinstance(payload, dict):
        raise ValueError("X-PAYMENT is not a JSON object")
    return payload


def local_check(payload, requirement):
    """Returns (payer, None) or (None, reason). No network."""
    if payload.get("scheme") != requirement["scheme"]:
        return None, "scheme mismatch"
    if payload.get("network") != requirement["network"]:
        return None, "network mismatch"
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
    if value < int(requirement["maxAmountRequired"]):
        return None, "amount below price"
    return payer, None


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
    def __init__(self, facilitators, screen, post=http_post):
        self.facilitators = [f.rstrip("/") for f in facilitators if f.strip()]
        if not self.facilitators:
            raise ValueError("at least one facilitator URL is required")
        if screen is None:
            raise ValueError("a payer screen is required")
        self.screen, self.post = screen, post

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
        body = {"x402Version": payload.get("x402Version", 1),
                "paymentPayload": payload, "paymentRequirements": requirement}
        tried = []
        for base in self.facilitators:
            try:
                verdict = self.post(base + "/verify", body)
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

        body = {"x402Version": payload.get("x402Version", 1),
                "paymentPayload": payload, "paymentRequirements": requirement}
        tried = []
        for base in self.facilitators:
            try:
                verdict = self.post(base + "/verify", body)
            except Unreachable as exc:
                tried.append("%s: %s" % (base, exc))
                continue
            if not verdict.get("isValid"):
                return self._refuse("payment invalid: %s"
                                    % verdict.get("invalidReason", "no reason given"),
                                    facilitator=base)
            try:
                result = self.post(base + "/settle", body)
            except Unreachable as exc:
                return self._refuse("settle got no answer: %s" % exc,
                                    charged="unknown", facilitator=base)
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
                    "receipt_header": receipt, "charged": True, "facilitator": base}
        return self._refuse("no facilitator reachable: " + "; ".join(tried))


def from_env(env, post=http_post):
    """The adapter the environment describes, or None. None keeps the sale
    off: the endpoint lists "no payment adapter" among its reasons."""
    urls = [u for u in env.get("RIGGRADE_FACILITATORS", "").split(",") if u.strip()]
    screen_path = env.get("RIGGRADE_SCREEN_LIST", "")
    if not urls or not screen_path:
        return None
    return FacilitatorAdapter(urls, AddressListScreen(screen_path), post=post)
