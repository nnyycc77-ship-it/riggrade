"""Verify a RigGrade cluster acceptance certificate.

    python3 -m riggrade.verify certificate.json
    python3 -m riggrade.verify certificate.json --keys riggrade-keys.json
    python3 -m riggrade.verify certificate.json --offline

What the check proves depends on the anchor. The published key set
(GET /.well-known/riggrade-keys.json on the issuing service) is the anchor:
a signature that verifies under a key in that set proves RigGrade issued the
certificate and it has not changed. By default the verifier fetches that
document from the URL the certificate names (keys_url), or from --keys
(a saved copy). With --offline and no --keys, only the key embedded in the
certificate is used, which proves the certificate matches its own key and
nothing about who issued it; the output says so. Exit 0 when valid and
anchored, 2 when valid but unanchored, 1 otherwise.
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from . import acceptance

UNANCHORED = ("UNANCHORED: checked only against the key embedded in the certificate. This proves "
              "the certificate matches its own key, not that RigGrade issued it; a forged "
              "certificate can carry its own key. Supply the published key set (--keys, or let "
              "the verifier fetch keys_url) to prove issuance.")


def fetch_keys(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "riggrade-verify"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check(cert, keys_doc=None, public_key=None):
    """(ok, anchored, lines). Never raises on a malformed certificate.
    anchored is True only when the embedded key was found in a published
    key set; a valid but unanchored result says plainly what it proves."""
    lines = []
    sig = cert.get("ed25519") if isinstance(cert, dict) else None
    if not isinstance(sig, dict):
        return False, False, ["no ed25519 object: this certificate carries only the service's HMAC"]
    embedded = sig.get("public_key", "")
    key = public_key or embedded
    anchored = False
    if keys_doc is not None:
        published = {k.get("public_key_base64") for k in keys_doc.get("keys", [])}
        if embedded in published:
            lines.append("embedded key is in the published key set (kid %s): anchored" % sig.get("kid"))
            anchored = True
        else:
            lines.append("embedded key is NOT in the published key set: not issued under a published key")
            return False, False, lines
    ok = acceptance.verify_ed25519(cert, key)
    lines.append("ed25519 signature %s (kid %s)" % ("valid" if ok else "INVALID", sig.get("kid")))
    if ok and not anchored:
        lines.append(UNANCHORED)
    lines.append("pack sha256 %s: recompute the evidence pack with runner.cluster evaluate and compare"
                 % cert.get("pack_sha256"))
    return ok, anchored, lines


def main(argv=None, fetch=fetch_keys):
    ap = argparse.ArgumentParser()
    ap.add_argument("certificate")
    ap.add_argument("--keys", default="", help="the published key document, saved as a file")
    ap.add_argument("--keys-url", default="", help="fetch the published key document from here "
                                                   "(default: the certificate's keys_url)")
    ap.add_argument("--offline", action="store_true", help="do not fetch; unanchored unless --keys is given")
    ap.add_argument("--public-key", default="", help="a base64 Ed25519 public key to use instead")
    a = ap.parse_args(argv)
    cert = json.loads(Path(a.certificate).read_text(encoding="utf-8"))
    if isinstance(cert, dict) and "certificate" in cert:   # the lookup response wraps it
        cert = cert["certificate"]
    keys_doc = None
    if a.keys:
        keys_doc = json.loads(Path(a.keys).read_text(encoding="utf-8"))
    elif not a.offline:
        url = a.keys_url or (cert.get("keys_url") if isinstance(cert, dict) else "")
        if url:
            try:
                keys_doc = fetch(url)
            except Exception as exc:  # noqa: BLE001 - reported, then unanchored
                sys.stdout.write("could not fetch the published key set from %s: %s\n" % (url, exc))
        else:
            sys.stdout.write("the certificate names no keys_url; pass --keys or --keys-url to anchor\n")
    ok, anchored, lines = check(cert, keys_doc, a.public_key or None)
    sys.stdout.write("\n".join(lines) + "\n")
    return 0 if ok and anchored else (2 if ok else 1)


if __name__ == "__main__":
    sys.exit(main())
