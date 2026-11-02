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
FLAT_RULE = (
    f"A series is flat over its window only when both hold: the end value is within {FLAT_END_PCT:.0f}% "
    f"of the start value, and no week in the window lies outside a {FLAT_BAND_PCT:.0f}% band around the "
    "start value. Either failing makes it not flat; the figure and the week that settle the label are "
    "printed beside it."
)
INDEX_METHOD_CITATION = (
    "docs/index-reconstructed-method.md, method v0, accepted for publication on 1 November 2026 under "
    "the version string the series carries (that string keeps the word draft because the method's own "
    "status line does, and the series' method field is append-only history)"
)
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


def flat_reading(window):
    """Section 4 flat rule (FLAT_RULE) on a window of (week, value) pairs.
    Returns the label with every figure that settles it: start, end, end
    move, and the widest band departure with its week when one breaches."""
    pairs = [(w, v) for w, v in window if v is not None]
    if len(pairs) < 2 or not pairs[0][1]:
        return {"flat": None, "detail": "fewer than two values"}
    (w0, start), (w1, end) = pairs[0], pairs[-1]
    end_move = (end - start) / start * 100
    moves = [(abs(v - start) / start * 100, (v - start) / start * 100, w, v) for w, v in pairs]
    widest = max(moves)
    breach = widest[0] > FLAT_BAND_PCT
    flat = abs(end_move) <= FLAT_END_PCT and not breach
    out = {"flat": flat, "start_week": w0, "start": start, "end_week": w1, "end": end,
           "end_move_pct": round(end_move, 1), "end_tolerance_pct": FLAT_END_PCT,
           "band_pct": FLAT_BAND_PCT, "band_breach": breach,
           "widest_departure_pct": round(widest[1], 1), "widest_departure_week": widest[2],
           "widest_departure_value": widest[3]}
    detail = (f"start {start:.4g} ({w0}), end {end:.4g} ({w1}), end move {end_move:+.1f}% "
              f"(tolerance {FLAT_END_PCT:.0f}%)")
    if breach:
        detail += (f"; band breach: {widest[3]:.4g} in week {widest[2]} is {widest[1]:+.1f}% from the "
                   f"start (band {FLAT_BAND_PCT:.0f}%)")
    else:
        detail += f"; no week outside the {FLAT_BAND_PCT:.0f}% band"
    out["detail"] = detail
    return out


def is_flat(values):
    """flat_reading on bare values: (flat, detail). Kept for callers that
    hold no week keys."""
    r = flat_reading([(f"#{i}", v) for i, v in enumerate(values)])
    return r["flat"], r["detail"]


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
           "index_method": doc.get("method"), "index_method_citation": INDEX_METHOD_CITATION,
           "read_this_first": list(POSTURE),
           "series": "reconstructed (link 4): a reconstructed history from third-party records, never "
                     "joined to a live series",
           "unit": "USD per GPU per hour, on-demand, listed not paid", "flat_rule": FLAT_RULE,
           "classes": {}}
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
            entry["flat_tolerance"] = {
                "note": "shown in use; not a signal by itself; rule in flat_rule",
                "median_price": flat_reading([(r["week"], r["median"]) for r in rows]),
                "offerings": flat_reading([(r["week"], float(r["offerings"])) for r in rows])}
        out["classes"][cid] = entry
    imports = doc.get("imports") or []
    last = imports[-1] if imports else {}
    out["signals"] = signals()
    out["provenance"] = {"source": doc.get("source"), "last_import": last,
                         "corrections": len(doc.get("corrections") or []),
                         "rule": "history is append-only; gaps stay gaps; no value is adjusted by hand"}
    out["read_this_last"] = list(POSTURE)
    return out
