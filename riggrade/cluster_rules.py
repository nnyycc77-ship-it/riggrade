"""Cluster acceptance rules (docs/cluster-acceptance.md): pure functions
shared by the node-side evaluate step (runner.cluster, inside the benchmark
container) and the grading server's paid acceptance certificate. Standard
library only; every rule is tested on the CPU suite.
"""

import statistics

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
