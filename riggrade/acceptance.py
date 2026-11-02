"""Paid cluster acceptance certificate (decided 1 Nov 2026).

The renter runs runner.cluster on the rented nodes and submits the
evidence pack it wrote (acceptance.json). The server recomputes every
check and the verdict from the claims and node reports with the same
rules, refuses a pack that does not recompute, and on payment issues a
signed certificate it also stores, so anyone with the id can fetch and
verify it. Measured, not promised: the certificate states what the
cluster delivered during that test against what its provider claimed.
"""

import hashlib
import hmac
import json

from . import cluster_rules, signing

PRODUCT = "riggrade_cluster_acceptance"
STATEMENT = ("Measured, not promised. What this cluster delivered during the named test, "
             "against what its provider claimed; no promise about future delivery.")


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def validate(pack):
    """Returns the error text for a malformed pack, else None."""
    if not isinstance(pack, dict):
        return "evidence_pack must be the acceptance.json object"
    if not isinstance(pack.get("claims"), dict):
        return "evidence_pack.claims must be an object"
    nodes = pack.get("nodes")
    if not isinstance(nodes, list) or not nodes or not all(isinstance(n, dict) for n in nodes):
        return "evidence_pack.nodes must be a non-empty list of node reports"
    if not isinstance(pack.get("checks"), list) or not isinstance(pack.get("verdict"), str):
        return "evidence_pack must carry the checks and verdict the evaluate step wrote"
    return None


def recompute(pack):
    """(checks, result) from the pack's claims and nodes, by the shared rules."""
    nodes = sorted(pack["nodes"], key=lambda n: n.get("rank", 0))
    return cluster_rules.evaluate(pack["claims"], nodes)


def certificate(pack, checks, result, secret, now, cert_id):
    nodes = pack["nodes"]
    cert = {"product": PRODUCT, "certificate_id": cert_id, "issued_at": now,
            "method": pack.get("method"), "rules": cluster_rules.METHOD_VERSION,
            "pack_sha256": hashlib.sha256(canonical(pack)).hexdigest(),
            "claims": pack["claims"],
            "images": sorted({n.get("image") or "unrecorded" for n in nodes}),
            "nodes": len(nodes),
            "checks": checks,
            "verdict": result["verdict"], "exceptions": result["exceptions"],
            "rejections": result["rejections"],
            "statement": STATEMENT}
    cert["signature"] = sign(cert, secret)
    return cert


SIGNATURE_FIELDS = ("signature", "ed25519")


def body(cert):
    """The signed content: every field but the signatures themselves."""
    return {k: v for k, v in cert.items() if k not in SIGNATURE_FIELDS}


def sign(cert, secret):
    """The service's own check (HMAC); kept beside the Ed25519 signature."""
    return hmac.new(secret, canonical(body(cert)), hashlib.sha256).hexdigest()


def verify(cert, secret):
    return hmac.compare_digest(sign(cert, secret), cert.get("signature", ""))


def attach_ed25519(cert, signer):
    """Independently verifiable signature with the published key; a no-op
    without a signer (HMAC-only certificate)."""
    if signer is None:
        return cert
    cert["ed25519"] = {"kid": signer.kid, "alg": signing.ALG, "public_key": signer.public_key_b64,
                       "signature": signer.sign(canonical(body(cert))),
                       "canonicalisation": signing.CANONICAL_RULE}
    return cert


def verify_ed25519(cert, public_key_b64=None):
    """True, False, or None when the certificate carries no Ed25519 object."""
    sig = cert.get("ed25519") if isinstance(cert, dict) else None
    if not isinstance(sig, dict):
        return None
    return signing.verify(public_key_b64 or sig.get("public_key", ""), canonical(body(cert)),
                          sig.get("signature", ""))
