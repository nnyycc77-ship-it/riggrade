"""GPU gate checks and micro-benchmarks (spec section 4).

Needs torch with CUDA, as provided by the pinned image. Not exercised by
the CPU test suite: these functions run only on a rented or granted rig.
"""

import subprocess
import time

# Same bars as the preflight's foreign-use warnings: memory or compute already
# in use before the run starts means someone else holds the card.
BUSY_USED_GB = 1.0
BUSY_UTIL_PCT = 10


def sharing_snapshot(run=subprocess.run):
    """What else is on the cards right now, from nvidia-smi: memory in use (GiB)
    and utilization per GPU, and the number of compute processes. None where
    nvidia-smi does not answer."""
    def query(args):
        try:
            out = run(["nvidia-smi"] + args, capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout if out.returncode == 0 else None
    gpus = query(["--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"])
    apps = query(["--query-compute-apps=pid", "--format=csv,noheader"])
    used, util = [], []
    for row in (gpus or "").splitlines():
        parts = [x.strip() for x in row.split(",")]
        if len(parts) == 2 and all(p.replace(".", "", 1).isdigit() for p in parts):
            used.append(float(parts[0]) / 1024)
            util.append(float(parts[1]))
    return {"used_gb": used, "util_pct": util,
            "apps": len([r for r in (apps or "").splitlines() if r.strip()]) if apps is not None else None}


def busy(snapshot):
    """True when the snapshot shows another job on the cards."""
    if not snapshot:
        return False
    return any(u > BUSY_USED_GB for u in snapshot.get("used_gb") or []) or \
        any(u > BUSY_UTIL_PCT for u in snapshot.get("util_pct") or [])


def _torch():
    import torch  # noqa: WPS433 (imported lazily so CPU tests never need it)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available")
    return torch


def free_memory_gb(device=0):
    torch = _torch()
    free, _total = torch.cuda.mem_get_info(device)
    return free / 2 ** 30


def memory_integrity(device=0, fraction=0.9, chunk_gb=1.0):
    """Fill up to `fraction` of free memory in chunks with a known pattern
    and read it back. Returns True only with zero mismatches."""
    torch = _torch()
    free, _ = torch.cuda.mem_get_info(device)
    target = int(free * fraction)
    chunk = int(chunk_gb * 2 ** 30) // 4
    held, done, ok = [], 0, True
    try:
        while done + chunk * 4 <= target:
            t = torch.arange(chunk, dtype=torch.int32, device=device)
            t.bitwise_xor_(0x5A5A5A5A)
            held.append(t)
            done += chunk * 4
        for t in held:
            expect = torch.arange(t.numel(), dtype=torch.int32, device=device)
            expect.bitwise_xor_(0x5A5A5A5A)
            if not torch.equal(t, expect):
                ok = False
                break
            del expect
    finally:
        held.clear()
        torch.cuda.empty_cache()
    return ok and done > 0


def release_memory():
    """Return cached GPU memory to the driver. torch's caching allocator keeps
    what earlier steps used (the training ruler holds gigabytes), and a server
    started next in another process would see that memory as taken."""
    import gc
    torch = _torch()
    gc.collect()
    torch.cuda.empty_cache()
    free, total = torch.cuda.mem_get_info()
    return round(free / 2 ** 30, 2), round(total / 2 ** 30, 2)


def native_bf16(capability):
    """bfloat16 runs natively from compute capability 8.0 (Ampere) up.
    Recent torch also reports bf16 as supported on older cards, by
    emulation: on a T4 that ran GEMM at about 2.6 TFLOPS, and vLLM refuses
    it outright. So the capability decides, not is_bf16_supported()."""
    return tuple(capability)[0] >= 8


def preferred_dtype(device=0):
    """bfloat16 where the card runs it natively (graded runs), else float16."""
    torch = _torch()
    return "bfloat16" if native_bf16(torch.cuda.get_device_capability(device)) else "float16"


def gemm(device=0, n=8192, reps=20, dtype="bfloat16"):
    """GEMM (BF16 for graded runs): correctness against FP32 on a small
    case, then TFLOPS."""
    torch = _torch()
    dt = getattr(torch, dtype)
    a = torch.randn(256, 256, device=device)
    b = torch.randn(256, 256, device=device)
    ref = a @ b
    got = (a.to(dt) @ b.to(dt)).float()
    correct = bool(torch.allclose(got, ref, rtol=5e-2, atol=5e-1))
    x = torch.randn(n, n, device=device, dtype=dt)
    y = torch.randn(n, n, device=device, dtype=dt)
    for _ in range(3):
        x @ y
    torch.cuda.synchronize(device)
    t0 = time.perf_counter()
    for _ in range(reps):
        x @ y
    torch.cuda.synchronize(device)
    dt = time.perf_counter() - t0
    return correct, 2 * n ** 3 * reps / dt / 1e12


def memory_bandwidth(device=0, gb=2.0, reps=10):
    """Device-to-device copy bandwidth in GB/s (read plus write)."""
    torch = _torch()
    n = int(gb * 2 ** 30)
    src = torch.empty(n, dtype=torch.uint8, device=device)
    dst = torch.empty_like(src)
    dst.copy_(src)
    torch.cuda.synchronize(device)
    t0 = time.perf_counter()
    for _ in range(reps):
        dst.copy_(src)
    torch.cuda.synchronize(device)
    dt = time.perf_counter() - t0
    return True, 2 * n * reps / dt / 1e9


def host_link(device=0, gb=1.0, reps=5):
    """Pinned host-to-device copy bandwidth in GB/s."""
    torch = _torch()
    n = int(gb * 2 ** 30)
    host = torch.empty(n, dtype=torch.uint8, pin_memory=True)
    dev = torch.empty(n, dtype=torch.uint8, device=device)
    dev.copy_(host)
    torch.cuda.synchronize(device)
    t0 = time.perf_counter()
    for _ in range(reps):
        dev.copy_(host, non_blocking=True)
    torch.cuda.synchronize(device)
    dt = time.perf_counter() - t0
    return True, n * reps / dt / 1e9


def card_link(card_count):
    """NCCL all-reduce across every card, run as one process per card.
    Returns (completed, bus bandwidth GB/s). Single-card rigs skip it."""
    if card_count <= 1:
        return True, None
    import torch.multiprocessing as mp
    _torch()
    result = mp.get_context("spawn").Manager().dict()
    mp.spawn(_allreduce_worker, args=(card_count, result), nprocs=card_count, join=True)
    ok = all(result.get(r, False) for r in range(card_count))
    return ok, result.get("busbw")


def _allreduce_worker(rank, world, result, mb=256, reps=10):
    import os
    import torch
    import torch.distributed as dist
    os.environ.setdefault("MASTER_ADDR", "127.0.0.1")
    os.environ.setdefault("MASTER_PORT", "29511")
    dist.init_process_group("nccl", rank=rank, world_size=world)
    torch.cuda.set_device(rank)
    n = mb * 2 ** 20 // 4
    t = torch.ones(n, device=rank)
    dist.all_reduce(t)
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(reps):
        dist.all_reduce(t)
    torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    result[rank] = bool(torch.isfinite(t).all().item())
    if rank == 0:
        size = n * 4
        result["busbw"] = size * reps / dt * 2 * (world - 1) / world / 1e9
    dist.destroy_process_group()
