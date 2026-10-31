"""Cluster acceptance test (docs/cluster-acceptance.md).

    python3 -m runner.cluster node --rank R --nodes N --master HOST:PORT [--storage PATH] --out node-R.json
    python3 -m runner.cluster evaluate --claims claims.json node-*.json --out acceptance.json

The node step needs GPUs, torch and NCCL. The evaluate step is pure
standard library, and every rule it applies is tested on the CPU suite.
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import statistics
import sys
import time
from pathlib import Path

METHOD_VERSION = "cluster-acceptance-v0-draft"
ACCEPT, EXCEPTION, REJECT, NOT_MEASURED, NO_CLAIM = (
    "accept", "exception", "reject", "not_measured", "no_claim")
_RANK = {ACCEPT: 0, EXCEPTION: 1, REJECT: 2}

STRAGGLER = (0.90, 0.80)   # accept at or above, exception at or above, else reject
FABRIC = (0.80, 0.60)      # measured over claimed
STORAGE = (0.80, 0.60)
STORAGE_BASIS = ("approximate: sum of per-node read rates; each node starts reading "
                 "as soon as the fabric all-reduce ends, with no synchronised start")


def _bands(ratio, bars):
    if ratio is None:
        return NOT_MEASURED
    accept, exception = bars
    return ACCEPT if ratio >= accept else EXCEPTION if ratio >= exception else REJECT


# ---------------------------------------------------------------- evaluate

def evaluate(claims, nodes):
    """claims: the provider's claims (any field may be absent).
    nodes: node reports, one per node that answered.
    Returns (checks, verdict). Pure."""
    checks = []

    def add(name, status, detail, **numbers):
        checks.append(dict({"check": name, "status": status, "detail": detail}, **numbers))

    # Inventory
    gpus = sum((n.get("identity") or {}).get("card_count", 0) for n in nodes)
    models = sorted({(n.get("identity") or {}).get("device_name") for n in nodes} - {None})
    problems = []
    if "nodes" in claims and len(nodes) < claims["nodes"]:
        problems.append("%d of %d claimed nodes answered" % (len(nodes), claims["nodes"]))
    if "gpus_per_node" in claims:
        short = [n["rank"] for n in nodes
                 if (n.get("identity") or {}).get("card_count", 0) < claims["gpus_per_node"]]
        if short:
            problems.append("fewer GPUs than claimed on node(s) %s" % short)
    if "gpu_model" in claims and models and models != [claims["gpu_model"]]:
        problems.append("GPU model %s, claimed %s" % (models, claims["gpu_model"]))
    if not nodes:
        problems.append("no node reports")
    add("inventory", REJECT if problems else ACCEPT,
        "; ".join(problems) or "%d nodes, %d GPUs, %s" % (len(nodes), gpus, models),
        nodes=len(nodes), gpus=gpus)

    # Image identity: every node must run the same container, and an
    # unpinned image is recorded as an exception.
    images = sorted({n.get("image") or "unrecorded" for n in nodes})
    if len(images) > 1:
        add("image", REJECT, "nodes ran different images: %s" % images, images=images)
    elif not images or images[0] in ("unrecorded", "unpinned"):
        add("image", EXCEPTION, "container image not pinned (%s)" % (images or ["none"])[0],
            images=images)
    else:
        add("image", ACCEPT, "all nodes ran %s" % images[0], images=images)

    # Node health (preflight on every node)
    verdicts = {n["rank"]: (n.get("preflight") or {}).get("verdict") for n in nodes}
    if any(v is None for v in verdicts.values()) or not verdicts:
        health = NOT_MEASURED
    elif "fail" in verdicts.values():
        health = REJECT
    elif "warn" in verdicts.values():
        health = EXCEPTION
    else:
        health = ACCEPT
    add("node_health", health, "preflight verdict per node: %s" % verdicts)

    # Stragglers: each node's mean GEMM TFLOPS against the cluster median
    per_node = {}
    for n in nodes:
        vals = [v for v in (n.get("tflops_per_gpu") or {}).values() if v]
        if vals:
            per_node[n["rank"]] = statistics.fmean(vals)
    if len(per_node) >= 2:
        med = statistics.median(per_node.values())
        ratios = {r: round(v / med, 3) for r, v in per_node.items()}
        worst = min(ratios.values())
        add("straggler", _bands(worst, STRAGGLER),
            "slowest node at %.0f%% of cluster median TFLOPS" % (100 * worst),
            ratios=ratios)
    else:
        add("straggler", NOT_MEASURED, "needs TFLOPS from at least 2 nodes")

    # Inter-node fabric
    fabric = next((n.get("fabric") for n in nodes if n.get("fabric")), None)
    claim = claims.get("inter_node_gbps_per_gpu")
    if len(nodes) < 2 and claims.get("nodes", len(nodes)) < 2:
        add("fabric", NO_CLAIM, "single node: no inter-node fabric to test")
    elif claim is None:
        add("fabric", NO_CLAIM, "no inter-node bandwidth claimed",
            busbw_gbs=(fabric or {}).get("busbw_gbs"))
    elif not fabric or fabric.get("busbw_gbs") is None:
        add("fabric", NOT_MEASURED, "all-reduce did not complete: %s"
            % ((fabric or {}).get("error") or "no result"))
    else:
        ratio = fabric["busbw_gbs"] / (claim / 8.0)
        add("fabric", _bands(ratio, FABRIC),
            "all-reduce bus bandwidth %.1f GB/s against %.1f GB/s claimed per GPU (%.0f%%)"
            % (fabric["busbw_gbs"], claim / 8.0, 100 * ratio),
            ratio=round(ratio, 3))

    # Storage: per-node concurrent read rates, summed
    rates = [n.get("storage_read_gbs") for n in nodes if n.get("storage_read_gbs") is not None]
    sclaim = claims.get("storage_read_gbs")
    if sclaim is None:
        add("storage", NO_CLAIM, "no storage rate claimed",
            measured_gbs=round(sum(rates), 2) if rates else None)
    elif not rates:
        add("storage", NOT_MEASURED, "no node measured storage reads")
    else:
        total = sum(rates)
        ratio = total / sclaim
        add("storage", _bands(ratio, STORAGE),
            "%.1f GB/s aggregate read from %d node(s) against %.1f GB/s claimed (%.0f%%); "
            "approximate: per-node rates summed, reads started together after the "
            "fabric test but not synchronised" % (total, len(rates), sclaim, 100 * ratio),
            ratio=round(ratio, 3), aggregate_basis=STORAGE_BASIS)

    return checks, verdict(checks, claims)


def verdict(checks, claims):
    graded = [c["status"] for c in checks if c["status"] in _RANK]
    worst = max(graded, key=_RANK.get) if graded else EXCEPTION
    claimed_unmeasured = [c["check"] for c in checks
                          if c["status"] == NOT_MEASURED
                          and (c["check"] in ("inventory", "node_health", "straggler")
                               or (c["check"] == "fabric" and "inter_node_gbps_per_gpu" in claims)
                               or (c["check"] == "storage" and "storage_read_gbs" in claims))]
    if worst == ACCEPT and claimed_unmeasured:
        worst = EXCEPTION
    return {"verdict": {ACCEPT: "accept", EXCEPTION: "accept with exceptions",
                        REJECT: "reject"}[worst],
            "exceptions": [c["check"] for c in checks if c["status"] == EXCEPTION]
                          + claimed_unmeasured,
            "rejections": [c["check"] for c in checks if c["status"] == REJECT]}


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evidence_pack(claims_path, node_paths, now=None):
    claims = json.loads(Path(claims_path).read_text(encoding="utf-8"))
    nodes = sorted((json.loads(Path(p).read_text(encoding="utf-8")) for p in node_paths),
                   key=lambda n: n.get("rank", 0))
    checks, result = evaluate(claims, nodes)
    now = now or dt.datetime.now(dt.timezone.utc)
    pack = {"method": METHOD_VERSION,
            "evaluated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "note": "Not a grade. What this cluster delivered during this test, "
                    "against what its provider claimed.",
            "claims": claims,
            "images": sorted({n.get("image") or "unrecorded" for n in nodes}),
            "inputs": {str(p): sha256_file(p) for p in [claims_path] + list(node_paths)},
            "nodes": nodes, "checks": checks}
    pack.update(result)
    return pack


# ---------------------------------------------------------------- node step (GPU)

def measure_storage(path, max_gb=20.0, seconds=30.0):
    """Read existing files under `path` (largest first) for up to `seconds`.
    Returns GB/s, or None if nothing was read. Page cache can flatter a
    re-read, so files are read once each."""
    files = sorted((p for p in Path(path).rglob("*") if p.is_file()),
                   key=lambda p: p.stat().st_size, reverse=True)
    got, t0, cap = 0, time.perf_counter(), int(max_gb * 1e9)
    for f in files:
        with open(f, "rb", buffering=0) as fh:
            while got < cap and time.perf_counter() - t0 < seconds:
                chunk = fh.read(16 << 20)
                if not chunk:
                    break
                got += len(chunk)
        if got >= cap or time.perf_counter() - t0 >= seconds:
            break
    elapsed = time.perf_counter() - t0
    return round(got / elapsed / 1e9, 2) if got and elapsed > 0 else None


def _fabric_worker(local_rank, node_rank, gpus, nodes, master, result, mb=1024, reps=20):
    import torch
    import torch.distributed as dist
    host, port = master.rsplit(":", 1)
    os.environ["MASTER_ADDR"], os.environ["MASTER_PORT"] = host, port
    world, rank = gpus * nodes, node_rank * gpus + local_rank
    dist.init_process_group("nccl", rank=rank, world_size=world)
    torch.cuda.set_device(local_rank)
    n = mb * 2 ** 20 // 4
    t = torch.ones(n, device=local_rank)
    for _ in range(3):
        dist.all_reduce(t)
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(reps):
        dist.all_reduce(t)
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - t0
    if local_rank == 0:
        size = n * 4
        result["busbw_gbs"] = round(size * reps / elapsed * 2 * (world - 1) / world / 1e9, 1)
        result["world"] = world
    dist.destroy_process_group()


def measure_fabric(node_rank, nodes, master):
    import torch
    import torch.multiprocessing as mp
    gpus = torch.cuda.device_count()
    result = mp.get_context("spawn").Manager().dict()
    mp.spawn(_fabric_worker, args=(node_rank, gpus, nodes, master, result),
             nprocs=gpus, join=True)
    return dict(result)


def run_node(rank, nodes, master, storage=None, preflight_probes=None,
             fabric_fn=measure_fabric, storage_fn=measure_storage, identity_fn=None):
    from . import preflight
    from . import identity as identity_mod
    report = {"rank": rank, "method": METHOD_VERSION, "errors": {},
              # The pinned image sets RIGGRADE_IMAGE_DIGEST; anything else is unpinned.
              "image": os.environ.get("RIGGRADE_IMAGE_DIGEST") or "unpinned"}
    try:
        report["identity"] = (identity_fn or identity_mod.collect)()
    except Exception as exc:  # noqa: BLE001
        report["errors"]["identity"] = str(exc)
    pf = preflight.run(preflight_probes or preflight.Probes(), seconds=30,
                       download=False, out=io.StringIO())
    report["preflight"] = {"verdict": pf["verdict"], "problems": pf["problems"]}
    report["tflops_per_gpu"] = pf.get("tflops_per_gpu")
    if nodes > 1:
        try:
            report["fabric"] = fabric_fn(rank, nodes, master)
        except Exception as exc:  # noqa: BLE001
            report["fabric"] = {"busbw_gbs": None, "error": "%s: %s" % (type(exc).__name__, exc)}
    if storage:
        try:
            report["storage_read_gbs"] = storage_fn(storage)
        except Exception as exc:  # noqa: BLE001
            report["errors"]["storage"] = str(exc)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cluster acceptance test (not a grade)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("node")
    n.add_argument("--rank", type=int, required=True)
    n.add_argument("--nodes", type=int, required=True)
    n.add_argument("--master", required=True, help="HOST:PORT reachable from every node")
    n.add_argument("--storage", help="shared storage path to time reads from")
    n.add_argument("--out", required=True)
    e = sub.add_parser("evaluate")
    e.add_argument("--claims", required=True)
    e.add_argument("--out", required=True)
    e.add_argument("reports", nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "node":
        report = run_node(args.rank, args.nodes, args.master, args.storage)
        Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
        print("node %d: preflight %s" % (args.rank, report["preflight"]["verdict"]))
        return 0
    pack = evidence_pack(args.claims, args.reports)
    Path(args.out).write_text(json.dumps(pack, indent=1, sort_keys=True) + "\n")
    for c in pack["checks"]:
        print("%-13s %-12s %s" % (c["check"], c["status"].upper(), c["detail"]))
    print("\nVERDICT: %s" % pack["verdict"].upper())
    return 1 if pack["verdict"] == "reject" else 0


if __name__ == "__main__":
    sys.exit(main())
