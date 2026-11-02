"""The GPU record (docs/gpu-record-method.md, draft v0): a per-card,
append-only history of what a card delivered when it was measured, keyed
to the hash of the driver's UUID and the board serial where exposed.
Entries arrive only through the runner's explicit --record opt-in, with
the consent text printed first. Ships behind RIGGRADE_RECORD (a path to the
append-only log); unset or "off" means no entry is accepted or served."""

import json
import re
from pathlib import Path

from . import pool
from .classes import identify

METHOD_VERSION = "gpu-record-v0-draft"
RUNNERS = ("host", "buyer", "riggrade")
CARD_ID = re.compile(r"^gc_[0-9a-f]{16}$")
RIG_ID = re.compile(r"^rg_[0-9a-f]{16}$")
SERIAL = re.compile(r"^[A-Za-z0-9-]{4,32}$")
MAX_CARDS = 64
CONSENT = (
    "Recording sends, with your explicit opt-in, an entry to the public RigGrade GPU record. Sent: "
    "the per-card identity, which is a hash of each card's driver UUID (never the UUID itself) and "
    "the card's board serial number where the driver exposes it (data-centre cards usually do; this "
    "serial is a persistent plain identifier of the card); the rig identity hash; the GPU model, "
    "count, memory and driver; the verdict, each check's status and the measured numbers; whether "
    "the box was declared exclusive, shared or virtualised; and who you declared as the runner "
    "(host, buyer or riggrade). Not sent: hostname, path, address, wallet, owner, location or any "
    "free text. Once accepted the entry is public through the record lookup, is kept for 24 months, "
    "and cannot be withdrawn: the record is append-only, a later run supersedes, nothing is deleted. "
    "An entry declared as a buyer's run on someone else's rig is held and not served until the rig's "
    "host consents. Method: "
    "https://github.com/nnyycc77-ship-it/riggrade/blob/main/docs/gpu-record-method.md"
)


def clean(doc, now):
    """The stored entry from a submitted document, or ValueError. Everything
    not listed here is dropped, so no identifier beyond the method's can slip in."""
    if not isinstance(doc, dict) or doc.get("record") != 1:
        raise ValueError("not a record document (record must be 1)")
    runner = doc.get("declared_runner")
    if runner not in RUNNERS:
        raise ValueError("declared_runner must be host, buyer or riggrade")
    rig = doc.get("rig_id")
    if not isinstance(rig, str) or not RIG_ID.match(rig):
        raise ValueError("rig_id must be the runner's rg_ hash")
    cards_in = doc.get("cards")
    if not isinstance(cards_in, list) or not cards_in or len(cards_in) > MAX_CARDS:
        raise ValueError("cards must be a non-empty list from the runner")
    cards = []
    for c in cards_in:
        if not isinstance(c, dict) or not isinstance(c.get("card_id"), str) or not CARD_ID.match(c["card_id"]):
            raise ValueError("each card needs the runner's gc_ card_id")
        serial = c.get("serial")
        cards.append({"card_id": c["card_id"],
                      "serial": serial if isinstance(serial, str) and SERIAL.match(serial) else None})
    # The measured part is exactly what the pool keeps, cleaned the same way.
    base = pool.clean(dict(doc, pool=1), int(now) // 3600 * 3600)
    base.pop("at_hour", None)
    entry = dict(base)
    entry.update({"method": METHOD_VERSION, "at": int(now), "rig_id": rig, "cards": cards,
                  "declared_runner": runner, "attested": bool(doc.get("attested", False)),
                  "virtualised": bool(doc.get("virtualised", False)),
                  "held": runner == "buyer"})
    att = "attested" if entry["attested"] else "unattested"
    entry["evidence_tag"] = f"declared:{runner} attestation:{att}"
    mem_gb = (entry.get("memory_total_mb") or 0) / 1024
    entry["peer_class"] = identify(entry.get("gpu_name", ""), mem_gb) or "unknown_class"
    return entry


def append(path, entry):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def read(path):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def is_operator_series(entry):
    """[condition 5] Operator free-compute runs (declared_runner riggrade) and
    virtualised runs form a separate labelled series; they never merge into
    the history of a physical card."""
    return entry.get("declared_runner") == "riggrade" or bool(entry.get("virtualised"))


def history(rows, card_id):
    """Served entries for one card, oldest first; held entries are counted
    but not served; operator and virtualised entries are listed apart."""
    mine = [r for r in rows if any(c.get("card_id") == card_id for c in r.get("cards") or [])]
    unheld = sorted((r for r in mine if not r.get("held")), key=lambda r: r.get("at", 0))
    physical = [r for r in unheld if not is_operator_series(r)]
    operator = [r for r in unheld if is_operator_series(r)]
    strip = lambda r: {k: v for k, v in r.items() if k != "held"}
    return {"card_id": card_id, "records": len(physical), "held": len(mine) - len(unheld),
            "entries": [strip(r) for r in physical],
            "operator_series": {"records": len(operator), "entries": [strip(r) for r in operator],
                                "note": "Operator free-compute and virtualised runs: a separate series, "
                                        "never merged into the physical card's history."},
            "note": "Counts are records, not cards owned or hosts. Each entry carries its evidence "
                    "tag; an unattested entry's identity is readable, not provable. History is "
                    "append-only; a later entry supersedes, nothing is deleted."}


SMALL = 5
PROTECTION_STAGE = 20


def protect(n, total):
    """[C4] Locked-counts protection, as Revision 5 discussion decision 5
    states it: while the class holds fewer than PROTECTION_STAGE records,
    every figure is rounded to the nearest five and anything under five
    reads "fewer than 5"; from PROTECTION_STAGE on, a figure publishes as
    counted, except that a cell under five still reads "fewer than 5"."""
    if n < SMALL:
        return "fewer than 5"
    if total < PROTECTION_STAGE:
        return int(SMALL * round(n / SMALL))
    return n


def counts(rows):
    """Per peer class, for publication: the class total and its split
    (physical records, held, operator series), each protected."""
    raw = {}
    for r in rows:
        c = raw.setdefault(r.get("peer_class") or "unknown_class", {"records": 0, "held": 0, "operator": 0})
        key = "held" if r.get("held") else ("operator" if is_operator_series(r) else "records")
        c[key] += 1
    out = {}
    for cid, c in raw.items():
        total = sum(c.values())
        out[cid] = {"total": protect(total, total), **{k: protect(v, total) for k, v in c.items()}}
    return out
