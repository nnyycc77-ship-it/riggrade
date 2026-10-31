"""GPU gate checks and micro-benchmarks (spec section 4).

Needs torch with CUDA, as provided by the pinned image. Not exercised by
the CPU test suite: these functions run only on a rented or granted rig.
"""

import time


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


def gemm(device=0, n=8192, reps=20):
    """BF16 GEMM: correctness against FP32 on a small case, then TFLOPS."""
    torch = _torch()
    a = torch.randn(256, 256, device=device)
    b = torch.randn(256, 256, device=device)
    ref = a @ b
    got = (a.bfloat16() @ b.bfloat16()).float()
    correct = bool(torch.allclose(got, ref, rtol=5e-2, atol=5e-1))
    x = torch.randn(n, n, device=device, dtype=torch.bfloat16)
    y = torch.randn(n, n, device=device, dtype=torch.bfloat16)
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
