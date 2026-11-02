"""Pooled preflight results (owner decision, 2 Nov 2026: yes to pooling).

A renter who runs the free preflight may opt in to share an anonymised
summary (runner.preflight --share, or the MCP tool's share flag). The
service keeps one row per shared report with no identifier of the box, the
host or the sender: peer class, GPU model name and count, driver version,
the verdict, each check's status, and the measured numbers. No hostnames,
mount paths, IP addresses, user agents, rig ids or free text are stored.

Rows are appended to a file under the state directory and published only
as aggregates per peer class once at least MIN_N rows exist for that
class, and only when publication is switched on (RIGGRADE_POOL_PUBLISH=1)
after the method has been reviewed (docs/preflight-pool-method.md).
"""

import json
import math
import statistics
from pathlib import Path

from .classes import identify

MIN_N = 5
COLLECTION_BEGAN = "2026-11-02T02:26:00Z"   # deploy run 24, the first service that accepted shares
UNIT = "records (one opt-in shared preflight each; self-reported, subject to opt-in bias; not rigs or hosts)"
CONSENT = (
    "Sharing sends an anonymised summary to the RigGrade pool: the GPU model, count, memory size and "
    "driver version, the CUDA versions, the verdict, each check's status (pass, warn, fail, skipped), "
    "the measured numbers (clock ratio, peak temperature, memory bandwidth, download and disk rates, "
    "TFLOPS, ECC counts, host load, memory and swap), the disk filesystem type, the CPU governor name, "
    "and whether the box was declared exclusive. Nothing else is sent: no hostname, path, address, "
    "id, error or detail text. The service keeps exactly those fields plus the hour of receipt and the "
    "peer class, and records no sender address. Rows are kept for as long as the series runs and are "
    "published only as per-class aggregates once a class holds five records. Because nothing in a row "
    "identifies a sender, there is no per-sender deletion path. Method: "
    "https://github.com/nnyycc77-ship-it/riggrade/blob/main/docs/preflight-pool-method.md")
MAX_DOC_BYTES = 16 * 1024
STATUSES = ("pass", "warn", "fail", "skipped")
MEASURE_KEYS = ("clock_ratio", "peak_temperature_c", "mem_bw_gbs", "download_mbs", "disk_write_mbs",
                "tflops", "ecc_corrected_volatile", "ecc_uncorrected_volatile",
                "host_load_per_core", "host_memory_available_pct", "host_swap_used_pct")
TEXT_KEYS = {"gpu_name": 80, "driver": 32, "disk_fs": 32, "cuda_driver": 16, "cuda_software": 16,
             "host_cpu_governor": 24}


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isnan(value)


def clean(doc, hour):
    """The stored record from a shared document, or ValueError. Everything
    not listed here is dropped, so nothing identifying can slip in."""
    if not isinstance(doc, dict) or doc.get("pool") != 1:
        raise ValueError("not a pool document (pool must be 1)")
    rec = {"at_hour": int(hour)}
    for key, limit in TEXT_KEYS.items():
        v = doc.get(key)
        if isinstance(v, str) and v.strip():
            rec[key] = v.strip()[:limit]
    gpus = doc.get("gpus")
    rec["gpus"] = int(gpus) if _num(gpus) and 0 < gpus < 64 else 1
    mem = doc.get("memory_total_mb")
    rec["memory_total_mb"] = int(mem) if _num(mem) and 0 < mem < 10_000_000 else None
    verdict = doc.get("verdict")
    rec["verdict"] = verdict if verdict in ("pass", "warn", "fail") else "unknown"
    statuses = doc.get("statuses")
    if not isinstance(statuses, dict):
        raise ValueError("statuses must be an object of check: status")
    rec["statuses"] = {str(k)[:48]: v for k, v in statuses.items() if v in STATUSES}
    measures = doc.get("measures") if isinstance(doc.get("measures"), dict) else {}
    rec["measures"] = {k: round(float(measures[k]), 4) for k in MEASURE_KEYS if _num(measures.get(k))}
    rec["exclusive"] = bool(doc.get("exclusive", True))
    mem_gb = (rec["memory_total_mb"] or 0) / 1024
    rec["peer_class"] = identify(rec.get("gpu_name", ""), mem_gb) or "unknown_class"
    return rec


def append(path, rec):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


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


def aggregate(rows, min_n=MIN_N):
    """Per peer class: count, verdict shares, status shares per check and
    medians of each measure. A class under min_n publishes only its count."""
    by_class = {}
    for r in rows:
        by_class.setdefault(r.get("peer_class") or "unknown_class", []).append(r)
    out = {}
    for cid, rs in sorted(by_class.items()):
        n = len(rs)
        if n < min_n:
            out[cid] = {"records": n, "n": n, "status": "insufficient", "min_n": min_n}
            continue
        verdicts = {v: sum(1 for r in rs if r.get("verdict") == v) / n for v in ("pass", "warn", "fail")}
        checks = {}
        for r in rs:
            for check, status in (r.get("statuses") or {}).items():
                base = check.split(".", 1)[1] if check.startswith("gpu") and "." in check else check
                checks.setdefault(base, {s: 0 for s in STATUSES})[status] += 1
        check_shares = {c: {s: round(cnt[s] / sum(cnt.values()), 3) for s in STATUSES if cnt[s]}
                        for c, cnt in sorted(checks.items())}
        medians = {}
        for key in MEASURE_KEYS:
            vals = [r["measures"][key] for r in rs if key in (r.get("measures") or {})]
            if len(vals) >= min_n:
                medians[key] = {"median": round(statistics.median(vals), 3), "n": len(vals)}
        out[cid] = {"records": n, "n": n, "status": "published",
                    "verdict_share": {k: round(v, 3) for k, v in verdicts.items()},
                    "check_status_share": check_shares, "measures_median": medians,
                    "first_hour": min(r.get("at_hour", 0) for r in rs),
                    "last_hour": max(r.get("at_hour", 0) for r in rs)}
    return out
