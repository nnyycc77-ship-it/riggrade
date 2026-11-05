"""Container entry point: one session from nonce to submitted bundle.

    python -m runner.main --endpoint https://... --workloads inference \
        --runner buyer [--host-wallet 0x...]

The container never pays. After the bundle is accepted it asks for the free,
no-wallet grade while the free trial runs (owner decision, 5 Nov 2026) and
prints it, so a person sees the grade as soon as the run ends. Outside the
trial, or with RIGGRADE_AUTO_GRADE=0 (riggrade-monitor's idle grading, which
grades with its standing claim instead), it prints the session id, the free
preview and the grade URL; opened in a browser that URL is the checkout.
"""

import argparse
import json
import os
import sys
import time

from . import bundle as bundle_mod
from . import stats
from .client import Client, EndpointError

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
    # The server sets sustained_s to 0 for a quick check (no 60-minute run).
    sustained_s = session_params.get("sustained_s", sustained_s)
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
        while sustained_s > 0:
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
        host_wallet=None, out=sys.stdout, auto_grade=True, mode="full", **measure_kw):
    session = client.new_session(workloads, declared_runner, host_wallet, mode=mode)
    started = client.start(session["session_id"])
    gate, micro, metrics, raw = measure(backend, started["params"], workloads, **measure_kw)
    raw["identity"] = identity
    bundle = bundle_mod.build(session, started["params"], image_digest, identity,
                              gate, micro, metrics, raw)
    result = client.submit(session["session_id"], bundle)
    report = {"session_id": session["session_id"], "accepted": result.get("accepted"),
              "preview": result.get("preview"),
              "grade_url": client.base + "/session/%s/grade" % session["session_id"]}
    if auto_grade and result.get("accepted"):
        report.update(free_grade(client, session["session_id"], out))
    out.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


UNITS = {"inference": "output tokens/s", "training": "training tokens/s"}


def summary_lines(doc):
    """The grade in plain words: what was measured, not only the grade letter.
    While a GPU class has no reference yet, speed cannot be ranked, so the
    measured numbers are the useful part of the result."""
    pack = doc.get("evidence_pack") or {}
    lines = []
    for label in doc.get("labels") or []:
        workload = label.get("workload_class")
        cls = (pack.get("classes") or {}).get(workload) or {}
        if cls.get("gate_failed"):
            lines.append(f"{workload}: health checks FAILED: {', '.join(cls['gate_failed'])}")
            continue
        if cls.get("withdrawn"):
            lines.append(f"{workload}: the card does not match its reported model ({cls['withdrawn']})")
            continue
        if not cls:
            lines.append(f"{workload}: not rated ({label.get('reason', 'no class')})")
            continue
        lines.append(f"{workload}: health checks passed")
        if cls.get("measured_throughput"):
            lines.append(f"  speed measured: {cls['measured_throughput']:.1f} {UNITS.get(workload, 'per s')}")
        cm = cls.get("consistency_measures") or {}
        steady = label.get("pillars", {}).get("consistency")
        if "cv" in cm:
            lines.append(f"  steadiness: grade {steady} (spread {cm['cv'] * 100:.1f}% across passes)")
        if isinstance(cm.get("decay"), (int, float)):
            lines.append(f"  heat test (60 min): {cm['decay'] * 100:.1f}% slower at the end")
        elif label.get("form") == "quick":
            lines.append("  heat test: not run (quick check; run the full grade for it)")
        basis = cls.get("reference_basis") or {}
        if cls.get("speed_ratio") is not None:
            lines.append(f"  speed vs other {label.get('peer_class')} rigs: {cls['speed_ratio'] * 100:.0f}% of the class median")
        else:
            lines.append(f"  speed rank: not yet; the {label.get('peer_class')} reference is still building "
                         f"({basis.get('rigs', 0)} of {basis.get('min_rigs', 5)} rigs)")
    return lines


def free_grade(client, session_id, out):
    """Ask for the no-wallet grade; on success print it in plain words."""
    try:
        doc = client.grade_free(session_id)
    except EndpointError as exc:
        doc = {"error": str(exc)}
    if not doc.get("labels"):
        out.write("No free grade right now (%s). Open the grade_url below in a browser to get it.\n"
                  % (doc.get("error") or "the service did not grade it"))
        return {"graded": False}
    out.write("\nYour RigGrade result (free, no wallet used, nothing charged):\n")
    for line in summary_lines(doc):
        out.write("  " + line + "\n")
    out.write("Badge: " + "; ".join(str(b) for b in doc.get("badges") or []) + "\n\n")
    return {"graded": True, "grade": {"labels": doc.get("labels"), "badges": doc.get("badges"),
                                      "evidence_pack": doc.get("evidence_pack"),
                                      "free_until": doc.get("free_until")}}


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
    ap.add_argument("--quick", action="store_true",
                    help="about 10 minutes: health checks, speed and steadiness, no 60-minute heat test")
    args = ap.parse_args(argv)
    from . import identity as identity_mod
    identity = identity_mod.collect()
    image = os.environ.get("RIGGRADE_IMAGE_DIGEST", "unpinned")
    backend = GpuBackend(identity)
    try:
        run(Client(args.endpoint), backend, identity, args.workloads.split(","),
            args.runner, image, args.host_wallet,
            auto_grade=os.environ.get("RIGGRADE_AUTO_GRADE", "1") != "0",
            mode="quick" if args.quick else "full")
    finally:
        if backend.server is not None:
            backend.server.__exit__(None, None, None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
