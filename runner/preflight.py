"""Preflight: a free 60-second check of a rented or hosted GPU box.

    python3 -m runner.preflight [--seconds 30] [--url URL] [--skip-download]

Answers one question before money goes into a long run: is this box
behaving like the card it reports? It looks for the failures renters hit
most, none of which a marketplace listing shows:

- clocks held down by heat or a hardware slowdown while the card reads busy
- memory or compute already taken by something outside your container
- a driver too old for the CUDA build your software was compiled against
- a PCIe link running narrower or slower than the card supports
- a download rate far below what the host advertises
- slow local disk

It is not a grade. No results are sent anywhere: every measurement stays
on the box and in preflight-report.json. (The download check only reads a
public file to time it.)

It is built for an exclusive box. On a shared or sliced box (MIG, or
--shared), use outside your slice can be a legitimate neighbour, so the two
foreign-use checks are shown as skipped with that reason, never failed. A grade is the separate, paid step
that measures the box against its peer class over a full session.

Exit code: 0 for pass or warn, 1 for fail (with --fail-on-warn, 1 for warn
too). The report file always carries pass, warn or fail for every check and
overall, so a warn is never mistaken for a clean pass.
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

PASS, WARN, FAIL = "pass", "warn", "fail"
_RANK = {PASS: 0, WARN: 1, FAIL: 2}

DEFAULT_URL = ("https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/resolve/main/"
               "model.safetensors")

# nvidia-smi clock event (throttle) reason bits.
REASONS = {
    0x1: "gpu_idle",
    0x2: "applications_clocks_setting",
    0x4: "sw_power_cap",
    0x8: "hw_slowdown",
    0x10: "sync_boost",
    0x20: "sw_thermal_slowdown",
    0x40: "hw_thermal_slowdown",
    0x80: "hw_power_brake_slowdown",
    0x100: "display_clock_setting",
}
# Reasons that mean the box is held back by heat or hardware. A power cap
# under a full GEMM is normal on most cards, so it is reported, not failed.
BAD_REASONS = {"hw_slowdown", "sw_thermal_slowdown", "hw_thermal_slowdown",
               "hw_power_brake_slowdown"}

# Thresholds. Plain numbers, published so a verdict can be checked by hand.
LIMITS = {
    "idle_foreign_memory_gb": (1.0, 2.0),   # warn, fail: memory in use before we start
    "idle_utilization_pct": (10, 30),       # warn, fail: compute busy before we start
    "clock_ratio": (0.80, 0.60),            # warn, fail: median SM clock / max SM clock under load
    "temperature_c": (85, 90),              # warn, fail: peak under load
    "download_mbs": (50, 10),               # warn, fail: MB/s pulled from the test URL
    "disk_write_mbs": (200, 50),            # warn, fail: MB/s sequential write with fsync
}

GPU_FIELDS = ["index", "name", "driver_version", "clocks.sm", "clocks.max.sm",
              "temperature.gpu", "power.draw", "power.limit", "utilization.gpu",
              "memory.used", "memory.total", "pcie.link.gen.current",
              "pcie.link.gen.max", "pcie.link.width.current", "pcie.link.width.max",
              "clocks_event_reasons.active"]
# Drivers before 535 call the reasons field clocks_throttle_reasons.active.
LEGACY_REASONS_FIELD = "clocks_throttle_reasons.active"


# ---------------------------------------------------------------- parsing

def _num(text):
    text = (text or "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def parse_gpu_csv(csv_text, fields=GPU_FIELDS):
    """One dict per GPU from `nvidia-smi --query-gpu=... --format=csv,noheader,nounits`.
    Fields nvidia-smi cannot report ("[N/A]", "[Not Supported]") become None."""
    out = []
    for line in csv_text.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        row = {}
        for key, val in zip(fields, parts):
            key = "reasons" if key in ("clocks_event_reasons.active", LEGACY_REASONS_FIELD) else key
            if key in ("name", "driver_version"):
                row[key] = val
            elif key == "reasons":
                row[key] = int(val, 16) if val.lower().startswith("0x") else None
            else:
                row[key] = _num(val)
        out.append(row)
    return out


def decode_reasons(mask):
    if not mask:
        return []
    return [name for bit, name in sorted(REASONS.items()) if mask & bit]


def parse_driver_cuda(smi_text):
    """The highest CUDA version the driver supports, from plain `nvidia-smi`."""
    m = re.search(r"CUDA Version:\s*([0-9]+)\.([0-9]+)", smi_text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_version(text):
    m = re.match(r"\s*([0-9]+)\.([0-9]+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


# ---------------------------------------------------------------- verdicts

def _grade_high_bad(value, warn, fail):
    """Larger is worse."""
    if value is None:
        return None
    return FAIL if value >= fail else WARN if value >= warn else PASS


def _grade_low_bad(value, warn, fail):
    """Smaller is worse."""
    if value is None:
        return None
    return FAIL if value < fail else WARN if value < warn else PASS


def evaluate(obs):
    """Turn observations into findings. Pure: no I/O, so every rule is
    tested on the CPU suite. A check with no observation is 'skipped'."""
    findings = []

    def add(check, status, detail):
        findings.append({"check": check, "status": status or "skipped", "detail": detail})

    if obs.get("gpu_error"):
        add("gpu_visible", FAIL, "no GPU visible to nvidia-smi (%s)" % obs["gpu_error"])

    shared = obs.get("shared")
    for gpu in obs.get("idle", []):
        i = int(gpu.get("index") or 0)
        if shared:
            for check in ("foreign_memory", "foreign_compute"):
                add("gpu%d.%s" % (i, check), None,
                    "shared or sliced box (%s): use outside your slice may be a "
                    "legitimate neighbour" % shared)
            continue
        used = gpu.get("memory.used")
        used_gb = None if used is None else used / 1024
        add("gpu%d.foreign_memory" % i,
            _grade_high_bad(used_gb, *LIMITS["idle_foreign_memory_gb"]),
            "%s GiB in use before this check started" % (
                "unknown" if used_gb is None else "%.2f" % used_gb))
        util = gpu.get("utilization.gpu")
        add("gpu%d.foreign_compute" % i,
            _grade_high_bad(util, *LIMITS["idle_utilization_pct"]),
            "%s%% busy before this check started" % ("unknown" if util is None else "%.0f" % util))

    for i, load in sorted((obs.get("load") or {}).items()):
        ratio = load.get("clock_ratio")
        add("gpu%s.clocks_under_load" % i,
            _grade_low_bad(ratio, *LIMITS["clock_ratio"]),
            "median SM clock %s of max under load" % (
                "unknown" if ratio is None else "%.0f%%" % (100 * ratio)))
        bad = sorted(set(load.get("reasons", [])) & BAD_REASONS)
        add("gpu%s.slowdown_reasons" % i, FAIL if bad else PASS,
            "active: %s" % (", ".join(bad) if bad else "none of %s" % ", ".join(sorted(BAD_REASONS))))
        temp = load.get("peak_temperature_c")
        add("gpu%s.temperature" % i,
            _grade_high_bad(temp, *LIMITS["temperature_c"]),
            "peak %s C under load" % ("unknown" if temp is None else "%.0f" % temp))
        gen, gen_max = load.get("pcie_gen"), load.get("pcie_gen_max")
        width, width_max = load.get("pcie_width"), load.get("pcie_width_max")
        if None in (gen, gen_max, width, width_max):
            add("gpu%s.pcie_link" % i, None, "link not reported")
        else:
            short = gen < gen_max or width < width_max
            add("gpu%s.pcie_link" % i, WARN if short else PASS,
                "Gen%d x%d under load (card supports Gen%d x%d)" % (gen, width, gen_max, width_max))

    driver_cuda, built = obs.get("driver_cuda"), obs.get("software_cuda")
    if driver_cuda and built:
        status = (FAIL if driver_cuda[0] < built[0]
                  else WARN if driver_cuda < built else PASS)
        add("driver_vs_cuda", status,
            "driver supports CUDA %d.%d, software built for CUDA %d.%d" % (driver_cuda + built))
    else:
        add("driver_vs_cuda", None, "CUDA versions not both known")

    dl = obs.get("download_mbs")
    add("download_rate", _grade_low_bad(dl, *LIMITS["download_mbs"]),
        "not measured" if dl is None else "%.1f MB/s from the test URL" % dl)
    disk = obs.get("disk_write_mbs")
    add("disk_write", _grade_low_bad(disk, *LIMITS["disk_write_mbs"]),
        "not measured" if disk is None else "%.0f MB/s sequential write" % disk)
    err = obs.get("compute_error")
    if err and err.startswith("ModuleNotFoundError"):
        add("compute", None, "load checks need torch with CUDA installed (%s)" % err)
    elif err:
        add("compute", FAIL, err)
    return findings


def verdict(findings):
    statuses = [f["status"] for f in findings if f["status"] in _RANK]
    overall = max(statuses, key=_RANK.get) if statuses else WARN
    problems = [f for f in findings if f["status"] in (WARN, FAIL)]
    if overall == FAIL:
        advice = "Consider swapping this box before a long run."
    elif overall == WARN:
        advice = "Usable, with the warnings above. Check they matter for your job."
    else:
        advice = ("No problems found in a short check. Before a long or costly run, a "
                  "RigGrade grade measures this box against its peer class.")
    return {"verdict": overall, "problems": [f["check"] for f in problems], "advice": advice}


# ---------------------------------------------------------------- probes

def query_gpus(run=subprocess.run):
    """Current state of every GPU; retries with the pre-535 field name."""
    for fields in (GPU_FIELDS, GPU_FIELDS[:-1] + [LEGACY_REASONS_FIELD]):
        out = run(["nvidia-smi", "--query-gpu=" + ",".join(fields),
                   "--format=csv,noheader,nounits"],
                  capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            return parse_gpu_csv(out.stdout, fields)
    raise RuntimeError("nvidia-smi query failed: %s" % out.stderr.strip()[:200])


def summarise_load(samples):
    """Per-GPU load summary from a list of query_gpus() snapshots."""
    by_gpu = {}
    for snap in samples:
        for gpu in snap:
            by_gpu.setdefault(int(gpu.get("index") or 0), []).append(gpu)
    out = {}
    for i, rows in by_gpu.items():
        ratios = [r["clocks.sm"] / r["clocks.max.sm"] for r in rows
                  if r.get("clocks.sm") and r.get("clocks.max.sm")]
        temps = [r["temperature.gpu"] for r in rows if r.get("temperature.gpu") is not None]
        reasons = set()
        for r in rows:
            reasons.update(decode_reasons(r.get("reasons")))
        last = rows[-1]
        out[i] = {"samples": len(rows),
                  "clock_ratio": round(statistics.median(ratios), 3) if ratios else None,
                  "peak_temperature_c": max(temps) if temps else None,
                  "reasons": sorted(reasons),
                  "pcie_gen": last.get("pcie.link.gen.current"),
                  "pcie_gen_max": last.get("pcie.link.gen.max"),
                  "pcie_width": last.get("pcie.link.width.current"),
                  "pcie_width_max": last.get("pcie.link.width.max")}
    return out


def load_and_sample(seconds, query, interval=2.0):
    """Run a matmul loop on every GPU for `seconds` while sampling nvidia-smi.
    Returns (load summary, TFLOPS per GPU)."""
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available to this process")
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    stop, tflops, samples = threading.Event(), {}, []

    def worker(dev):
        n = 4096
        x = torch.randn(n, n, device=dev, dtype=dtype)
        y = torch.randn(n, n, device=dev, dtype=dtype)
        done, t0 = 0, time.perf_counter()
        while not stop.is_set():
            for _ in range(10):
                x @ y
            torch.cuda.synchronize(dev)
            done += 10
        tflops[dev] = round(2 * n ** 3 * done / (time.perf_counter() - t0) / 1e12, 1)

    threads = [threading.Thread(target=worker, args=(d,), daemon=True)
               for d in range(torch.cuda.device_count())]
    for t in threads:
        t.start()
    time.sleep(min(5.0, seconds / 3))  # let clocks settle before sampling
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        samples.append(query())
        time.sleep(interval)
    stop.set()
    for t in threads:
        t.join(timeout=30)
    return summarise_load(samples), tflops


def software_cuda():
    try:
        import torch
        return parse_version(torch.version.cuda)
    except Exception:  # noqa: BLE001 - torch absent or CPU-only build
        return None


def measure_download(url, seconds=15.0, max_bytes=500 * 2 ** 20, opener=urllib.request.urlopen):
    got, t0 = 0, time.perf_counter()
    with opener(urllib.request.Request(url, headers={"User-Agent": "riggrade-preflight"}),
                timeout=20) as resp:
        while got < max_bytes and time.perf_counter() - t0 < seconds:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            got += len(chunk)
    elapsed = time.perf_counter() - t0
    return round(got / elapsed / 1e6, 1) if elapsed > 0 and got else None


def measure_disk(directory, mb=1024):
    block = os.urandom(1 << 20)
    fd, path = tempfile.mkstemp(dir=directory, prefix="riggrade-preflight-")
    try:
        t0 = time.perf_counter()
        with os.fdopen(fd, "wb") as fh:
            for _ in range(mb):
                fh.write(block)
            fh.flush()
            os.fsync(fh.fileno())
        return round(mb * 2 ** 20 / (time.perf_counter() - t0) / 1e6)
    finally:
        os.unlink(path)


class Probes:
    """The real I/O. Tests pass a fake with the same methods."""

    def gpus(self):
        return query_gpus()

    def mig_enabled(self):
        out = subprocess.run(["nvidia-smi", "--query-gpu=mig.mode.current",
                              "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=30)
        return out.returncode == 0 and "Enabled" in out.stdout

    def driver_cuda(self):
        out = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=30)
        return parse_driver_cuda(out.stdout)

    def software_cuda(self):
        return software_cuda()

    def load(self, seconds):
        return load_and_sample(seconds, query_gpus)

    def download(self, url):
        return measure_download(url)

    def disk(self, directory):
        return measure_disk(directory)


# ---------------------------------------------------------------- run

def run(probes, seconds=30.0, url=DEFAULT_URL, download=True, directory=".",
        out=sys.stdout, shared=False):
    obs, errors = {}, {}

    def attempt(name, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - record, carry on
            errors[name] = "%s: %s" % (type(exc).__name__, exc)
            return None

    obs["idle"] = attempt("gpus", probes.gpus) or []
    if "gpus" in errors:
        obs["gpu_error"] = errors["gpus"]
    if shared:
        obs["shared"] = "declared with --shared"
    elif getattr(probes, "mig_enabled", None) and attempt("mig", probes.mig_enabled):
        obs["shared"] = "MIG mode on"
    obs["driver_cuda"] = attempt("driver_cuda", probes.driver_cuda)
    obs["software_cuda"] = attempt("software_cuda", probes.software_cuda)
    loaded = attempt("load", lambda: probes.load(seconds))
    if loaded:
        obs["load"], obs["tflops"] = loaded
    elif "load" in errors:
        obs["compute_error"] = errors["load"]
    if download:
        obs["download_mbs"] = attempt("download", lambda: probes.download(url))
    obs["disk_write_mbs"] = attempt("disk", lambda: probes.disk(directory))

    findings = evaluate(obs)
    report = {"preflight": True,
              "note": "Not a grade. No results were sent anywhere.",
              "exclusive_box_assumed": not obs.get("shared"),
              "gpus": [{"index": g.get("index"), "name": g.get("name"),
                        "driver": g.get("driver_version")} for g in obs["idle"]],
              "tflops_per_gpu": obs.get("tflops"),
              "findings": findings, "errors": errors, "limits": LIMITS}
    report.update(verdict(findings))
    Path("preflight-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    for f in findings:
        out.write("%-6s %-28s %s\n" % (f["status"].upper(), f["check"], f["detail"]))
    out.write("\nVERDICT: %s. %s\n" % (report["verdict"].upper(), report["advice"]))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="RigGrade preflight check (free, not a grade)")
    ap.add_argument("--seconds", type=float, default=30.0, help="load phase length")
    ap.add_argument("--url", default=DEFAULT_URL, help="file to time a download against")
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--dir", default=".", help="directory for the disk write test")
    ap.add_argument("--shared", action="store_true",
                    help="box is shared or sliced: skip the foreign-use checks")
    ap.add_argument("--fail-on-warn", action="store_true",
                    help="exit 1 on warn as well as fail")
    args = ap.parse_args(argv)
    report = run(Probes(), args.seconds, args.url, not args.skip_download, args.dir,
                 shared=args.shared)
    bad = (FAIL, WARN) if args.fail_on_warn else (FAIL,)
    return 1 if report["verdict"] in bad else 0


if __name__ == "__main__":
    sys.exit(main())
