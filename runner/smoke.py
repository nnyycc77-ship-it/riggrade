"""Smoke test: proves the GPU code paths run on a real card, at no cost.

    python3 -m runner.smoke [--minutes 2] [--skip-inference]

Built for a free notebook GPU (for example a T4 on Colab or Kaggle). It is
NOT a grade: it uses a small inference model, a short sustained run and a
local in-process endpoint with the sale off, on any card, including cards
that are not a v0 peer class. Each step is timed and its error recorded,
so the report always prints, and a failing step never hides the others.
The report is also written to smoke-report.json.
"""

import argparse
import json
import secrets
import sys
import tempfile
import time
import traceback
from pathlib import Path

SMOKE_IMAGE = "smoke-unpinned"
SMOKE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"  # Apache-2.0, fits a 16 GB card


class Step:
    def __init__(self, report, name):
        self.report, self.name = report, name

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        entry = {"seconds": round(time.perf_counter() - self.t0, 2),
                 "ok": exc_type is None}
        if exc_type is not None:
            entry["error"] = "%s: %s" % (exc_type.__name__, exc)
            entry["trace"] = traceback.format_exc(limit=3)
        self.report["steps"][self.name] = entry
        return True  # record and carry on


def run(gpu, rulers, identity_mod, minutes=2.0, inference=True, out=sys.stdout):
    from riggrade.endpoint import GradingApp
    from . import bundle as bundle_mod
    from . import stats
    from .local import InProcessClient

    report = {"smoke": True, "note": "Not a grade. Code-path check only.", "steps": {}}
    ident, dtype, server = None, "bfloat16", None
    with Step(report, "identity"):
        ident = identity_mod.collect()
        report["identity"] = ident
    with Step(report, "dtype"):
        dtype = gpu.preferred_dtype()
        report["dtype"] = dtype
    gate = {"results": {}, "free_memory_gb": None}
    with Step(report, "gate.free_memory"):
        gate["free_memory_gb"] = round(gpu.free_memory_gb(), 2)
    with Step(report, "gate.memory_integrity"):
        gate["results"]["memory_integrity"] = gpu.memory_integrity(fraction=0.5)
    micro = {}
    with Step(report, "micro.gemm"):
        ok, tflops = gpu.gemm(n=4096, reps=10, dtype=dtype)
        gate["results"]["gemm"], micro["gemm_tflops"] = ok, round(tflops, 1)
    with Step(report, "micro.memory_bandwidth"):
        ok, bw = gpu.memory_bandwidth(gb=1.0, reps=5)
        gate["results"]["memory_bandwidth"], micro["mem_bw_gbs"] = ok, round(bw, 1)
    with Step(report, "micro.host_link"):
        ok, hl = gpu.host_link(gb=0.5, reps=3)
        gate["results"]["host_link"], micro["host_link_gbs"] = ok, round(hl, 1)
    if ident and ident.get("card_count", 1) > 1:
        with Step(report, "micro.card_link"):
            ok, busbw = gpu.card_link(ident["card_count"])
            gate["results"]["card_link"], micro["allreduce_busbw_gbs"] = ok, busbw
    report["gate"], report["micro"] = gate, micro

    params = {"passes": 3, "pass_order": ["training"] + (["inference"] if inference else []),
              "training": {"micro_batch": 8, "sequence_length": 512},
              "inference": {"prompt_tokens": 256, "output_tokens": 128, "concurrency": 4}}
    metrics = {}
    with Step(report, "ruler.training"):
        passes = [rulers.training_pass(params, seconds=20, dtype=dtype) for _ in range(3)]
        metrics["training"] = {"throughput_passes": [round(p, 1) for p in passes]}
    if inference:
        with Step(report, "ruler.inference.server_start"):
            server = rulers.InferenceServer(model=SMOKE_MODEL, dtype=dtype,
                                            extra_args=["--max-model-len", "2048",
                                                        "--gpu-memory-utilization", "0.6"]).__enter__()
        if server is not None:
            with Step(report, "ruler.inference.passes"):
                passes, gaps = [], []
                for _ in range(3):
                    tps, _ttft, g = server.run_pass(params)
                    passes.append(round(tps, 1))
                    gaps.extend(g)
                metrics["inference"] = {"throughput_passes": passes,
                                        "p99_p50": stats.p99_p50(gaps)}
            with Step(report, "ruler.inference.sustained"):
                samples, t0 = [], time.monotonic()
                while time.monotonic() - t0 < minutes * 60:
                    samples.append((time.monotonic() - t0, server.run_pass(params)[0]))
                metrics["inference"]["sustained_samples"] = len(samples)
                # Decay needs two 5-minute windows; a short smoke run reports None.
                metrics["inference"]["decay"] = stats.decay(samples)
            with Step(report, "ruler.inference.server_stop"):
                server.__exit__(None, None, None)
    report["metrics"] = metrics

    with Step(report, "endpoint.session"):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"RIGGRADE_SECRET": secrets.token_hex(16),
                   "RIGGRADE_IMAGE_DIGESTS": SMOKE_IMAGE,
                   "RIGGRADE_SALE_LOG": str(Path(tmp) / "sales.jsonl")}
            app = GradingApp(env, lambda: int(time.time()))
            client = InProcessClient(app)
            workloads = sorted(metrics) or ["training"]
            session = client.new_session(workloads, "host")
            started = client.start(session["session_id"])
            for w in metrics:
                metrics[w].setdefault("decay", 0.0)
            b = bundle_mod.build(session, started["params"], SMOKE_IMAGE, ident or {},
                                 {w: gate for w in workloads}, micro, metrics,
                                 {"smoke_report": {"steps": report["steps"]}})
            result = client.submit(session["session_id"], b)
            report["endpoint"] = {"status": result.get("_status"),
                                  "accepted": result.get("accepted"),
                                  "preview": result.get("preview"),
                                  "errors": result.get("errors")}

    report["summary"] = {"steps_ok": sum(s["ok"] for s in report["steps"].values()),
                         "steps_total": len(report["steps"]),
                         "failed": sorted(k for k, s in report["steps"].items() if not s["ok"])}
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    Path("smoke-report.json").write_text(text + "\n", encoding="utf-8")
    out.write(text + "\n")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="RigGrade smoke test (not a grade)")
    ap.add_argument("--minutes", type=float, default=2.0)
    ap.add_argument("--skip-inference", action="store_true")
    args = ap.parse_args(argv)
    from . import gpu, rulers
    from . import identity as identity_mod
    report = run(gpu, rulers, identity_mod, args.minutes, not args.skip_inference)
    return 0 if not report["summary"]["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
