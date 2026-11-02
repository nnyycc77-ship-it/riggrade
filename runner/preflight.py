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
- slow local disk, and which filesystem it is on
- ECC errors on cards that have ECC (uncorrected ones fail a training run)
- a host starving the card: CPU load, little free RAM, swap, a powersave governor
- device memory bandwidth, a short copy sample

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
    "ecc_corrected_errors": (10, 1000),     # warn, fail: corrected ECC errors since boot (volatile)
    "host_load_per_core": (1.0, 2.0),       # warn, fail: 1-minute load average per CPU, before our load
    "host_memory_available_pct": (20, 10),  # warn, fail: MemAvailable as % of MemTotal
    "host_swap_used_pct": (10, 50),         # warn, fail: swap in use as % of swap total
    "mem_bw_gbs": (100, 50),                # warn, fail: device-to-device copy bandwidth
}
# Filesystems that are network or layered storage: slow disk writes there are
# the mount, not the drive.
SLOW_FS = {"nfs", "nfs4", "cifs", "smb3", "overlay", "9p", "virtiofs", "fuse", "fuse.sshfs",
           "fuse.s3fs", "fuse.gcsfuse", "fuse.rclone", "ceph", "glusterfs", "lustre"}

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

def parse_ecc_csv(csv_text):
    """nvidia-smi ECC rows: index, mode, corrected volatile, uncorrected
    volatile, corrected aggregate, uncorrected aggregate, retired pages
    pending. {index: {...}}; [N/A] fields are None (consumer cards)."""
    out = {}
    for line in (csv_text or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7 or _num(parts[0]) is None:
            continue
        out[int(_num(parts[0]))] = {
            "mode": None if parts[1].startswith("[") else parts[1],
            "corrected_volatile": _num(parts[2]), "uncorrected_volatile": _num(parts[3]),
            "corrected_aggregate": _num(parts[4]), "uncorrected_aggregate": _num(parts[5]),
            "retired_pages_pending": (None if parts[6].startswith("[")
                                      else parts[6].lower() in ("yes", "true", "1"))}
    return out


def parse_host(loadavg_text, meminfo_text, cpus, governor=None):
    """Host starvation signals from /proc/loadavg and /proc/meminfo."""
    out = {"cpus": cpus, "governor": (governor or "").strip() or None}
    try:
        out["load1"] = float((loadavg_text or "").split()[0])
    except (ValueError, IndexError):
        out["load1"] = None
    mem = {}
    for line in (meminfo_text or "").splitlines():
        if ":" in line:
            key, _, rest = line.partition(":")
            val = _num(rest.strip().split()[0]) if rest.strip() else None
            mem[key.strip()] = val
    total, avail = mem.get("MemTotal"), mem.get("MemAvailable")
    out["mem_total_gb"] = None if total is None else round(total / 2 ** 20, 1)
    out["mem_available_pct"] = (None if not total or avail is None
                                else round(100.0 * avail / total, 1))
    st, sf = mem.get("SwapTotal"), mem.get("SwapFree")
    out["swap_used_pct"] = (None if not st else round(100.0 * (st - (sf or 0)) / st, 1))
    out["load1_per_core"] = (None if out["load1"] is None or not cpus
                             else round(out["load1"] / cpus, 2))
    return out


def find_mount(mounts_text, path):
    """The mount point and filesystem type that hold `path` (longest
    matching mount point in /proc/mounts), or (None, None)."""
    best = (None, None)
    for line in (mounts_text or "").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        point, fstype = parts[1].replace("\\040", " "), parts[2]
        if path == point or path.startswith(point.rstrip("/") + "/") or point == "/":
            if best[0] is None or len(point) > len(best[0]):
                best = (point, fstype)
    return best


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
        seen = sorted(set(load.get("reasons", [])) - {"gpu_idle"})
        bad = sorted(set(seen) & BAD_REASONS)
        status = _grade_low_bad(ratio, *LIMITS["clock_ratio"])
        detail = "median SM clock %s of max under load" % (
            "unknown" if ratio is None else "%.0f%%" % (100 * ratio))
        if status in (WARN, FAIL) and not bad and "sw_power_cap" in seen:
            # Low clocks under full load with only the power cap active is how
            # power-limited cards (a 70 W T4, for example) are meant to run, as
            # long as the limit is the card's own default. A limit the host has
            # lowered is a warning, never a pass.
            limit, default = (obs.get("power_limits") or {}).get(int(i), (None, None))
            if limit and default and limit < 0.95 * default:
                status = WARN
                detail += ("; held by a power limit the host lowered to %.0f W "
                           "(card default %.0f W)" % (limit, default))
            elif limit and default:
                status = PASS
                detail += ("; held by the card's own power limit (%.0f W, the default), "
                           "which is by design" % default)
            else:
                status = WARN
                detail += "; held by a power cap, and the card's default limit is unknown"
        add("gpu%s.clocks_under_load" % i, status, detail)
        add("gpu%s.slowdown_reasons" % i, FAIL if bad else PASS,
            "bad reasons active: %s; all reasons seen: %s" % (
                ", ".join(bad) or "none", ", ".join(seen) or "none"))
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

    for i, ecc in sorted((obs.get("ecc") or {}).items()):
        unc, cor = ecc.get("uncorrected_volatile"), ecc.get("corrected_volatile")
        if ecc.get("mode") not in ("Enabled",) or (unc is None and cor is None):
            add("gpu%s.ecc_errors" % i, None,
                "ECC %s on this card; consumer cards have none" % (
                    "disabled" if ecc.get("mode") == "Disabled" else "not reported"))
            continue
        pending = ecc.get("retired_pages_pending")
        status = _grade_high_bad(cor, *LIMITS["ecc_corrected_errors"])
        if (unc or 0) > 0 or pending:
            status = FAIL
        add("gpu%s.ecc_errors" % i, status,
            "ECC since boot: %s corrected, %s uncorrected (aggregate %s / %s)%s" % (
                "?" if cor is None else "%d" % cor, "?" if unc is None else "%d" % unc,
                "?" if ecc.get("corrected_aggregate") is None else "%d" % ecc["corrected_aggregate"],
                "?" if ecc.get("uncorrected_aggregate") is None else "%d" % ecc["uncorrected_aggregate"],
                "; retired pages pending a reset" if pending else ""))

    host = obs.get("host")
    if host:
        lpc = host.get("load1_per_core")
        add("host.cpu_load", _grade_high_bad(lpc, *LIMITS["host_load_per_core"]),
            "unknown load" if lpc is None else "1-minute load %.2f per core (%s CPUs) before our load started"
            % (lpc, host.get("cpus")))
        avail = host.get("mem_available_pct")
        add("host.memory", _grade_low_bad(avail, *LIMITS["host_memory_available_pct"]),
            "unknown memory" if avail is None else "%.0f%% of %s GB RAM available" % (
                avail, host.get("mem_total_gb")))
        swap = host.get("swap_used_pct")
        if swap is None:
            add("host.swap", PASS, "no swap configured")
        else:
            add("host.swap", _grade_high_bad(swap, *LIMITS["host_swap_used_pct"]),
                "%.0f%% of swap in use" % swap)
        gov = host.get("governor")
        add("host.cpu_governor", None if gov is None else (WARN if gov == "powersave" else PASS),
            "governor not readable" if gov is None else "CPU frequency governor is %s" % gov)

    for i, bw in sorted((obs.get("mem_bw_gbs") or {}).items()):
        add("gpu%s.memory_bandwidth" % i, _grade_low_bad(bw, *LIMITS["mem_bw_gbs"]),
            "not measured" if bw is None else "%.0f GB/s device-to-device copy" % bw)

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
    mount = obs.get("disk_mount") or {}
    where = ""
    if mount.get("fstype"):
        where = " on %s at %s" % (mount["fstype"], mount.get("mountpoint") or "?")
        if mount["fstype"] in SLOW_FS or mount["fstype"].startswith("fuse"):
            where += " (network or layered storage)"
    add("disk_write", _grade_low_bad(disk, *LIMITS["disk_write_mbs"]),
        "not measured" if disk is None else "%.0f MB/s sequential write%s" % (disk, where))
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
    # Native bf16 only from compute capability 8.0; older cards emulate it slowly.
    dtype = torch.bfloat16 if torch.cuda.get_device_capability(0)[0] >= 8 else torch.float16
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


def measure_bandwidth(seconds=3.0, mb=256):
    """Device-to-device copy bandwidth per GPU in GB/s (counts bytes read
    plus written), a short sample of the memory system."""
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available to this process")
    out = {}
    for dev in range(torch.cuda.device_count()):
        src = torch.empty(mb << 20, dtype=torch.uint8, device=dev)
        dst = torch.empty_like(src)
        dst.copy_(src)
        torch.cuda.synchronize(dev)
        copies, t0 = 0, time.perf_counter()
        while time.perf_counter() - t0 < seconds:
            for _ in range(5):
                dst.copy_(src)
            torch.cuda.synchronize(dev)
            copies += 5
        out[dev] = round(2 * (mb << 20) * copies / (time.perf_counter() - t0) / 1e9)
    return out


def read_host(cpus=None):
    """Host starvation signals; any file that cannot be read is left None."""
    def read(path):
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return ""
    return parse_host(read("/proc/loadavg"), read("/proc/meminfo"),
                      cpus or os.cpu_count(),
                      read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"))


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

    def power_limits(self):
        """{gpu index: (enforced limit W, default limit W)}."""
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,power.limit,power.default_limit",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return {}
        rows = {}
        for line in out.stdout.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3 and _num(parts[0]) is not None:
                rows[int(_num(parts[0]))] = (_num(parts[1]), _num(parts[2]))
        return rows

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

    def ecc(self):
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,ecc.mode.current,"
                              "ecc.errors.corrected.volatile.total,ecc.errors.uncorrected.volatile.total,"
                              "ecc.errors.corrected.aggregate.total,ecc.errors.uncorrected.aggregate.total,"
                              "retired_pages.pending", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=30)
        return parse_ecc_csv(out.stdout) if out.returncode == 0 else {}

    def host(self):
        return read_host()

    def cards(self):
        """Per-card identity for the record: UUID hash and board serial where
        exposed; the UUID itself is never written (runner.identity)."""
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid,serial",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=30)
        return parse_cards_csv(out.stdout) if out.returncode == 0 else None

    def mount(self, directory):
        try:
            mounts = Path("/proc/mounts").read_text(encoding="utf-8")
        except OSError:
            return {}
        point, fstype = find_mount(mounts, os.path.realpath(directory))
        return {"mountpoint": point, "fstype": fstype} if fstype else {}

    def bandwidth(self):
        return measure_bandwidth()


# ---------------------------------------------------------------- run

def idle_snapshot(probes, samples=5, interval=None, sleep=time.sleep):
    """The idle state, as the median of several samples. A single sample can
    still carry utilisation from work that ended a second earlier (seen on a
    T4 right after another job: 35% busy with 0 GiB in use)."""
    interval = getattr(probes, "idle_interval", 0.5) if interval is None else interval
    snaps = []
    for k in range(samples):
        snaps.append(probes.gpus())
        if k < samples - 1 and interval:
            sleep(interval)
    out = []
    for i, gpu in enumerate(snaps[-1]):
        row = dict(gpu)
        for key in ("memory.used", "utilization.gpu"):
            vals = [s[i].get(key) for s in snaps if i < len(s) and s[i].get(key) is not None]
            row[key] = statistics.median(vals) if vals else None
        row["idle_samples"] = len(snaps)
        out.append(row)
    return out


DEFAULT_ENDPOINT = "https://riggrade-674098696181.us-central1.run.app/riggrade/v1"


def parse_cards_csv(csv_text):
    """[{index, card_id, serial}] and the rig id from `index,uuid,serial` rows."""
    from . import identity
    cards, uuids = [], []
    for line in csv_text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[1].startswith("GPU-"):
            continue
        serial = parts[2] if len(parts) > 2 and parts[2] not in identity.NOT_REPORTED else None
        cards.append({"index": _num(parts[0]), "card_id": identity.card_id(parts[1]), "serial": serial})
        uuids.append(parts[1])
    if not cards:
        return None
    return {"rig_id": identity.rig_id(uuids), "cards": cards}


def measures_of(obs):
    """The measured numbers behind the findings, as a flat dict (first GPU
    for per-GPU values, every GPU under per_gpu). Numbers only."""
    m = {}
    loads = obs.get("load") or {}
    per_gpu = {}
    for i, load in sorted(loads.items()):
        per_gpu[str(i)] = {k: load.get(k) for k in ("clock_ratio", "peak_temperature_c") if load.get(k) is not None}
    if per_gpu:
        first = per_gpu[sorted(per_gpu)[0]]
        m.update(first)
        m["per_gpu"] = per_gpu
    tflops = obs.get("tflops") or {}
    if isinstance(tflops, dict) and tflops:
        vals = [v for v in tflops.values() if isinstance(v, (int, float))]
        if vals:
            m["tflops"] = min(vals)
    bw = obs.get("mem_bw_gbs")
    if isinstance(bw, dict) and bw:
        vals = [v for v in bw.values() if isinstance(v, (int, float))]
        if vals:
            m["mem_bw_gbs"] = min(vals)
    elif isinstance(bw, (int, float)):
        m["mem_bw_gbs"] = bw
    for key in ("download_mbs", "disk_write_mbs"):
        if isinstance(obs.get(key), (int, float)):
            m[key] = obs[key]
    mount = obs.get("disk_mount") or {}
    if isinstance(mount, dict) and mount.get("fstype"):
        m["disk_fs"] = str(mount["fstype"])
    ecc = obs.get("ecc") or {}
    if isinstance(ecc, dict) and ecc:
        first = ecc[sorted(ecc)[0]] or {}
        for src, dst in (("corrected_volatile", "ecc_corrected_volatile"),
                         ("uncorrected_volatile", "ecc_uncorrected_volatile")):
            if isinstance(first.get(src), (int, float)):
                m[dst] = first[src]
    host = obs.get("host") or {}
    if isinstance(host, dict):
        for src, dst in (("load_per_core", "host_load_per_core"),
                         ("memory_available_pct", "host_memory_available_pct"),
                         ("swap_used_pct", "host_swap_used_pct")):
            if isinstance(host.get(src), (int, float)):
                m[dst] = host[src]
        if host.get("cpu_governor"):
            m["host_cpu_governor"] = str(host["cpu_governor"])
    return m


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


# The record's consent text; riggrade.record.CONSENT must read the same (a test holds it).
RECORD_CONSENT = (
    "Recording sends, with your explicit opt-in, an entry to the public RigGrade GPU record: the "
    "per-card identity (a hash of each card's driver UUID, never the UUID itself, and the board "
    "serial where the driver exposes it), the rig identity hash, the GPU model, count, memory and "
    "driver, the verdict, each check's status, the measured numbers, whether the box was declared "
    "exclusive, shared or virtualised, and who you declared as the runner (host, buyer or riggrade). "
    "Nothing else is sent: no hostname, path, address, wallet, owner, location or free text. The "
    "entry is public once accepted and cannot be withdrawn: the record is append-only, a later run "
    "supersedes, nothing is deleted. An entry declared as a buyer's run on someone else's rig is held "
    "and not served until the rig's host consents. Method: "
    "https://github.com/nnyycc77-ship-it/riggrade/blob/main/docs/gpu-record-method.md"
)


def record_doc(report, runner):
    """The record entry the runner sends with --record: the anonymised
    summary plus the per-card identity block and the declared runner."""
    doc = anonymise(report)
    doc.pop("pool", None)
    doc.update({"record": 1, "rig_id": report.get("rig_id"), "cards": report.get("cards"),
                "declared_runner": runner, "attested": False,
                "virtualised": not bool(report.get("exclusive_box_assumed", True))})
    return doc


def record_send(doc, endpoint=None, opener=None, timeout=10):
    return share(doc, endpoint=endpoint, opener=opener, timeout=timeout, path="/record")


def anonymise(report):
    """The shareable summary: peer-class inputs, verdict, each check's
    status, the measured numbers. No hostnames, paths, ids, free text or
    error strings; the detail strings are dropped on purpose."""
    gpus = report.get("gpus") or []
    first = gpus[0] if gpus else {}
    measures = {k: v for k, v in (report.get("measures") or {}).items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)}
    doc = {"pool": 1, "gpu_name": first.get("name"), "driver": first.get("driver"),
           "gpus": len(gpus) or 1, "memory_total_mb": first.get("memory_total_mb"),
           "verdict": report.get("verdict"),
           "statuses": {f["check"]: f["status"] for f in report.get("findings", []) if isinstance(f, dict)},
           "measures": measures, "exclusive": bool(report.get("exclusive_box_assumed", True))}
    for key in ("disk_fs", "host_cpu_governor"):
        if (report.get("measures") or {}).get(key):
            doc[key] = str(report["measures"][key])
    return {k: v for k, v in doc.items() if v is not None}


def share(doc, endpoint=None, opener=None, timeout=10, path="/preflight/share"):
    """POST the anonymised summary; returns the server's answer or an
    error dict. Never raises: sharing must not change the verdict."""
    import urllib.error
    import urllib.request
    url = (endpoint or os.environ.get("RIGGRADE_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/") + path
    data = json.dumps(doc).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                           "User-Agent": "riggrade-preflight"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"accepted": False, "error": "HTTP %s" % exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"accepted": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def run(probes, seconds=30.0, url=DEFAULT_URL, download=True, directory=".",
        out=sys.stdout, shared=False):
    obs, errors = {}, {}

    def attempt(name, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - record, carry on
            errors[name] = "%s: %s" % (type(exc).__name__, exc)
            return None

    obs["idle"] = attempt("gpus", lambda: idle_snapshot(probes)) or []
    if "gpus" in errors:
        obs["gpu_error"] = errors["gpus"]
    if shared:
        obs["shared"] = "declared with --shared"
    elif getattr(probes, "mig_enabled", None) and attempt("mig", probes.mig_enabled):
        obs["shared"] = "MIG mode on"
    if getattr(probes, "power_limits", None):
        obs["power_limits"] = attempt("power_limits", probes.power_limits) or {}
    if getattr(probes, "ecc", None):
        obs["ecc"] = attempt("ecc", probes.ecc) or {}
    if getattr(probes, "host", None):
        obs["host"] = attempt("host", probes.host)
    obs["driver_cuda"] = attempt("driver_cuda", probes.driver_cuda)
    obs["software_cuda"] = attempt("software_cuda", probes.software_cuda)
    loaded = attempt("load", lambda: probes.load(seconds))
    if loaded:
        obs["load"], obs["tflops"] = loaded
    elif "load" in errors:
        obs["compute_error"] = errors["load"]
    if loaded and getattr(probes, "bandwidth", None):
        obs["mem_bw_gbs"] = attempt("bandwidth", probes.bandwidth)
    if download:
        obs["download_mbs"] = attempt("download", lambda: probes.download(url))
    obs["disk_write_mbs"] = attempt("disk", lambda: probes.disk(directory))
    if getattr(probes, "mount", None):
        obs["disk_mount"] = attempt("mount", lambda: probes.mount(directory)) or {}

    if getattr(probes, "cards", None):
        obs["cards"] = attempt("cards", probes.cards)
    findings = evaluate(obs)
    report = {"preflight": True, "measures": measures_of(obs),
              "note": "Not a grade. No results were sent anywhere.",
              "exclusive_box_assumed": not obs.get("shared"),
              "gpus": [{"index": g.get("index"), "name": g.get("name"),
                        "driver": g.get("driver_version")} for g in obs["idle"]],
              "tflops_per_gpu": obs.get("tflops"),
              # Identity for the GPU record: hashes and board serials only;
              # kept on the box, never in the anonymised share.
              "rig_id": (obs.get("cards") or {}).get("rig_id"),
              "cards": (obs.get("cards") or {}).get("cards"),
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
    ap.add_argument("--share", action="store_true",
                    help="opt in: after the check, send an anonymised summary to the RigGrade pool. "
                         "Sent: GPU model, count, memory, driver and CUDA versions, the verdict, each "
                         "check's status, the measured numbers, the disk filesystem type, the CPU "
                         "governor, and whether the box was declared exclusive. Not sent: hostname, "
                         "paths, addresses, ids, errors, detail text. Kept while the series runs, "
                         "published only as per-class aggregates at five records; no per-sender "
                         "deletion path, since nothing identifies you. The exact wording is printed "
                         "before sending; --dry-share prints the summary and sends nothing.")
    ap.add_argument("--record", action="store_true",
                    help="opt in: after the check, send an entry to the public RigGrade GPU record "
                         "(per-card identity hashes and serials where exposed, plus the anonymised "
                         "summary); requires --runner; the consent text is printed first")
    ap.add_argument("--runner", choices=("host", "buyer", "riggrade"), default=None,
                    help="who is running this box for --record: its host, a buyer renting it, or riggrade")
    ap.add_argument("--dry-share", action="store_true",
                    help="print the anonymised summary that --share would send, and send nothing")
    ap.add_argument("--endpoint", default=None, help="service URL for --share (default: RIGGRADE_ENDPOINT)")
    ap.add_argument("--fail-on-warn", action="store_true",
                    help="exit 1 on warn as well as fail")
    args = ap.parse_args(argv)
    report = run(Probes(), args.seconds, args.url, not args.skip_download, args.dir,
                 shared=args.shared)
    bad = (FAIL, WARN) if args.fail_on_warn else (FAIL,)
    if args.record:
        if not args.runner:
            ap.error("--record requires --runner host|buyer|riggrade")
        doc = record_doc(report, args.runner)
        sys.stdout.write("\nRECORD CONSENT: " + RECORD_CONSENT + "\n")
        sys.stdout.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        try:
            answer = record_send(doc, endpoint=args.endpoint)
            sys.stdout.write("record: %s\n" % json.dumps(answer, sort_keys=True))
        except Exception as exc:  # noqa: BLE001 - the local report stands whatever the network did
            sys.stdout.write("record not sent: %s\n" % exc)
    if args.share or args.dry_share:
        doc = anonymise(report)
        sys.stdout.write("\n" + CONSENT + "\n\nSummary to send:\n" + json.dumps(doc, indent=1, sort_keys=True) + "\n")
        if args.share:
            answer = share(doc, args.endpoint)
            sys.stdout.write("Shared with the RigGrade pool: %s\n" % json.dumps(answer, sort_keys=True))
        else:
            sys.stdout.write("Dry run: nothing was sent.\n")
    return 1 if report["verdict"] in bad else 0


if __name__ == "__main__":
    sys.exit(main())
