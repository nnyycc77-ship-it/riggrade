"""Session protocol (spec section 6): nonce, parameters at run start, and
result bundle checks. State lives in a store object so the endpoint can use
memory in tests and a file in deployment."""

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from . import BENCH_VERSION
from .classes import WORKLOAD_CLASSES

COMPLETION_WINDOW_S = 3 * 3600  # spec 6, [proposed]
DISTRIBUTION_FILE = Path(__file__).resolve().parent / "spec" / "param-distribution.json"
RUNNERS = ("buyer", "host")


# Full: the spec 5 run with the 60-minute sustained run. Quick (owner
# decision, 5 Nov 2026): the same gate and passes without it, about 10 minutes.
MODES = ("full", "quick")


class SessionError(ValueError):
    """The session's state does not allow the request (HTTP 409)."""


class SessionInputError(SessionError):
    """The request itself is malformed: answered 400, as the OpenAPI says."""


class SessionNotFound(SessionError):
    """No session with this id: answered 404."""


def load_distribution(path=None):
    return json.loads(Path(path or DISTRIBUTION_FILE).read_text(encoding="utf-8"))


def draw(secret, nonce, field, choices):
    """Deterministic draw: HMAC-SHA256(secret, nonce || field) mod len."""
    digest = hmac.new(secret, (nonce + "|" + field).encode(), hashlib.sha256).digest()
    return choices[int.from_bytes(digest, "big") % len(choices)]


def draw_parameters(secret, nonce, workloads, distribution):
    params = {"passes": draw(secret, nonce, "passes", distribution["passes"]),
              "pass_order": [w for w in draw(secret, nonce, "pass_order",
                                             distribution["pass_order"])
                             if w in workloads]}
    for workload in workloads:
        params[workload] = {field: draw(secret, nonce, workload + "." + field, choices)
                            for field, choices in sorted(distribution[workload].items())}
    return params


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Sessions:
    def __init__(self, secret, store, allowed_images, distribution=None,
                 clock=None):
        if not secret:
            raise SessionError("a server secret is required")
        self.secret = secret
        self.store = store
        self.allowed_images = set(allowed_images)
        self.distribution = distribution or load_distribution()
        self.clock = clock

    def issue(self, workloads, declared_runner, host_wallet=None, mode="full"):
        if mode not in MODES:
            raise SessionInputError("mode must be full or quick")
        if not workloads or any(w not in WORKLOAD_CLASSES for w in workloads):
            raise SessionInputError("workload_classes must be a non-empty subset of %s"
                                    % (WORKLOAD_CLASSES,))
        if declared_runner not in RUNNERS:
            raise SessionInputError("declared_runner must be buyer or host")
        now = self.clock()
        session = {"session_id": "rs_" + secrets.token_hex(8),
                   "nonce": secrets.token_hex(16),
                   "workloads": sorted(set(workloads)),
                   "declared_runner": declared_runner,
                   "host_wallet": host_wallet,
                   "mode": mode,
                   "issued_at": now,
                   "expires_at": now + COMPLETION_WINDOW_S,
                   "started_at": None, "params": None, "bundle": None}
        self.store.put(session["session_id"], session)
        return session

    def start(self, session_id):
        session = self._live(session_id)
        if session["started_at"] is None:
            session["started_at"] = self.clock()
            session["params"] = draw_parameters(self.secret, session["nonce"],
                                                session["workloads"],
                                                self.distribution)
            if session.get("mode") == "quick":
                # The container skips the 60-minute sustained run (spec 5.2).
                session["params"]["sustained_s"] = 0
            self.store.put(session_id, session)
        return session

    def submit(self, session_id, bundle):
        """Validate a result bundle (spec 6 step 4). Returns the error list;
        an empty list means accepted and stored."""
        session = self.store.get(session_id)
        if session is None:
            return ["unknown_session"]
        errors = []
        if session["started_at"] is None:
            errors.append("not_started")
        if self.clock() > session["expires_at"]:
            errors.append("window_expired")
        if bundle.get("nonce") != session["nonce"]:
            errors.append("nonce_mismatch")
        if bundle.get("bench_version") != BENCH_VERSION:
            errors.append("bench_version_mismatch")
        if bundle.get("image_digest") not in self.allowed_images:
            errors.append("image_not_pinned")
        if session["params"] is not None and bundle.get("params") != session["params"]:
            errors.append("params_mismatch")
        for name, out in sorted((bundle.get("outputs") or {}).items()):
            if sha256_hex(out.get("content", "")) != out.get("sha256"):
                errors.append("output_hash_mismatch:" + name)
        if not bundle.get("outputs"):
            errors.append("no_outputs")
        if session.get("bundle") is not None:
            errors.append("bundle_already_submitted")
        if not errors:
            session["bundle"] = bundle
            self.store.put(session_id, session)
        return errors

    def _live(self, session_id):
        session = self.store.get(session_id)
        if session is None:
            raise SessionNotFound("unknown_session")
        if self.clock() > session["expires_at"]:
            raise SessionError("window_expired")
        return session


class MemoryStore:
    def __init__(self):
        self._data = {}

    def get(self, key):
        value = self._data.get(key)
        return json.loads(json.dumps(value)) if value is not None else None

    def put(self, key, value):
        self._data[key] = json.loads(json.dumps(value))

    def items(self):
        return list(self._data.items())


class FileStore:
    """One JSON file per key under a directory, written by temp file and
    rename so a crash never leaves half a record. Meant for a single server
    process (Cloud Run with max-instances 1) on a disk or a mounted bucket."""

    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key):
        return self.dir / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")

    def get(self, key):
        path = self._path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))["value"]

    def put(self, key, value):
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"key": key, "value": value}, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, path)

    def items(self):
        out = []
        for path in sorted(self.dir.glob("*.json")):
            entry = json.loads(path.read_text(encoding="utf-8"))
            out.append((entry["key"], entry["value"]))
        return out
