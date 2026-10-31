"""Take one daily snapshot and append it to a hash-chained JSONL file.

    python3 -m pricewatch.record --out data/index/listed-prices.jsonl [--source vast]

Only aggregates are written. A source that fails is recorded as failed for
that day, never filled in. The file is append-only; an edit or removal
breaks the chain (riggrade.salelog.read_chain checks it).
"""

import argparse
import datetime as dt
import sys

from riggrade import salelog
from . import METHOD_VERSION, aggregate, sources


def snapshot(source_objs, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    per_summary, per_pairs, failed = {}, {}, {}
    for src in source_objs:
        try:
            summary, pairs = aggregate.by_class(src.offers())
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            failed[src.name] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
            continue
        per_summary[src.name], per_pairs[src.name] = summary, pairs
    return {"date": now.strftime("%Y-%m-%d"),
            "taken_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "method": METHOD_VERSION,
            "sources": per_summary,
            "failed_sources": failed,
            "combined": aggregate.combined(per_pairs, per_summary)}


def record(path, source_objs, now=None):
    snap = snapshot(source_objs, now)
    existing = salelog.read_chain(path)  # raises if the chain is broken
    if any(r.get("date") == snap["date"] for r in existing):
        raise SystemExit("a snapshot for %s is already recorded" % snap["date"])
    salelog.append(path, snap)
    return snap


def main(argv=None):
    ap = argparse.ArgumentParser(description="RigGrade index: daily listed-price snapshot")
    ap.add_argument("--out", required=True)
    ap.add_argument("--source", action="append", choices=sorted(sources.SOURCES))
    args = ap.parse_args(argv)
    snap = record(args.out, [sources.SOURCES[s]() for s in (args.source or sorted(sources.SOURCES))])
    ok = {s: sum(c["status"] == "ok" for c in v["classes"].values())
          for s, v in snap["sources"].items()}
    print("recorded %s: classes ok per source %s; failed %s"
          % (snap["date"], ok, snap["failed_sources"] or "none"))
    return 1 if snap["failed_sources"] and not snap["sources"] else 0


if __name__ == "__main__":
    sys.exit(main())
