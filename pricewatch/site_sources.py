"""Fetch the public datasets behind the site context (docs/site-risk-method.md).

Runs on the GitHub runner by the site-sources workflow; the service and the
CLI only read the snapshots it commits under riggrade/site/. Every source is a
US federal government work (public domain). Each fetch stops loudly on a
shape it does not recognise: a red run is better than a snapshot of garbage.

Sources:
  nri   FEMA National Risk Index, county table (hazard risk ratings)
  price EIA Detailed State Data, average retail price by state and sector
  mix   EIA Detailed State Data, net generation by state and energy source
"""

import argparse
import csv
import hashlib
import io
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "riggrade" / "site"
UA = "riggrade-site-sources (+https://github.com/nnyycc77-ship-it/riggrade)"
LICENCE = "US federal government work, public domain (17 U.S.C. 105)"

URLS = {
    "nri": "https://hazards.fema.gov/nri/Content/StaticDocuments/DataDownload//NRI_Table_Counties/NRI_Table_Counties.zip",
    "price": "https://www.eia.gov/electricity/data/state/avgprice_annual.xlsx",
    "mix": "https://www.eia.gov/electricity/data/state/annual_generation_state.xls",
}

# NRI hazard prefixes kept, in the order the context prints them. The rating
# column for each is <PREFIX>_RISKR.
HAZARDS = {
    "HWAV": "Heat wave", "RFLD": "Riverine flooding", "CFLD": "Coastal flooding",
    "HRCN": "Hurricane", "WFIR": "Wildfire", "TRND": "Tornado", "SWND": "Strong wind",
    "ISTM": "Ice storm", "WNTW": "Winter weather", "CWAV": "Cold wave", "DRGT": "Drought",
    "ERQK": "Earthquake", "LTNG": "Lightning", "HAIL": "Hail", "LNDS": "Landslide",
}
RATINGS = {
    "Very High": "VH", "Relatively High": "RH", "Relatively Moderate": "RM",
    "Relatively Low": "RL", "Very Low": "VL", "No Rating": "NR",
    "Not Applicable": "NA", "Insufficient Data": "ID",
}
NRI_MIN_ROWS = 3000  # the county table covers every US county and equivalent


def fail(msg):
    raise SystemExit(f"site-sources: {msg}")


def require(cond, msg):
    if not cond:
        fail(msg)


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Keeps the redirect chain so a failed fetch says where it was sent."""

    def __init__(self):
        self.chain = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append(f"{code} -> {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, timeout=120):
    hops = _Redirects()
    opener = urllib.request.build_opener(hops)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as err:
        fail(f"{url}: HTTP {err.code} at {err.url}; redirects: {hops.chain or 'none'}; "
             f"server: {err.headers.get('Server')}; content-type: {err.headers.get('Content-Type')}")
    except urllib.error.URLError as err:
        fail(f"{url}: {err.reason}; redirects: {hops.chain or 'none'}")
    return None


def rating(value, where):
    value = (value or "").strip()
    if value == "":
        return "NR"
    require(value in RATINGS, f"unknown NRI rating {value!r} in {where}")
    return RATINGS[value]


def parse_nri(zip_bytes):
    """Rows of (fips, state, county, overall, sovi, resl, *hazard codes) and the
    name of the CSV read inside the zip."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")
                 and "dictionary" not in n.lower() and "metadata" not in n.lower()]
        require(len(names) == 1, f"expected one county CSV in the NRI zip, found {names}")
        text = zf.read(names[0]).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    cols = set(reader.fieldnames or [])
    need = {"STCOFIPS", "STATEABBRV", "COUNTY", "RISK_RATNG", "SOVI_RATNG", "RESL_RATNG"}
    need |= {f"{h}_RISKR" for h in HAZARDS}
    missing = sorted(need - cols)
    require(not missing, f"NRI columns missing: {missing}; rating columns present: "
                         f"{sorted(c for c in cols if c.endswith('_RISKR'))}")
    rows = []
    for r in reader:
        fips = r["STCOFIPS"].strip().zfill(5)
        require(fips.isdigit() and len(fips) == 5, f"bad county FIPS {r['STCOFIPS']!r}")
        rows.append([fips, r["STATEABBRV"].strip(), r["COUNTY"].strip(),
                     rating(r["RISK_RATNG"], fips), rating(r["SOVI_RATNG"], fips),
                     rating(r["RESL_RATNG"], fips)]
                    + [rating(r[f"{h}_RISKR"], fips) for h in HAZARDS])
    require(len(rows) >= NRI_MIN_ROWS, f"NRI county table has {len(rows)} rows, expected {NRI_MIN_ROWS}+")
    require(len({r[0] for r in rows}) == len(rows), "duplicate county FIPS in the NRI table")
    return rows, names[0]


def header_index(rows, *words):
    """Index of the first row whose cells contain every word (case-insensitive)."""
    for i, row in enumerate(rows[:20]):
        cells = [str(c).strip().lower() for c in row]
        if all(any(w.lower() == c for c in cells) for w in words):
            return i
    fail(f"no header row with {words} in the first 20 rows")
    return None


def col(header, name):
    cells = [str(c).strip().lower() for c in header]
    require(name.lower() in cells, f"column {name!r} not in header {header}")
    return cells.index(name.lower())


def parse_prices(rows, span=5):
    """Per state: the latest year's industrial and commercial average retail
    price (cents/kWh), and the industrial price `span` years earlier."""
    h = header_index(rows, "Year", "State", "Industrial")
    head = rows[h]
    iy, ist, isec = col(head, "Year"), col(head, "State"), col(head, "Industry Sector Category")
    iind, icom = col(head, "Industrial"), col(head, "Commercial")
    by = {}
    for r in rows[h + 1:]:
        if len(r) <= max(iy, ist, isec, iind, icom) or "total electric industry" not in str(r[isec]).lower():
            continue
        try:
            year = int(float(r[iy]))
            ind, com = float(r[iind]), float(r[icom])
        except (TypeError, ValueError):
            continue
        by[(str(r[ist]).strip(), year)] = (ind, com)
    require(by, "no 'Total Electric Industry' price rows")
    latest = max(y for _, y in by)
    out = []
    for (state, year), (ind, com) in sorted(by.items()):
        if year != latest:
            continue
        base = by.get((state, latest - span))
        out.append([state, latest, round(ind, 2), round(com, 2), latest - span,
                    round(base[0], 2) if base else ""])
    require(len(out) >= 50, f"only {len(out)} states priced for {latest}")
    return out


def parse_mix(rows):
    """Per state and energy source: the share of the latest year's total net
    generation, all producers ('Total Electric Power Industry')."""
    h = header_index(rows, "YEAR", "STATE", "ENERGY SOURCE")
    head = rows[h]
    iy, ist, itp, isrc = col(head, "YEAR"), col(head, "STATE"), col(head, "TYPE OF PRODUCER"), col(head, "ENERGY SOURCE")
    igen = next((i for i, c in enumerate(head) if str(c).strip().upper().startswith("GENERATION")), None)
    require(igen is not None, f"no GENERATION column in {head}")
    gen = {}
    for r in rows[h + 1:]:
        if len(r) <= max(iy, ist, itp, isrc, igen) or str(r[itp]).strip() != "Total Electric Power Industry":
            continue
        try:
            year, mwh = int(float(r[iy])), float(r[igen])
        except (TypeError, ValueError):
            continue
        gen[(str(r[ist]).strip(), year, str(r[isrc]).strip())] = mwh
    require(gen, "no 'Total Electric Power Industry' generation rows")
    latest = max(y for _, y, _ in gen)
    out = []
    for state in sorted({s for s, y, _ in gen if y == latest}):
        total = gen.get((state, latest, "Total"))
        if not total or total <= 0:
            continue
        for (s, y, src), mwh in sorted(gen.items()):
            if s == state and y == latest and src != "Total" and mwh > 0:
                out.append([state, latest, src, round(mwh / total, 4)])
    require(len({r[0] for r in out}) >= 50, f"generation mix for only {len({r[0] for r in out})} states")
    return out


def excel_rows(data, name):
    import pandas as pd  # CI only: the service and the CLI never import this module
    frame = pd.read_excel(io.BytesIO(data), header=None, engine="xlrd" if name.endswith(".xls") else "openpyxl")
    return frame.where(frame.notna(), None).values.tolist()


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    old_path = out / "SOURCES.json"
    sources = json.loads(old_path.read_text(encoding="utf-8")) if old_path.exists() else {}
    failures = []

    def nri():
        data = fetch(URLS["nri"])
        rows, inner = parse_nri(data)
        write_csv(out / "nri_counties.csv", ["fips", "state", "county", "risk", "sovi", "resl", *HAZARDS], rows)
        return data, {"name": "FEMA National Risk Index, county table", "file": inner, "rows": len(rows)}

    def price():
        data = fetch(URLS["price"])
        rows = parse_prices(excel_rows(data, URLS["price"]))
        write_csv(out / "eia_state_prices.csv",
                  ["state", "year", "industrial_cents_kwh", "commercial_cents_kwh", "base_year",
                   "industrial_base_cents_kwh"], rows)
        return data, {"name": "EIA average retail price of electricity by state and sector",
                      "year": rows[0][1], "rows": len(rows)}

    def mix():
        data = fetch(URLS["mix"])
        rows = parse_mix(excel_rows(data, URLS["mix"]))
        write_csv(out / "eia_state_mix.csv", ["state", "year", "source", "share"], rows)
        return data, {"name": "EIA net generation by state and energy source, all producers",
                      "year": rows[0][1], "rows": len(rows)}

    # Each source stands alone: one refusing does not stop the others, and a
    # failed source keeps its last good snapshot and SOURCES entry. Any
    # failure still ends the run red.
    for key, job in (("nri", nri), ("price", price), ("mix", mix)):
        try:
            blob, meta = job()
        except SystemExit as exc:
            failures.append(str(exc))
            sys.stdout.write(f"{key}: FAILED: {exc}\n")
            continue
        meta.update({"url": URLS[key], "fetched_at": stamp, "licence": LICENCE,
                     "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob)})
        sources[key] = meta
        sys.stdout.write(f"{key}: {meta['rows']} rows, {meta['bytes']} bytes, sha256 {meta['sha256'][:16]}\n")
    if len(failures) < 3:
        old_path.write_text(json.dumps(sources, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if failures:
        fail(f"{len(failures)} of 3 sources failed; the others were written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
