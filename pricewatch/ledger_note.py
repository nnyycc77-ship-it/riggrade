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
from riggrade.ledger import (  # noqa: F401 - re-exported for the note's callers and tests
    BANDS,
    FLAT_BAND_PCT,
    FLAT_END_PCT,
    FLAT_RULE,
    HOURS_PER_YEAR,
    INDEX_METHOD_CITATION,
    METHOD_VERSION,
    POSTURE,
    flat_reading,
    implied_revenue,
    is_flat,
    series_window,
    spread,
)


def build(doc, date):
    """The note as markdown lines. Every figure cites its series and week."""
    classes = doc.get("classes") or {}
    src = doc.get("source") or {}
    lines = [f"# Compute supply ledger, weekly note, {date}", "",
             (
                 f"Method {METHOD_VERSION} (docs/compute-supply-method.md). Link 4 is the **reconstructed** index "
                 f"series under {INDEX_METHOD_CITATION}; the series' method field reads \"{doc.get('method')}\"."
             ),
             ""]
    lines += ["**Read this first.** " + " ".join(POSTURE), ""]
    lines += ["## Link 4, marketplace (reconstructed series): listed on-demand price per peer class, latest week", "",
              (
                  f"Series: reconstructed, from {src.get('title')}, {src.get('licence')}, with attribution: "
                  f"{src.get('attribution')} A reconstructed history is never joined to a live series."
              ), "",
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
              "The one computation possible on link 4 alone, shown so the tolerance is seen in use (it is not a signal by itself).",
              "", f"Flat rule: {FLAT_RULE}", ""]
    for cid, c in sorted(classes.items()):
        win = series_window(c.get("weeks") or {}, 8)
        if len(win) < 2:
            continue
        r1 = flat_reading([(k, v["median_price"]) for k, v in win])
        r2 = flat_reading([(k, float(v["offering_count"])) for k, v in win])
        w1 = "flat" if r1["flat"] else "not flat"
        w2 = "flat" if r2["flat"] else "not flat"
        lines.append(
            f"- {cid}, window {win[0][0]} to {win[-1][0]} ({len(win)} weeks): median listed price {w1} "
            f"({r1['detail']}); offerings {w2} ({r2['detail']})."
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
