"""Result bundle builder (spec section 11)."""

import hashlib
import json

from riggrade import BENCH_VERSION


def output_entry(obj):
    """Raw output stored as canonical JSON text with its SHA-256."""
    content = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return {"content": content,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}


def build(session, params, image_digest, identity, gate, micro, metrics,
          raw, slice_info=None, attestation=None):
    """Assemble the bundle the endpoint checks: nonce, version, pinned image,
    echoed parameters, and every raw output with its hash."""
    return {"session_id": session["session_id"],
            "nonce": session["nonce"],
            "bench_version": BENCH_VERSION,
            "image_digest": image_digest,
            "params": params,
            "identity": identity,
            "gate": gate,
            "micro": micro,
            "metrics": metrics,
            "slice": slice_info,
            "attestation": attestation,
            "outputs": {name: output_entry(obj) for name, obj in sorted(raw.items())}}
