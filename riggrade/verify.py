"""Verify a RigGrade cluster acceptance certificate offline.

    python3 -m riggrade.verify certificate.json
    python3 -m riggrade.verify certificate.json --keys riggrade-keys.json
    python3 -m riggrade.verify certificate.json --public-key <base64>

With no key given, the public key embedded in the certificate's ed25519
object is used; pass the published key document (GET
/.well-known/riggrade-keys.json, saved to a file) to check the embedded
key against the service's published set. Exit 0 when valid, 1 otherwise.
"""

import argparse
import json
import sys
from pathlib import Path

from . import acceptance


def check(cert, keys_doc=None, public_key=None):
    """(ok, lines). Never raises on a malformed certificate."""
    lines = []
    sig = cert.get("ed25519") if isinstance(cert, dict) else None
    if not isinstance(sig, dict):
        return False, ["no ed25519 object: this certificate carries only the service's HMAC"]
    embedded = sig.get("public_key", "")
    key = public_key or embedded
    if keys_doc is not None:
        published = {k.get("public_key_base64") for k in keys_doc.get("keys", [])}
        if embedded in published:
            lines.append("embedded key is in the published key set (kid %s)" % sig.get("kid"))
        else:
            lines.append("embedded key is NOT in the published key set")
            return False, lines
    ok = acceptance.verify_ed25519(cert, key)
    lines.append("ed25519 signature %s (kid %s)" % ("valid" if ok else "INVALID", sig.get("kid")))
    lines.append("pack sha256 %s: recompute the evidence pack with runner.cluster evaluate and compare"
                 % cert.get("pack_sha256"))
    return ok, lines


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("certificate")
    ap.add_argument("--keys", default="", help="the published key document, saved as a file")
    ap.add_argument("--public-key", default="", help="a base64 Ed25519 public key to use instead")
    a = ap.parse_args(argv)
    cert = json.loads(Path(a.certificate).read_text(encoding="utf-8"))
    if isinstance(cert, dict) and "certificate" in cert:   # the lookup response wraps it
        cert = cert["certificate"]
    keys_doc = json.loads(Path(a.keys).read_text(encoding="utf-8")) if a.keys else None
    ok, lines = check(cert, keys_doc, a.public_key or None)
    sys.stdout.write("\n".join(lines) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
