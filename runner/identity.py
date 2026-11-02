"""Rig identity from nvidia-smi (spec section 11, declared identity)."""

import hashlib
import subprocess

QUERY = ["nvidia-smi", "--query-gpu=name,memory.total,driver_version,pci.bus_id,uuid,serial",
         "--format=csv,noheader,nounits"]
NOT_REPORTED = ("[N/A]", "[Not Supported]", "N/A", "")


class IdentityError(RuntimeError):
    pass


def parse(csv_text):
    """Parse nvidia-smi CSV rows into the bundle identity block.

    nvidia-smi reports memory.total in MiB; the bundle carries it as GiB
    (MiB / 1024), which is what class identification matches against the
    nominal size within 10%. A rig with mixed card models is refused: a rig
    is graded as one unit of one peer class.
    """
    rows = [r for r in (line.strip() for line in csv_text.splitlines()) if r]
    if not rows:
        raise IdentityError("no GPU reported")
    cards = []
    for row in rows:
        parts = [p.strip() for p in row.split(",")]
        if len(parts) < 5:
            raise IdentityError("unexpected nvidia-smi row: %r" % row)
        name, mem_mib, driver, bus, uuid = parts[:5]
        serial = parts[5] if len(parts) > 5 and parts[5] not in NOT_REPORTED else None
        cards.append({"name": name, "memory_gb": round(float(mem_mib) / 1024, 1),
                      "driver": driver, "pci_bus_id": bus, "uuid": uuid, "serial": serial})
    names = {c["name"] for c in cards}
    if len(names) > 1:
        raise IdentityError("mixed card models in one rig: %s" % sorted(names))
    return {"device_name": cards[0]["name"],
            "memory_gb": cards[0]["memory_gb"],
            "card_count": len(cards),
            "driver": cards[0]["driver"],
            "pci_bus_ids": [c["pci_bus_id"] for c in cards],
            "rig_id": rig_id([c["uuid"] for c in cards]),
            # Per-card identity for the GPU record (docs/gpu-record-method.md):
            # the hash of the driver's UUID, and the board serial where the
            # driver exposes it (data-centre cards; GeForce usually reports
            # N/A). The UUID itself is never written.
            "cards": [{"card_id": card_id(c["uuid"]), "serial": c["serial"]} for c in cards]}


def card_id(uuid):
    """Stable per-card identity from the GPU UUID, without storing the UUID."""
    return "gc_" + hashlib.sha256(uuid.encode()).hexdigest()[:16]


def rig_id(uuids):
    """Stable rig identity from the GPU UUIDs the driver reports. Without
    attestation these are self-reported, so the id is a declaration like
    the runner field; it stops casual repeat sessions, not a determined
    spoofer (spec 13 steering defences)."""
    joined = ",".join(sorted(uuids))
    return "rg_" + hashlib.sha256(joined.encode()).hexdigest()[:16]


def collect(run=subprocess.run):
    out = run(QUERY, capture_output=True, text=True, check=True, timeout=60)
    return parse(out.stdout)
