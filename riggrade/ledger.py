"""The compute supply ledger's arithmetic and the paid feed document
(docs/compute-supply-method.md). Reads link 4 only (the reconstructed
index); links 1, 2, 3 and 5 enter as verified values are recorded, so every
signal that needs them reports "no signal" with the missing series named.
Every document carries the four posture statements of section 4b, first
and last. Nothing here forecasts, advises or offers."""

PRODUCT = "riggrade_ledger_feed"
METHOD_VERSION = "compute-supply-ledger-v0"
HOURS_PER_YEAR = 8760
BANDS = (0.4, 0.6, 0.8)
POSTURE = (
    "Information, not advice, and not an offer, a rate or a forecast.",
    (
        "This ledger covers the long-tail listings market only and says nothing about hyperscaler "
        "fleets or sponsors whose capacity never lists."
    ),
    "Marketplace listings are the marginal market, not fleet utilisation.",
    "Listed prices are asks, not paid prices.",
)
FLAT_END_PCT = 5.0
FLAT_BAND_PCT = 10.0
DEFAULT_WEEKS = 8
MAX_WEEKS = 52
# Section 4 signals and the series each needs; a signal is produced only
# when every series it names has a published value for the period.
SIGNALS = {
    "absorption_gap": ("chips", "buildout"),
    "hyperscale_absorption": ("chips", "sponsors"),
    "secondary_lead": ("secondary", "marketplace"),
    "yield_shortfall": ("sponsors", "marketplace"),
    "delivery_gap": ("rig", "marketplace"),
}
# Series with a published value today. "rig" (the preflight pool) counts
# only once a class holds five records; the feed reports it absent until
# then rather than reading the pool itself.
PUBLISHED = ("marketplace",)


def implied_revenue(median_usd_per_hour, band):
    """Gross USD per GPU-year at a utilisation band: arithmetic, not a forecast."""
    return round(median_usd_per_hour * HOURS_PER_YEAR * band, 0)


def spread(v):
    return round(v["max_price"] / v["min_price"], 2) if v.get("min_price") else None


def series_window(weeks, n=DEFAULT_WEEKS):
    keys = sorted(weeks)[-n:]
    return [(k, weeks[k]) for k in keys]


def is_flat(values):
    """Section 4: end within FLAT_END_PCT of start, no value outside a
    FLAT_BAND_PCT band around the start. Returns (flat, detail)."""
    if len(values) < 2 or not values[0]:
        return None, "fewer than two values"
    start, end = values[0], values[-1]
    end_move = (end - start) / start * 100
    band_breach = any(abs(v - start) / start * 100 > FLAT_BAND_PCT for v in values)
    flat = abs(end_move) <= FLAT_END_PCT and not band_breach
    breach = "yes" if band_breach else "no"
    return flat, (
        f"start {start:.4g}, end {end:.4g}, end move {end_move:+.1f}% (tolerance {FLAT_END_PCT:.0f}%), "
        f"band breach {breach} (band {FLAT_BAND_PCT:.0f}%)"
    )


def signals():
    """Every section 4 signal with its state: no signal until every series it
    names is published. The feed never fills a gap."""
    out = {}
    for name, needs in SIGNALS.items():
        missing = [s for s in needs if s not in PUBLISHED]
        out[name] = {"state": "no signal", "needs": list(needs), "missing_series": missing} if missing \
            else {"state": "not computed", "needs": list(needs), "missing_series": []}
    return out


def preview(doc, weeks=DEFAULT_WEEKS, classes=None):
    """What a paid call returns, described without the figures: free."""
    have = doc.get("classes") or {}
    chosen = sorted(have) if not classes else [c for c in sorted(have) if c in set(classes)]
    latest = [have[c].get("latest_week") for c in chosen if have[c].get("latest_week")]
    return {"classes": chosen, "weeks": weeks, "latest_week": max(latest) if latest else None,
            "signals_computed": 0, "series_published": list(PUBLISHED),
            "unknown_classes": sorted(set(classes or []) - set(have))}


def feed(doc, weeks=DEFAULT_WEEKS, classes=None):
    """The paid document: posture first and last, link 4 window per class,
    derived figures labelled, signals as they stand, provenance."""
    have = doc.get("classes") or {}
    chosen = sorted(have) if not classes else [c for c in sorted(have) if c in set(classes)]
    out = {"product": PRODUCT, "method": METHOD_VERSION,
           "index_method": doc.get("method"), "read_this_first": list(POSTURE),
           "unit": "USD per GPU per hour, on-demand, listed not paid", "classes": {}}
    for cid in chosen:
        c = have[cid]
        win = series_window(c.get("weeks") or {}, weeks)
        rows = [{"week": k, "median": v["median_price"], "provider_median": v["provider_median_price"],
                 "min": v["min_price"], "max": v["max_price"], "spread": spread(v),
                 "providers": v["provider_count"], "offerings": v["offering_count"]} for k, v in win]
        entry = {"latest_week": c.get("latest_week"), "weeks_returned": len(rows), "link4": rows}
        if rows:
            m = rows[-1]["median"]
            entry["implied_gross_usd_per_gpu_year"] = {
                "arithmetic": "median listed USD/GPU-hour x 8760 x band; gross, before power, hosting, "
                              "support and the marketplace's cut; listed, not paid",
                "bands": {f"{int(b * 100)}%": implied_revenue(m, b) for b in BANDS}}
            medians = [r["median"] for r in rows]
            offers = [float(r["offerings"]) for r in rows]
            f1, d1 = is_flat(medians)
            f2, d2 = is_flat(offers)
            entry["flat_tolerance"] = {"note": "shown in use; not a signal by itself",
                                       "median_price": {"flat": f1, "detail": d1},
                                       "offerings": {"flat": f2, "detail": d2}}
        out["classes"][cid] = entry
    imports = doc.get("imports") or []
    last = imports[-1] if imports else {}
    out["signals"] = signals()
    out["provenance"] = {"source": doc.get("source"), "last_import": last,
                         "corrections": len(doc.get("corrections") or []),
                         "rule": "history is append-only; gaps stay gaps; no value is adjusted by hand"}
    out["read_this_last"] = list(POSTURE)
    return out
