"""Reconstructed listed-price series from third-party records
(docs/index-reconstructed-method.md). v0: GetDeploying's CC BY 4.0 weekly
dataset files, mapped to the v0 peer classes through the published table
below. Each source stays separate; weekly figures stay weekly; history is
append-only (a changed past value becomes a correction beside the original).

    python3 -m pricewatch.reconstructed --out riggrade/index/reconstructed-getdeploying.json
    python3 -m pricewatch.reconstructed --payload weekly.json --out ... --now 2026-11-01T00:00:00Z
"""

import argparse
import datetime as dt
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

METHOD_VERSION = "reconstructed-v0-draft"

SOURCE = {
    "name": "getdeploying",
    "title": "GetDeploying GPU price history (weekly)",
    "url": "https://getdeploying.com/dataset/gpu-prices/weekly.json",
    "home": "https://getdeploying.com/gpus",
    "licence": "CC BY 4.0",
    "licence_url": "https://creativecommons.org/licenses/by/4.0/",
    "attribution": "GetDeploying, https://getdeploying.com/gpus, CC BY 4.0. Figures changed: "
                   "mapped to RigGrade peer classes and re-published per class; on-demand "
                   "billing rows only; the source's own medians, not re-aggregated.",
    "basis": "median_price and provider_median_price as the source states them per week "
             "(USD per GPU per hour, on-demand). Weekly stays weekly.",
}

BILLING = "ON_DEMAND"

# The published mapping table (method section 3). A source name that could
# fit two v0 classes maps to neither: the source's "nvidia-h100" carries no
# form (SXM or PCIe) and "nvidia-a100" no memory size, so both are dropped
# and counted. Only unambiguous names map.
MAPPING = {
    "nvidia-h200": "h200-sxm-141",
    "nvidia-l40s": "l40s-48",
    "nvidia-rtx-5090": "rtx5090-32",
    "nvidia-rtx-4090": "rtx4090-24",
    "nvidia-rtx-3090": "rtx3090-24",
}
AMBIGUOUS = {"nvidia-h100": "form not stated: could be h100-sxm-80 or h100-pcie-80",
             "nvidia-a100": "memory not stated: could be a100-sxm-80, a100-pcie-80 or a100-pcie-40"}

# Amendment A1 (method text accepted in review on 2026-11-01; 14 days' notice):
# a chip-family series for the two ambiguous names, in its own namespace,
# never a peer class, alongside and never spliced. Published only from the
# date the notice ends. Section 3 is unchanged: the names stay dropped and
# counted in the peer-class series whether or not A1 publishes.
A1_NOTICE_ENDS = "2026-11-15"
A1_METHOD_VERSION = "reconstructed-v0-draft-A1"
CHIP_FAMILY = {"nvidia-h100": "chip:h100", "nvidia-a100": "chip:a100"}
CHIP_FAMILY_LABEL = "chip family (form and memory not stated by the source)"
CHIP_FAMILY_MIXES = {
    "chip:h100": "The source does not state the form (SXM or PCIe); this figure mixes h100-sxm-80 "
                 "and h100-pcie-80 in whatever shares the source's offerings had that week.",
    "chip:a100": "The source does not state the form or the memory size; this figure mixes "
                 "a100-sxm-80, a100-pcie-80 and a100-pcie-40 in whatever shares the source's "
                 "offerings had that week.",
}
CHIP_FAMILY_RULE = ("Never a peer class, never spliced into one, never used for a grade, a bar, "
                    "a reference, a consistency rule or a performance-adjusted price.")


def a1_published(now):
    """True once the notice period has ended, judged on the import date."""
    return str(now)[:10] >= A1_NOTICE_ENDS

FIELDS = ("median_price", "provider_median_price", "min_price", "max_price",
          "provider_count", "offering_count")


class SchemaError(ValueError):
    pass


def fetch(url=SOURCE["url"], opener=urllib.request.urlopen, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "riggrade-pricewatch"})
    with opener(req, timeout=timeout) as resp:
        return resp.read()


def parse(raw):
    """The dataset file -> (meta, rows); loud on any shape surprise."""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SchemaError("getdeploying: not JSON: %s" % exc)
    if not isinstance(doc, dict) or not isinstance(doc.get("data"), list):
        raise SchemaError("getdeploying: expected {meta, data[]}")
    meta = doc.get("meta") or {}
    if "CC-BY" not in str(meta.get("license", "")).upper().replace(" ", "-"):
        raise SchemaError("getdeploying: licence field is not CC BY: %r" % meta.get("license"))
    for row in doc["data"]:
        for key in ("gpu_slug", "date", "billing_type") + FIELDS:
            if key not in row:
                raise SchemaError("getdeploying: row missing %r" % key)
    return meta, doc["data"]


def _week_value(row):
    return {k: row[k] for k in FIELDS}


def _merge_append_only(series, previous, corrections, now, kind):
    """Carry every recorded week forward; a changed value becomes a
    correction beside the original; a missing week stays recorded."""
    for key, old in (previous or {}).items():
        new = series.setdefault(key, {"source_name": old.get("source_name"), "weeks": {}})
        for week, old_value in (old.get("weeks") or {}).items():
            new_value = new["weeks"].get(week)
            if new_value is None:
                new["weeks"][week] = old_value          # a gap in the new file stays as recorded
            elif new_value != old_value:
                corrections.append({kind: key, "week": week, "recorded": old_value,
                                    "source_now_states": new_value, "noticed_at": now})
                new["weeks"][week] = old_value          # history is never rewritten
    for c in series.values():
        c["weeks"] = dict(sorted(c["weeks"].items()))
        c["latest_week"] = max(c["weeks"]) if c["weeks"] else None


def build(raw, existing=None, now=None):
    """The series document. `existing` is the previous document, so past
    weeks are never rewritten: a changed value is appended as a correction
    and the original stays."""
    now = now or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta, rows = parse(raw)
    classes = {cid: {"source_name": slug, "weeks": {}} for slug, cid in MAPPING.items()}
    publish_a1 = a1_published(now)
    families = ({fid: {"source_name": slug, "label": CHIP_FAMILY_LABEL, "mixes": CHIP_FAMILY_MIXES[fid],
                       "rule": CHIP_FAMILY_RULE, "weeks": {}} for slug, fid in CHIP_FAMILY.items()}
                if publish_a1 else {})
    dropped = {}
    for row in rows:
        if row["billing_type"] != BILLING:
            continue
        slug = row["gpu_slug"]
        cid = MAPPING.get(slug)
        if cid is None:
            dropped[slug] = dropped.get(slug, 0) + 1   # section 3: ambiguous names stay dropped and counted
            fid = CHIP_FAMILY.get(slug)
            if fid and publish_a1:
                families[fid]["weeks"][row["date"]] = _week_value(row)
            continue
        classes[cid]["weeks"][row["date"]] = _week_value(row)
    corrections = list((existing or {}).get("corrections") or [])
    _merge_append_only(classes, (existing or {}).get("classes"), corrections, now, "class")
    if publish_a1:
        _merge_append_only(families, (existing or {}).get("chip_families"), corrections, now, "chip_family")
    imports = list((existing or {}).get("imports") or [])
    imports.append({"retrieved_at": now, "url": SOURCE["url"], "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "source_latest_week": meta.get("latest_week"),
                    "source_last_snapshot": meta.get("last_snapshot")})
    return {"series": "reconstructed", "method": A1_METHOD_VERSION if publish_a1 else METHOD_VERSION,
            "a1_notice_ends": A1_NOTICE_ENDS, "chip_families": families,
            "method_url": "https://github.com/nnyycc77-ship-it/riggrade/blob/main/docs/index-reconstructed-method.md",
            "note": "Information, not an offer, a rate or advice. Listed prices as a third party "
                    "recorded them, mapped to peer classes; never joined to the live series.",
            "source": dict(SOURCE, disclaimer=meta.get("disclaimer")),
            "billing": BILLING, "mapping": MAPPING, "ambiguous_dropped": AMBIGUOUS,
            "dropped_rows_by_source_name": dict(sorted(dropped.items())),
            "classes": classes, "corrections": corrections, "imports": imports}


def to_csv(doc):
    header = ("class,source_name,week,median_price,provider_median_price,min_price,max_price,"
              "provider_count,offering_count,licence,attribution")
    lines = [header]
    rows = sorted(doc["classes"].items()) + sorted((doc.get("chip_families") or {}).items())
    for cid, c in rows:
        for week, v in c["weeks"].items():
            lines.append(",".join(str(x) for x in (
                cid, c["source_name"], week, v["median_price"], v["provider_median_price"],
                v["min_price"], v["max_price"], v["provider_count"], v["offering_count"],
                doc["source"]["licence"], '"' + doc["source"]["home"] + '"')))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--csv", default="")
    ap.add_argument("--payload", default="", help="a downloaded weekly.json instead of fetching")
    ap.add_argument("--now", default="")
    a = ap.parse_args(argv)
    raw = Path(a.payload).read_bytes() if a.payload else fetch()
    out = Path(a.out)
    existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    doc = build(raw, existing, a.now or None)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if a.csv:
        Path(a.csv).write_text(to_csv(doc), encoding="utf-8")
    mapped = sum(len(c["weeks"]) for c in doc["classes"].values())
    sys.stdout.write("reconstructed: %d class-weeks across %d classes; %d corrections; latest %s\n" % (
        mapped, len(doc["classes"]), len(doc["corrections"]),
        max((c["latest_week"] or "") for c in doc["classes"].values())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
