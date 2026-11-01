"""Host fix-it report: the paid diagnosis after a preflight warning or fail
(decided 1 Nov 2026). Input is the preflight-report.json the free
check writes; output names, for every warn or fail finding, the likely
cause, the fix steps, who can act (host or renter) and which grade pillar
it threatens. Pure: no I/O, no network. Prices live in riggrade.endpoint.
"""

import re

PRODUCT = "riggrade_fixit_report"
VERSION = "fixit-v0"

COOLING = ["Clean dust from the card's heatsink, fans and case filters",
           "Check the fan curve under load (nvidia-smi --query-gpu=fan.speed --format=csv -l 1); "
           "a fan stuck low under load is a driver or firmware setting",
           "Give the card airflow: open slot spacing, intake and exhaust fans, lower ambient temperature",
           "On an older card, replace the thermal paste and pads"]
POWER = ["Check every PCIe power cable is seated at both ends; use separate cables, not a daisy-chained 8-pin",
         "Check PSU capacity against the total draw of all cards plus CPU",
         "Read the power limit and draw: nvidia-smi -q -d POWER"]


def _num(pattern, text, cast=float):
    m = re.search(pattern, text or "")
    return cast(m.group(1)) if m else None


def _rule(base, detail, status):
    """Returns (cause, fixes, who, confidence, pillar)."""
    if base == "foreign_memory":
        gb = _num(r"([\d.]+) GiB in use", detail)
        return ("Another process held GPU memory before the check started"
                + (" (%.2f GiB)" % gb if gb is not None else ""),
                ["On the box: nvidia-smi --query-compute-apps=pid,used_memory --format=csv to find it, then stop it",
                 "Host: stop leftover tenant processes before each handover; a renter sees this as a shared card",
                 "Renter: swap the box, or run the check with --shared if you knowingly rented a slice"],
                "host", "high" if gb is not None else "medium", "gate")
    if base == "foreign_compute":
        pct = _num(r"(\d+)% busy", detail, int)
        return ("Something else was using the GPU before the check started"
                + (" (%d%% busy)" % pct if pct is not None else ""),
                ["On the box: nvidia-smi to see the process; stop it or reboot the container",
                 "Host: no other tenant or background job on a card sold as exclusive",
                 "Renter: swap the box"],
                "host", "high" if pct is not None else "medium", "consistency")
    if base == "clocks_under_load":
        ratio = _num(r"median SM clock (\d+)%", detail, int)
        lowered = re.search(r"lowered to ([\d.]+) W \(card default ([\d.]+) W\)", detail or "")
        if lowered:
            lim, dflt = lowered.group(1), lowered.group(2)
            return ("The host lowered the power limit to %s W, below the card's default %s W, so clocks "
                    "sit at %s%% of max under load" % (lim, dflt, ratio),
                    ["Host: raise it back: sudo nvidia-smi -pl %s (persists until reboot; put it in a boot script)" % dflt,
                     "If the limit is deliberate (power or heat budget), say so in the listing; "
                     "a renter is paying for the default"],
                    "host", "high", "speed")
        if "default limit is unknown" in (detail or ""):
            return ("Clocks are held by a power cap and the card's default limit could not be read",
                    ["On the box: nvidia-smi -q -d POWER to read the current and default limits",
                     "If the current limit is below the default: sudo nvidia-smi -pl <default>",
                     "If nvidia-smi cannot read limits, the driver or permissions in the container are the issue"],
                    "host", "medium", "speed")
        return ("Clocks fell to %s%% of max under load with no power cap reported: heat or a hardware brake"
                % (ratio if ratio is not None else "an unknown share"),
                COOLING + ["Check the slowdown_reasons finding in the same report for which brake fired"],
                "host", "medium", "speed")
    if base == "slowdown_reasons":
        bad = re.search(r"bad reasons active: ([^;]+)", detail or "")
        reasons = [r.strip() for r in (bad.group(1).split(",") if bad else []) if r.strip() and r.strip() != "none"]
        thermal = any("thermal" in r for r in reasons)
        brake = any("power_brake" in r for r in reasons)
        if thermal and not brake:
            return ("The card hit a thermal slowdown under load (%s)" % ", ".join(reasons),
                    COOLING, "host", "high", "consistency")
        if brake:
            return ("The hardware power brake fired under load (%s): power delivery, not heat"
                    % ", ".join(reasons), POWER, "host", "high", "consistency")
        return ("A hardware slowdown fired under load (%s)" % (", ".join(reasons) or "unspecified"),
                POWER + COOLING, "host", "medium", "consistency")
    if base == "temperature":
        peak = _num(r"peak (\d+) C", detail, int)
        return ("Peak temperature %s C under load" % (peak if peak is not None else "too high"),
                COOLING, "host", "high" if peak is not None else "medium", "consistency")
    if base == "pcie_link":
        m = re.search(r"Gen(\d) x(\d+) under load \(card supports Gen(\d) x(\d+)\)", detail or "")
        got = "Gen%s x%s" % (m.group(1), m.group(2)) if m else "a narrower or slower link"
        cap = "Gen%s x%s" % (m.group(3), m.group(4)) if m else "its full link"
        return ("The card ran at %s while it supports %s: the slot, a riser, or a BIOS setting is limiting it"
                % (got, cap),
                ["Move the card to a full x16 slot wired to the CPU, not the chipset",
                 "Remove risers and extenders, or replace a cheap one",
                 "In BIOS, set the PCIe generation for that slot to Auto or the card's maximum",
                 "Check the slot is not sharing lanes with an NVMe drive or a second card"],
                "host", "high" if m else "medium", "gate")
    if base == "driver_vs_cuda":
        m = re.search(r"CUDA ([\d.]+).*?CUDA ([\d.]+)", detail or "")
        return ("The driver's CUDA version is older than the software's CUDA build"
                + (" (driver %s, software %s)" % (m.group(1), m.group(2)) if m else ""),
                ["Host: update the NVIDIA driver to one that supports the software's CUDA version, then reboot",
                 "Renter: use a container built for the driver's CUDA version, or swap the box"],
                "host", "high" if m else "medium", "gate")
    if base == "download_rate":
        mbs = _num(r"([\d.]+) MB/s", detail)
        return ("Download ran at %s MB/s" % (("%.0f" % mbs) if mbs is not None else "a low rate"),
                ["Host: check the uplink and NIC speed (ethtool <nic>), and whether tenants share one uplink",
                 "Renter: pre-stage model weights on local disk or a nearby mirror; pick a host in a closer region"],
                "both", "medium", "speed")
    if base == "disk_write":
        mbs = _num(r"([\d.]+) MB/s", detail)
        fs = re.search(r" on (\S+) at (\S+)", detail or "")
        rate = ("%.0f" % mbs) if mbs is not None else "a low rate"
        if fs and "network or layered storage" in (detail or ""):
            return ("Disk writes ran at %s MB/s because the workspace is on %s (mounted at %s): network "
                    "or layered storage, not a local drive" % (rate, fs.group(1), fs.group(2)),
                    ["Host: put tenant workspaces on local NVMe and mount it at the tenant's working path",
                     "Host: if the overlay root must stay, offer a local volume (docker -v /nvme/tenant:/workspace)",
                     "Renter: write checkpoints to the fastest local path the host offers (df -T to find it)"],
                    "host", "high", "consistency")
        if fs:
            return ("Disk writes ran at %s MB/s on a local %s filesystem (mounted at %s): a slow or busy drive, "
                    "or a thin-provisioned volume" % (rate, fs.group(1), fs.group(2)),
                    ["Host: check the drive behind that mount (lsblk, smartctl) and other tenants' I/O on it",
                     "Host: move tenant workspaces to NVMe",
                     "Renter: write checkpoints to the fastest local path the host offers"],
                    "host", "high", "consistency")
        return ("Disk writes ran at %s MB/s" % rate,
                ["Host: put tenant workspaces on local NVMe, not network or overlay storage",
                 "Check the mount (df, mount): a thin-provisioned or overlay volume is often the cause",
                 "Renter: write checkpoints to the fastest local path the host offers"],
                "host", "medium", "consistency")
    if base == "ecc_errors":
        cor = _num(r"(\d+) corrected", detail, int)
        unc = _num(r"(\d+) uncorrected", detail, int)
        pending = "retired pages pending" in (detail or "")
        if (unc or 0) > 0 or pending:
            return ("The card's memory reported %s uncorrected ECC error(s) since boot%s: a training run "
                    "on it can crash or silently corrupt" % (unc if unc is not None else "some",
                                                              "; pages are waiting to be retired" if pending else ""),
                    ["Host: nvidia-smi -q -d ECC,PAGE_RETIREMENT,ROW_REMAPPER for the full picture",
                     "Host: reset the card (nvidia-smi -r) or reboot so pending page retirements and row remaps apply",
                     "Host: if uncorrected errors come back, take the card out of service and RMA it",
                     "Renter: swap the box; do not run long training on it"],
                    "host", "high", "consistency")
        return ("The card's memory reported %s corrected ECC errors since boot: above the quiet level, "
                "an early sign of failing memory" % (cor if cor is not None else "many"),
                ["Host: nvidia-smi -q -d ECC,ROW_REMAPPER; watch whether the count keeps rising",
                 "Host: check cooling and power delivery, which drive correctable error rates up",
                 "Renter: fine for short jobs; for a long training run prefer a card with a clean count"],
                "host", "high" if cor is not None else "medium", "consistency")
    if base == "cpu_load":
        lpc = _num(r"load ([\d.]+) per core", detail)
        return ("The host CPU was already under load (%s per core) before this check started: other tenants "
                "or background jobs will starve the data pipeline feeding the GPU"
                % (("%.2f" % lpc) if lpc is not None else "high"),
                ["On the box: top or htop to see what is running; stop leftovers from a previous tenant",
                 "Host: pin tenants to CPU cores (cgroups or docker --cpuset-cpus) and sell the core count honestly",
                 "Renter: if data loading is your bottleneck, swap the box"],
                "host", "high" if lpc is not None else "medium", "speed")
    if base == "memory":
        pct = _num(r"(\d+)% of", detail, int)
        return ("Only %s%% of host RAM is available: data loaders and CUDA pinned buffers will swap or fail"
                % (pct if pct is not None else "little"),
                ["On the box: free -g and ps aux --sort=-rss to find who holds it",
                 "Host: stop leftover processes; size the RAM offered per tenant to the cards sold",
                 "Renter: lower num_workers and batch prefetch, or swap the box"],
                "host", "high" if pct is not None else "medium", "speed")
    if base == "swap":
        pct = _num(r"(\d+)% of swap", detail, int)
        return ("The host is %s%% into swap: memory pressure that stalls data loading unpredictably"
                % (pct if pct is not None else "deep"),
                ["On the box: free -g, vmstat 1 to see swapping; find the resident hog",
                 "Host: more RAM per tenant, or fewer tenants per box",
                 "Renter: reduce host-side memory use, or swap the box"],
                "host", "high" if pct is not None else "medium", "consistency")
    if base == "cpu_governor":
        return ("The CPU frequency governor is set to powersave: the cores feeding the GPU run slow",
                ["Host: sudo cpupower frequency-set -g performance (or write performance to "
                 "/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor), and make it persist at boot",
                 "Host: in BIOS, disable deep power-saving states for a compute box"],
                "host", "high", "speed")
    if base == "memory_bandwidth":
        gbs = _num(r"(\d+) GB/s", detail)
        return ("Device memory copy bandwidth measured %s GB/s, far below any current card's memory system: "
                "memory clocks held down, a degraded memory bus, or a heavily shared card"
                % (("%.0f" % gbs) if gbs is not None else "a low value"),
                ["On the box: nvidia-smi --query-gpu=clocks.mem,clocks.max.mem --format=csv under load",
                 "Host: check for a lowered memory clock (nvidia-smi -q -d CLOCK) and reset application clocks (nvidia-smi -rac)",
                 "Host: if the memory clock is right and bandwidth stays low, the card's memory is failing; "
                 "check ECC counts and consider RMA",
                 "Renter: swap the box"],
                "host", "medium", "speed")
    if base in ("gpu_visible", "compute"):
        return ("The GPU was not usable from inside the container",
                ["Host: install the NVIDIA container toolkit and run containers with --gpus all",
                 "Check the driver is loaded (nvidia-smi on the host)",
                 "Check the container's CUDA build matches the driver"],
                "host", "medium", "gate")
    return ("Finding outside the current rule set",
            ["Re-run the preflight with the current container and share the report"],
            "either", "low", "gate")


def items(report):
    out = []
    for f in report.get("findings") or []:
        if not isinstance(f, dict):
            continue  # not a finding object; the endpoint refuses such reports
        status = f.get("status")
        if status not in ("warn", "fail"):
            continue
        check = str(f.get("check", ""))
        gpu, base = (check.split(".", 1) if "." in check else (None, check))
        cause, fixes, who, confidence, pillar = _rule(base, f.get("detail", ""), status)
        out.append({"check": check, "gpu": gpu, "status": status, "detail": f.get("detail", ""),
                    "cause": cause, "fix": fixes, "who": who, "confidence": confidence,
                    "threatens": pillar})
    return out


def preview(report):
    """Free: how many findings the report would address, and which, without
    the causes and fixes."""
    found = items(report)
    skipped = [f.get("check") for f in report.get("findings") or []
               if isinstance(f, dict) and f.get("status") in (None, "skipped")]
    return {"fixable": len(found),
            "checks": [i["check"] for i in found],
            "fail": sum(1 for i in found if i["status"] == "fail"),
            "warn": sum(1 for i in found if i["status"] == "warn"),
            "not_measured": skipped,
            "verdict": report.get("verdict")}


def diagnose(report):
    found = items(report)
    by_who = {}
    for i in found:
        by_who[i["who"]] = by_who.get(i["who"], 0) + 1
    return {"product": PRODUCT, "version": VERSION,
            "verdict": report.get("verdict"),
            "gpus": report.get("gpus"),
            "items": found,
            "by_who": by_who,
            "not_measured": preview(report)["not_measured"],
            "next_step": "Apply the fixes, then re-run the free preflight on the same box. A grade "
                         "measures the result against the class bar.",
            "statement": "Causes are inferred from the preflight measurements; the fixes are the "
                         "usual ones for each cause, not a promise that one applies to this box."}
