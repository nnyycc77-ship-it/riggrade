"""Container entry point: one session from nonce to submitted bundle.

    python -m runner.main --endpoint https://... --workloads inference \
        --runner buyer [--host-wallet 0x...]

The container never pays. After the bundle is accepted it prints the
session id, the free preview and the grade URL; the payer buys the grade
from that URL over x402 with their own wallet.
"""

import argparse
import json
import os
import sys
import time

from . import bundle as bundle_mod
from . import stats
from .client import Client

SUSTAINED_S = 3600  # spec 5.2: sustained 60-minute run


def measure(backend, session_params, workloads, sustained_s=SUSTAINED_S,
            clock=time.monotonic):
    """Run the gate, micro-benchmarks and rulers through `backend`.

    backend contract:
      gate(workload) -> (results dict, free_memory_gb)
      micro() -> dict of micro-benchmark numbers
      ruler_pass(workload, params) -> (throughput, latencies list)
      sustained_sample(workload, params) -> throughput over about a minute
    The sustained run samples until `sustained_s` seconds of real time pass.
    Returns (gate, micro, metrics, raw outputs).
    """
    gate, metrics, raw = {}, {}, {}
    micro = backend.micro()
    raw["micro"] = micro
    order = session_params.get("pass_order") or list(workloads)
    for workload in order:
        results, free_gb = backend.gate(workload)
        gate[workload] = {"results": results, "free_memory_gb": free_gb}
        raw["gate." + workload] = gate[workload]
        if not all(results.values()):
            continue
        # One unmeasured warm-up pass first. On the third free T4 run the first
        # inference pass read 327 tokens/s against 650 for the next two (server
        # warm-up), which would read as inconsistency in the CV.
        warm, _lat = backend.ruler_pass(workload, session_params)
        raw["warmup." + workload] = {"throughput": warm}
        passes, latencies = [], []
        for _ in range(session_params["passes"]):
            throughput, lat = backend.ruler_pass(workload, session_params)
            passes.append(throughput)
            latencies.extend(lat or [])
        samples, t0 = [], clock()
        while True:
            value = backend.sustained_sample(workload, session_params)
            elapsed = clock() - t0
            samples.append((elapsed, value))
            if elapsed >= sustained_s:
                break
        metrics[workload] = {
            "throughput_passes": passes,
            "decay": stats.decay(samples),
            "p99_p50": stats.p99_p50(latencies) if workload == "inference" else None,
            "isolation_drop": None,
        }
        raw["ruler." + workload] = {"passes": passes, "latencies": latencies,
                                    "sustained": samples}
    return gate, micro, metrics, raw


def run(client, backend, identity, workloads, declared_runner, image_digest,
        host_wallet=None, out=sys.stdout, **measure_kw):
    session = client.new_session(workloads, declared_runner, host_wallet)
    started = client.start(session["session_id"])
    gate, micro, metrics, raw = measure(backend, started["params"], workloads, **measure_kw)
    raw["identity"] = identity
    bundle = bundle_mod.build(session, started["params"], image_digest, identity,
                              gate, micro, metrics, raw)
    result = client.submit(session["session_id"], bundle)
    report = {"session_id": session["session_id"], "accepted": result.get("accepted"),
              "preview": result.get("preview"),
              "grade_url": client.base + "/session/%s/grade" % session["session_id"]}
    out.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


class GpuBackend:
    """Real backend for a CUDA rig, used only inside the pinned image."""

    def __init__(self, identity):
        from . import gpu
        self.gpu = gpu
        self.identity = identity
        self.server = None

    def gate(self, workload):
        g = self.gpu
        results = {"memory_integrity": g.memory_integrity(),
                   "gemm": g.gemm(n=2048, reps=2)[0],
                   "memory_bandwidth": g.memory_bandwidth(gb=0.5, reps=2)[0],
                   "host_link": g.host_link(gb=0.5, reps=2)[0]}
        if workload == "training":
            results["card_link"] = g.card_link(self.identity["card_count"])[0]
        return results, g.free_memory_gb()

    def micro(self):
        g = self.gpu
        return {"memory_gb": self.identity["memory_gb"],
                "gemm_tflops": g.gemm()[1],
                "mem_bw_gbs": g.memory_bandwidth()[1],
                "host_link_gbs": g.host_link()[1],
                "allreduce_busbw_gbs": g.card_link(self.identity["card_count"])[1]}

    def ruler_pass(self, workload, params):
        from . import rulers
        if workload == "training":
            return rulers.training_pass(params), []
        if self.server is None:
            self.server = rulers.InferenceServer().__enter__()
        throughput, _ttft, gaps = self.server.run_pass(params)
        return throughput, gaps

    def sustained_sample(self, workload, params):
        from . import rulers
        if workload == "training":
            return rulers.training_pass(params, seconds=55)
        return self.server.run_pass(params)[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description="RigGrade benchmark container")
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--workloads", default="inference")
    ap.add_argument("--runner", choices=["buyer", "host"], required=True)
    ap.add_argument("--host-wallet")
    args = ap.parse_args(argv)
    from . import identity as identity_mod
    identity = identity_mod.collect()
    image = os.environ.get("RIGGRADE_IMAGE_DIGEST", "unpinned")
    backend = GpuBackend(identity)
    try:
        run(Client(args.endpoint), backend, identity, args.workloads.split(","),
            args.runner, image, args.host_wallet)
    finally:
        if backend.server is not None:
            backend.server.__exit__(None, None, None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
