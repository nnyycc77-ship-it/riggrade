"""The compute supply ledger's weekly note, built from the data RigGrade
holds (docs/compute-supply-method.md, link 4 and section 3). Every page it
writes carries the four posture statements of section 4b. Nothing here
reads links 1, 2, 3 or 5; those enter only as verified values are recorded.

    python3 -m pricewatch.ledger_note --out docs/ledger/2026-11-02.md
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from riggrade import index as price_index

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


def implied_revenue(median_usd_per_hour, band):
    """Gross USD per GPU-year at a utilisation band: arithmetic, not a forecast."""
    return round(median_usd_per_hour * HOURS_PER_YEAR * band, 0)


def spread(v):
    return round(v["max_price"] / v["min_price"], 2) if v.get("min_price") else None


def series_window(weeks, n=8):
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


def build(doc, date):
    """The note as markdown lines. Every figure cites its series and week."""
    classes = doc.get("classes") or {}
    src = doc.get("source") or {}
    lines = [f"# Compute supply ledger, weekly note, {date}", "",
             f"Method {METHOD_VERSION} (docs/compute-supply-method.md); reconstructed index method {doc.get('method')}.",
             ""]
    lines += ["**Read this first.** " + " ".join(POSTURE), ""]
    lines += ["## Link 4, marketplace: listed on-demand price per peer class, latest week", "",
              f"Source: {src.get('title')}, {src.get('licence')}, with attribution: {src.get('attribution')}", "",
              "| Class | Week | Median USD/GPU-h | Provider median | Min | Max | Spread (max/min) | Providers | Offerings |",
              "|---|---|---|---|---|---|---|---|---|"]
    rows = []
    for cid, c in sorted(classes.items()):
        week = c.get("latest_week")
        if not week:
            lines.append(f"| {cid} | no on-demand weeks mapped | | | | | | | |")
            continue
        v = c["weeks"][week]
        rows.append((cid, week, v))
        lines.append(
            f"| {cid} | {week} | {v['median_price']:.3f} | {v['provider_median_price']:.3f} | "
            f"{v['min_price']:.3f} | {v['max_price']:.3f} | {spread(v)} | {v['provider_count']:d} | "
            f"{v['offering_count']:d} |"
        )
    lines += ["", "## Section 3, derived figures: implied gross revenue per GPU-year (arithmetic on link 4)", "",
              (
                  "Median listed price per GPU-hour × 8,760 hours × utilisation band. Gross, before power, hosting, "
                  "support and the marketplace's cut. Listed, not paid. The bands are assumptions, printed beside every figure."
              ), "",
              "| Class | Week | at 40% | at 60% | at 80% |", "|---|---|---|---|---|"]
    for cid, week, v in rows:
        m = v["median_price"]
        cells = " | ".join(f"{implied_revenue(m, b):.0f} USD (band {b * 100:.0f}%)" for b in BANDS)
        lines.append(f"| {cid} | {week} | {cells} |")
    lines += ["", "## Section 4, signals this week", "",
              (
                  "A signal is produced only when every series it names has a published value for the period. "
                  "Links 1, 2, 3 and 5 carry no recorded value yet, so the absorption gap, hyperscale absorption, "
                  "secondary lead and yield shortfall signals are **no signal (missing series: chips, buildout, "
                  "sponsors, secondary)**. The delivery gap signal is **no signal (missing series: rig, under five "
                  "records per class)**."
              ), "",
              "The one computation possible on link 4 alone, shown so the tolerance is seen in use (it is not a signal by itself):", ""]
    for cid, c in sorted(classes.items()):
        win = series_window(c.get("weeks") or {}, 8)
        if len(win) < 2:
            continue
        medians = [v["median_price"] for _, v in win]
        offers = [v["offering_count"] for _, v in win]
        f1, d1 = is_flat(medians)
        f2, d2 = is_flat([float(o) for o in offers])
        w1 = "flat" if f1 else "not flat"
        w2 = "flat" if f2 else "not flat"
        lines.append(
            f"- {cid}, window {win[0][0]} to {win[-1][0]} ({len(win)} weeks): median listed price {w1} ({d1}); "
            f"offerings {w2} ({d2})."
        )
    imports = doc.get("imports") or []
    last = imports[-1] if imports else {}
    lines += ["", "## Provenance", "",
              (
                  f"Last import of the source file: {last.get('retrieved_at')}, {last.get('bytes')} bytes, "
                  f"SHA-256 {last.get('sha256')}; source latest week {last.get('source_latest_week')}. "
                  f"Corrections on record: {len(doc.get('corrections') or [])}. "
                  "History is append-only; gaps stay gaps; no value is adjusted by hand."
              ), "",
              "**Read this last.** " + " ".join(POSTURE), ""]
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--series", default=str(price_index.RECONSTRUCTED))
    a = ap.parse_args(argv)
    doc = json.loads(Path(a.series).read_text(encoding="utf-8"))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(build(doc, a.date)) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
