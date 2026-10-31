"""Hash-chained JSONL sale log. Each line carries the hash of the previous
line, so an edited or removed entry breaks the chain."""

import hashlib
import json
from pathlib import Path

GENESIS = "0" * 64


def _hash(prev, record):
    body = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev + "|" + body).encode()).hexdigest()


def read_chain(path):
    """Returns the records, or raises ValueError if the chain is broken."""
    p = Path(path)
    if not p.exists():
        return []
    prev, records = GENESIS, []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        entry = json.loads(line)
        if entry.get("prev") != prev or entry.get("hash") != _hash(prev, entry.get("record")):
            raise ValueError("sale log chain broken at line %d" % n)
        prev = entry["hash"]
        records.append(entry["record"])
    return records


def ready(path):
    """True when the log can be appended to safely."""
    try:
        read_chain(path)
    except (ValueError, OSError, json.JSONDecodeError):
        return False
    return Path(path).parent.exists()


def append(path, record):
    p = Path(path)
    prev = GENESIS
    if p.exists():
        lines = p.read_text(encoding="utf-8").splitlines()
        if lines:
            prev = json.loads(lines[-1])["hash"]
    entry = {"prev": prev, "record": record, "hash": _hash(prev, record)}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry["hash"]
