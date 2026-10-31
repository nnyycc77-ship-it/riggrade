"""Pure aggregation for the listed-price series (method sections 3 and 5).

Input is a list of normalised offers:
    {"gpu_name": str, "gpu_memory_gb": float, "num_gpus": int,
     "total_price_per_hour": float, "verified": bool or None}
No identifiers enter this module, so none can reach the output.
"""

from riggrade.classes import PEER_CLASSES, identify

MIN_OFFERS = 10


def weighted_percentile(pairs, q):
    """pairs: (value, weight). Percentile q in [0, 1], weight-aware: the
    smallest value whose cumulative weight reaches q of the total."""
    pairs = sorted((v, w) for v, w in pairs if w > 0)
    if not pairs:
        return None
    total = sum(w for _v, w in pairs)
    acc = 0.0
    for value, weight in pairs:
        acc += weight
        if acc >= q * total - 1e-12:
            return value
    return pairs[-1][0]


def _summary(pairs, verified_gpus=None, known_gpus=None):
    gpus = sum(w for _v, w in pairs)
    out = {"offers": len(pairs), "gpus": gpus}
    if len(pairs) < MIN_OFFERS:
        out["status"] = "insufficient"
        return out
    out["status"] = "ok"
    out.update({"p25": round(weighted_percentile(pairs, 0.25), 4),
                "median": round(weighted_percentile(pairs, 0.50), 4),
                "p75": round(weighted_percentile(pairs, 0.75), 4)})
    if known_gpus:
        out["verified_share"] = round(verified_gpus / known_gpus, 3)
    return out


def by_class(offers):
    """One source's snapshot -> {class_id: summary}, plus unmatched count."""
    pairs, verified, known, unmatched = {}, {}, {}, 0
    for o in offers:
        n = int(o.get("num_gpus") or 0)
        price = o.get("total_price_per_hour")
        if n <= 0 or price is None or price <= 0:
            unmatched += 1
            continue
        cls = identify(o.get("gpu_name"), o.get("gpu_memory_gb"))
        if cls is None:
            unmatched += 1
            continue
        pairs.setdefault(cls, []).append((price / n, n))
        if o.get("verified") is not None:
            known[cls] = known.get(cls, 0) + n
            verified[cls] = verified.get(cls, 0) + (n if o["verified"] else 0)
    classes = {c: _summary(p, verified.get(c), known.get(c)) for c, p in pairs.items()}
    return {"classes": classes, "unmatched_offers": unmatched}, pairs


def combined(per_source_pairs, per_source_summary):
    """Method 3: median across sources that met the minimum sample, per class."""
    pooled = {}
    for source, pairs in per_source_pairs.items():
        for cls, items in pairs.items():
            if per_source_summary[source]["classes"][cls]["status"] == "ok":
                pooled.setdefault(cls, []).extend(items)
    out = {}
    for cls in PEER_CLASSES:
        items = pooled.get(cls, [])
        out[cls] = ({"status": "ok", "gpus": sum(w for _v, w in items),
                     "median": round(weighted_percentile(items, 0.5), 4)}
                    if items else {"status": "missing"})
    return out


def performance_adjusted(median_price_per_gpu_hour, throughputs_per_second, min_rigs=5):
    """Method 5: price per million units of measured work (for inference,
    tokens). `throughputs_per_second` is one first-session value per graded
    rig. Returns None until `min_rigs` rigs exist."""
    vals = sorted(throughputs_per_second)
    if median_price_per_gpu_hour is None or len(vals) < min_rigs:
        return None
    mid = len(vals) // 2
    med = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
    return round(median_price_per_gpu_hour / (med * 3600) * 1e6, 4)
