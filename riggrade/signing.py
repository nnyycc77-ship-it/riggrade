"""Independently verifiable certificate signatures.

The service derives one Ed25519 signing key from its configured secret
(HMAC-SHA256 of the secret with a fixed label, so no second secret is
stored anywhere) and publishes the public key at
/.well-known/riggrade-keys.json. A certificate carries an "ed25519" object
(key id, public key, signature over its canonical body), so anyone holding
the certificate can verify it offline with the published key:
`python3 -m riggrade.verify certificate.json`. The service's own HMAC
check stays as a second, internal field. Needs the optional `cryptography`
package; without it the service issues HMAC-only certificates and the key
document says so.
"""

import base64
import hashlib
import hmac
import json

try:  # optional extra "cdp" ships cryptography; the server image has it
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
except ImportError:  # pragma: no cover - exercised only where the package is absent
    ed25519 = None
    serialization = None
    InvalidSignature = Exception

ALG = "Ed25519"
DERIVATION_LABEL = b"riggrade certificate signing v1"
CANONICAL_RULE = ("JSON with keys sorted, separators ',' and ':', UTF-8, over every certificate "
                  "field except 'signature' and 'ed25519'")
KEYS_PATH = "/.well-known/riggrade-keys.json"


def available():
    return ed25519 is not None


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def kid_of(public_key_b64):
    return hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()[:16]


class Signer:
    def __init__(self, seed):
        self._key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
        raw = self._key.public_key().public_bytes(serialization.Encoding.Raw,
                                                  serialization.PublicFormat.Raw)
        self.public_key_b64 = base64.b64encode(raw).decode()
        self.kid = kid_of(self.public_key_b64)

    @classmethod
    def from_secret(cls, secret):
        """The service's signer, or None without a secret or the package."""
        if not secret or not available():
            return None
        seed = hmac.new(secret, DERIVATION_LABEL, hashlib.sha256).digest()
        return cls(seed)

    def sign(self, body_bytes):
        return base64.b64encode(self._key.sign(body_bytes)).decode()


def verify(public_key_b64, body_bytes, signature_b64):
    """True when the signature is valid for the body under the key; False
    otherwise, including when the package is absent (never a silent pass)."""
    if not available():
        return False
    try:
        key = ed25519.Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(signature_b64), body_bytes)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def keys_document(signer, prior_public_keys=()):
    """The published key set. Prior keys stay listed so older certificates
    still verify after a rotation."""
    keys = []
    if signer is not None:
        keys.append({"kid": signer.kid, "alg": ALG, "public_key_base64": signer.public_key_b64,
                     "use": "cluster acceptance certificates", "status": "current"})
    for pk in prior_public_keys:
        pk = pk.strip()
        if pk:
            keys.append({"kid": kid_of(pk), "alg": ALG, "public_key_base64": pk,
                         "use": "cluster acceptance certificates", "status": "prior"})
    return {"keys": keys,
            "canonicalisation": CANONICAL_RULE,
            "verify": "python3 -m riggrade.verify certificate.json --keys <this document>, or any Ed25519 "
                      "library: decode public_key_base64, verify ed25519.signature over the canonical body.",
            "note": "No keys listed means this service issues HMAC-only certificates (cryptography not installed)."
                    if not keys else "Certificates name their key by kid in the ed25519 object."}
