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
import sys
import time
from pathlib import Path

from riggrade.cluster_rules import (  # noqa: F401 - re-exported for callers and tests
    ACCEPT, EXCEPTION, FABRIC, METHOD_VERSION, NO_CLAIM, NOT_MEASURED, REJECT, STORAGE,
    STORAGE_BASIS, STRAGGLER, _RANK, _bands, evaluate, verdict)


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
