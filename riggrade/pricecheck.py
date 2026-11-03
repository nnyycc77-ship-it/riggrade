"""Price check: where one asking price for a GPU class sits against the
latest week of the free price index (riggrade.index). Owner decision,
3 Nov 2026: one call, 0.01 USDC, paid by x402. The figures are the same
listed series the free index serves; the call does the placing for an agent
that has a quote in hand. Information, not advice, an offer or a rate. It
names no host or marketplace and ranks no offers.
"""

PRODUCT = "riggrade_price_check"
VERSION = "price-check-v0"
RECENT_WEEKS = 8
AT_MEDIAN_PCT = 5.0
MAX_USD_PER_HOUR = 1000.0
POSTURE = (
    "Information, not advice, and not an offer, a rate or a forecast.",
    "Listed prices are asks on the long-tail listings market, not paid prices.",
    "A price says nothing about a box's health: run the free preflight on whatever you rent.",
)


class BadRequest(ValueError):
    pass


def parse(body):
    """(peer_class, usd_per_gpu_hour) from a request body, or BadRequest."""
    if not isinstance(body, dict):
        raise BadRequest("send {peer_class, usd_per_gpu_hour}")
    cls, price = body.get("peer_class"), body.get("usd_per_gpu_hour")
    if not isinstance(cls, str) or not cls:
        raise BadRequest("peer_class is required, e.g. h100-sxm-80 (GET /riggrade/v1/index lists them)")
    if (isinstance(price, bool) or not isinstance(price, (int, float))
            or not 0 < price <= MAX_USD_PER_HOUR):
        raise BadRequest(f"usd_per_gpu_hour must be a number above 0 and at most {MAX_USD_PER_HOUR:g}")
    return cls, float(price)


def known_classes(doc):
    return sorted(cid for cid, c in ((doc or {}).get("classes") or {}).items() if c.get("latest_week"))


def preview(doc, cls):
    """What the 402 and a 503 show before payment: whether the class is
    covered and which week would be read. No figures."""
    c = ((doc or {}).get("classes") or {}).get(cls)
    week = c.get("latest_week") if isinstance(c, dict) else None
    return {"peer_class": cls, "covered": bool(week), "latest_week": week,
            **({} if week else {"known": known_classes(doc)})}


def position(price, week):
    lo, med, hi = week["min_price"], week["median_price"], week["max_price"]
    if price < lo:
        return "below the listed minimum"
    if abs(price / med - 1) * 100 <= AT_MEDIAN_PCT:
        return f"at the median (within {AT_MEDIAN_PCT:g}%)"
    if price < med:
        return "between the listed minimum and the median"
    if price <= hi:
        return "between the median and the listed maximum"
    return "above the listed maximum"


def check(doc, cls, price):
    c = (doc.get("classes") or {})[cls]
    week = c["latest_week"]
    v = c["weeks"][week]
    recent = sorted(c["weeks"])[-RECENT_WEEKS:]
    medians = [c["weeks"][w]["median_price"] for w in recent]
    src = doc.get("source") or {}
    return {
        "read_this_first": list(POSTURE),
        "product": PRODUCT, "version": VERSION,
        "peer_class": cls, "usd_per_gpu_hour_asked": price,
        "unit": "USD per GPU per hour, on-demand, listed not paid",
        "latest_week": week,
        "week": {"usd_per_gpu_hour_median": v["median_price"],
                 "usd_per_gpu_hour_provider_median": v["provider_median_price"],
                 "usd_per_gpu_hour_min": v["min_price"], "usd_per_gpu_hour_max": v["max_price"],
                 "providers": v["provider_count"], "offerings": v["offering_count"]},
        "position": position(price, v),
        "vs_median_pct": round((price / v["median_price"] - 1) * 100, 1),
        "recent_weeks": [{"week": w, "usd_per_gpu_hour_median": m} for w, m in zip(recent, medians)],
        "recent_median_range": [min(medians), max(medians)],
        "source": {k: src.get(k) for k in ("title", "licence", "attribution")},
        "method_url": doc.get("method_url"),
        "free_series": "GET /riggrade/v1/index serves the same weekly figures free",
    }
