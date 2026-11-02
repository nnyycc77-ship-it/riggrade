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
import re
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
    # Electric Power Annual table 2.10: the latest two years of average price by
    # state and sector (the workbook above stops at 2020).
    "price_latest": "https://www.eia.gov/electricity/annual/xls/epa_02_10.xlsx",
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
# A hazard the index has renamed between versions: the first prefix present
# is used, and the snapshot's column carries the prefix actually read.
ALIASES = {"RFLD": ("RFLD", "IFLD")}


def resolve_hazards(cols):
    """{hazard key: prefix present in the source}; stops if one has none."""
    out, missing = {}, []
    for h in HAZARDS:
        hit = next((p for p in ALIASES.get(h, (h,)) if f"{p}_RISKR" in cols), None)
        if hit:
            out[h] = hit
        else:
            missing.append(h)
    require(not missing, f"NRI hazard rating columns missing for {missing}; rating columns present: "
                         f"{sorted(c for c in cols if c.endswith('_RISKR'))}")
    return out


RATINGS = {
    "Very High": "VH", "Relatively High": "RH", "Relatively Moderate": "RM",
    "Relatively Low": "RL", "Very Low": "VL", "No Rating": "NR",
    "Not Applicable": "NA", "Insufficient Data": "ID", "Data Unavailable": "DU",
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


def parse_nri_records(fieldnames, records):
    """Rows of (fips, state, county, overall, sovi, resl, *hazard codes) from the
    county table's own field names, whether read from the CSV or the service,
    and the snapshot header (hazard columns named by the prefix actually read)."""
    cols = set(fieldnames or [])
    base = {"STCOFIPS", "STATEABBRV", "COUNTY", "RISK_RATNG", "SOVI_RATNG", "RESL_RATNG"}
    missing = sorted(base - cols)
    require(not missing, f"NRI columns missing: {missing}")
    prefixes = resolve_hazards(cols)
    rows = []
    for r in records:
        fips = str(r["STCOFIPS"]).strip().zfill(5)
        require(fips.isdigit() and len(fips) == 5, f"bad county FIPS {r['STCOFIPS']!r}")
        rows.append([fips, str(r["STATEABBRV"]).strip(), str(r["COUNTY"]).strip(),
                     rating(r["RISK_RATNG"], fips), rating(r["SOVI_RATNG"], fips),
                     rating(r["RESL_RATNG"], fips)]
                    + [rating(r[f"{prefixes[h]}_RISKR"], fips) for h in HAZARDS])
    require(len(rows) >= NRI_MIN_ROWS, f"NRI county table has {len(rows)} rows, expected {NRI_MIN_ROWS}+")
    require(len({r[0] for r in rows}) == len(rows), "duplicate county FIPS in the NRI table")
    rows.sort()
    return rows, ["fips", "state", "county", "risk", "sovi", "resl", *(prefixes[h] for h in HAZARDS)]


def parse_nri(zip_bytes):
    """The county table from FEMA's download zip, and the CSV name read inside it."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")
                 and "dictionary" not in n.lower() and "metadata" not in n.lower()]
        require(len(names) == 1, f"expected one county CSV in the NRI zip, found {names}")
        text = zf.read(names[0]).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows, header = parse_nri_records(reader.fieldnames, reader)
    return rows, header, names[0]


# Since late 2026 the download above forwards to FEMA's planning-tool page.
# The same county table is served as a hosted layer; it is used only when the
# organisation that owns it names itself as FEMA.
NRI_ORG = "XG15cJAlne2vxtgt"
NRI_SERVICE = f"https://services.arcgis.com/{NRI_ORG}/arcgis/rest/services/National_Risk_Index_Counties/FeatureServer"
NRI_PORTAL = f"https://www.arcgis.com/sharing/rest/portals/{NRI_ORG}?f=json"


def _json(url, timeout=60):
    try:
        doc = json.loads(fetch(url, timeout=timeout))
    except ValueError as exc:
        fail(f"{url}: not JSON ({exc})")
    require(isinstance(doc, dict) and "error" not in doc, f"{url}: {doc.get('error') if isinstance(doc, dict) else doc}")
    return doc


def nri_owner(portal):
    name = str(portal.get("name") or "")
    require("fema" in name.lower() or "federal emergency management" in name.lower(),
            f"the hosted county layer's organisation is {name!r}, not FEMA; not used")
    return name


def fetch_nri_service(get=None, page=None):
    """The county table from the hosted layer, paged; returns rows and provenance."""
    get = get or _json
    owner = nri_owner(get(NRI_PORTAL))
    layer = get(f"{NRI_SERVICE}/0?f=json")
    fields = [f.get("name") for f in layer.get("fields", [])]
    need = ["STCOFIPS", "STATEABBRV", "COUNTY", "RISK_RATNG", "SOVI_RATNG", "RESL_RATNG"]
    missing = [f for f in need if f not in fields]
    require(not missing, f"hosted layer fields missing: {missing}")
    need += [f"{p}_RISKR" for p in resolve_hazards(set(fields)).values()]
    size = page or min(int(layer.get("maxRecordCount") or 1000), 2000)
    records, offset = [], 0
    while True:
        q = (f"{NRI_SERVICE}/0/query?where=1%3D1&outFields={','.join(need)}&returnGeometry=false"
             f"&orderByFields=STCOFIPS&resultOffset={offset}&resultRecordCount={size}&f=json")
        doc = get(q)
        feats = [f.get("attributes") or {} for f in doc.get("features", [])]
        records += feats
        offset += len(feats)
        if not feats or (len(feats) < size and not doc.get("exceededTransferLimit")):
            break
        require(offset < 10000, "hosted layer paging did not end")
    rows, header = parse_nri_records(need, records)
    edited = (layer.get("editingInfo") or {}).get("lastEditDate") or (layer.get("editingInfo") or {}).get("dataLastEditDate")
    meta = {"via": "hosted layer", "service": NRI_SERVICE, "owner": owner, "layer": layer.get("name"),
            "layer_last_edit_ms": edited}
    return rows, header, meta


def year_of(value):
    """A year from a cell such as 2023, 2023.0, '2023P' or 'Year 2023'; None if none."""
    m = re.search(r"(?<!\d)(\d{4})(?!\d)", str(value if value is not None else ""))
    return int(m.group(1)) if m else None


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
        year = year_of(r[iy])
        try:
            ind, com = float(r[iind]), float(r[icom])
        except (TypeError, ValueError):
            continue
        if year is None:
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


STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
    "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "U.S. Total": "US", "US Total": "US",
}


def parse_price_latest(rows):
    """Per state: [state, year, industrial, commercial] for the latest year of a
    table laid out as a sector header row (Residential, Commercial, Industrial,
    each spanning its year columns) over a row of years, then one row per state."""
    h = None
    for i, row in enumerate(rows[:20]):
        cells = [str(c or "").strip().lower() for c in row]
        if any(c.startswith("commercial") for c in cells) and any(c.startswith("industrial") for c in cells):
            h = i
            break
    require(h is not None, "no sector header row (Commercial, Industrial) in the first 20 rows")
    yrow = next((j for j in range(h + 1, min(h + 4, len(rows)))
                 if sum(year_of(c) is not None and 1990 <= (year_of(c) or 0) <= 2100 for c in rows[j]) >= 2), None)
    require(yrow is not None, "no row of years under the sector header")
    cols, sector = {}, None
    width = max(len(rows[h]), len(rows[yrow]))
    for c in range(width):
        cell = str(rows[h][c] if c < len(rows[h]) and rows[h][c] is not None else "").strip().lower()
        if cell:
            sector = cell.split()[0]
        y = year_of(rows[yrow][c]) if c < len(rows[yrow]) else None
        if sector in ("commercial", "industrial") and y:
            cols[(sector, y)] = c
    years = sorted({y for _, y in cols})
    require(years, f"no Commercial or Industrial year columns found in {rows[h]} / {rows[yrow]}")
    latest = years[-1]
    require(("industrial", latest) in cols and ("commercial", latest) in cols,
            f"latest year {latest} lacks a Commercial or Industrial column")
    out = []
    for r in rows[yrow + 1:]:
        name = re.sub(r"[\d*]+$", "", str(r[0] or "")).strip()
        code = STATES.get(name)
        if not code:
            continue
        try:
            ind = float(r[cols[("industrial", latest)]])
            com = float(r[cols[("commercial", latest)]])
        except (TypeError, ValueError, IndexError):
            continue
        out.append([code, latest, round(ind, 2), round(com, 2)])
    require(len({r[0] for r in out}) == len(out), "a state appears twice in the latest-year table")
    require(len(out) >= 50, f"only {len(out)} states priced in the latest-year table")
    return out


# EIA-861 annual files: one zip per data year, newest first. The zip holds the
# reliability table (outage duration and frequency per utility) and the
# service territory table (the counties each utility serves).
EIA861_URL = "https://www.eia.gov/electricity/data/eia861/zip/f861{year}.zip"


def _norm_header(cell):
    return " ".join(str(cell or "").replace("\n", " ").lower().split())


def parse_reliability(rows):
    """Per utility and state: SAIDI and SAIFI under the IEEE standard, with and
    without major event days. Rows: [utility, state, name, year, saidi_all,
    saidi_no_med, saifi_all, saifi_no_med]."""
    h = None
    for i, row in enumerate(rows[:20]):
        cells = [_norm_header(c) for c in row]
        if "utility number" in cells and "state" in cells and any(c.startswith("saidi") for c in cells):
            h = i
            break
    require(h is not None, "no reliability header row (Utility Number, State, SAIDI) in the first 20 rows")
    head = [_norm_header(c) for c in rows[h]]
    group = [_norm_header(c) for c in rows[h - 1]] if h > 0 else []
    start = next((i for i, c in enumerate(group) if "ieee" in c), 0)

    def first(prefix):
        hit = next((i for i, c in enumerate(head) if i >= start and c.startswith(prefix)), None)
        require(hit is not None, f"no column starting {prefix!r} at or after the IEEE group; header {rows[h]}")
        return hit

    iu, ist = head.index("utility number"), head.index("state")
    iname = next((i for i, c in enumerate(head) if c.startswith("utility name")), None)
    iyear = next((i for i, c in enumerate(head) if c.startswith("data year")), None)
    cols = [first("saidi with med"), first("saidi without med"), first("saifi with med"), first("saifi without med")]
    out = []
    for r in rows[h + 1:]:
        if len(r) <= max([iu, ist, *cols]):
            continue
        try:
            util = int(float(r[iu]))
        except (TypeError, ValueError):
            continue
        vals = []
        for c in cols:
            try:
                vals.append(round(float(r[c]), 3))
            except (TypeError, ValueError):
                vals.append("")
        if all(v == "" for v in vals):
            continue
        out.append([util, str(r[ist]).strip(), str(r[iname]).strip() if iname is not None else "",
                    year_of(r[iyear]) if iyear is not None else "", *vals])
    require(len(out) >= 200, f"only {len(out)} utilities report reliability")
    return out


def parse_territory(rows):
    """[state, county, utility] for every county a utility serves."""
    h = None
    for i, row in enumerate(rows[:20]):
        cells = [_norm_header(c) for c in row]
        if "utility number" in cells and "state" in cells and "county" in cells:
            h = i
            break
    require(h is not None, "no territory header row (Utility Number, State, County) in the first 20 rows")
    head = [_norm_header(c) for c in rows[h]]
    iu, ist, ico = head.index("utility number"), head.index("state"), head.index("county")
    out = set()
    for r in rows[h + 1:]:
        if len(r) <= max(iu, ist, ico) or not r[ico]:
            continue
        try:
            util = int(float(r[iu]))
        except (TypeError, ValueError):
            continue
        out.add((str(r[ist]).strip(), str(r[ico]).strip(), util))
    require(len(out) >= 1000, f"only {len(out)} utility-county rows")
    return sorted(out)


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
        year = year_of(r[iy])
        try:
            mwh = float(r[igen])
        except (TypeError, ValueError):
            continue
        if year is None:
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


def excel_sheets(data, name):
    """[(sheet name, rows)] for every sheet in the workbook."""
    import pandas as pd  # CI only: the service and the CLI never import this module
    book = pd.read_excel(io.BytesIO(data), header=None, sheet_name=None,
                         engine="xlrd" if name.endswith(".xls") else "openpyxl")
    return [(sheet, frame.where(frame.notna(), None).values.tolist()) for sheet, frame in book.items()]


def best_sheet(sheets, parse):
    """Parse each sheet that parses; keep the one reaching the latest year.
    Returns (rows, sheet name, {sheet: latest year or the reason it did not parse})."""
    seen, best = {}, None
    for sheet, rows in sheets:
        try:
            out = parse(rows)
        except SystemExit as exc:
            seen[sheet] = str(exc)[:80]
            continue
        seen[sheet] = out[0][1]
        if best is None or out[0][1] > best[0][0][1]:
            best = (out, sheet)
    require(best is not None, f"no sheet parsed: {seen}")
    return best[0], best[1], seen


def best_rows(sheets, parse):
    """The first sheet that parses: (rows, sheet name, {sheet: reason it did not})."""
    seen = {}
    for sheet, rows in sheets:
        try:
            return parse(rows), sheet, seen
        except SystemExit as exc:
            seen[sheet] = str(exc)[:80]
    fail(f"no sheet parsed: {seen}")
    return None


def dump(sheets, name, n=8):
    """Print the first rows of a workbook whose shape was not recognised, for the next fix."""
    for sheet, rows in sheets[:2]:
        sys.stdout.write(f"{name} sheet {sheet!r}, first rows for the next fix:\n")
        for r in rows[:n]:
            sys.stdout.write(f"  {r[:14]}\n")


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


CATALOGUES = {
    "data.gov": "https://catalog.data.gov/api/3/action/package_search?rows=10&q=%22National+Risk+Index%22+counties",
    "arcgis-hub": "https://hub.arcgis.com/api/search/v1/collections/dataset/items?limit=10&q=National%20Risk%20Index%20Counties",
}


def find_nri(out=sys.stdout):
    """Print where public catalogues say the county risk index now lives.
    Read-only, for a person to pick the agency's own address; nothing is
    adopted from here automatically."""
    for name, url in CATALOGUES.items():
        out.write(f"== {name}: {url}\n")
        try:
            doc = json.loads(fetch(url, timeout=30))
        except SystemExit as exc:
            out.write(f"   unavailable: {exc}\n")
            continue
        if name == "data.gov":
            for pkg in (doc.get("result") or {}).get("results", []):
                org = (pkg.get("organization") or {}).get("title")
                out.write(f"- {pkg.get('title')} | {org} | modified {pkg.get('metadata_modified')}\n")
                for res in pkg.get("resources", [])[:6]:
                    out.write(f"    {res.get('format')}: {res.get('url')}\n")
        else:
            for feat in doc.get("features", []):
                p = feat.get("properties") or {}
                out.write(f"- {p.get('title')} | owner {p.get('owner')} | org {p.get('orgTitle') or p.get('source')}"
                          f" | {p.get('url')}\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--find-nri", action="store_true", help="print catalogue entries for the county risk index and stop")
    args = ap.parse_args(argv)
    if args.find_nri:
        return find_nri()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    old_path = out / "SOURCES.json"
    sources = json.loads(old_path.read_text(encoding="utf-8")) if old_path.exists() else {}
    failures = []

    def nri():
        try:
            data = fetch(URLS["nri"])
        except SystemExit as exc:
            sys.stdout.write(f"nri: download refused ({exc}); trying the hosted layer\n")
            rows, header, meta = fetch_nri_service()
            write_csv(out / "nri_counties.csv", header, rows)
            blob = json.dumps(rows).encode()
            meta.update({"name": "FEMA National Risk Index, county table", "rows": len(rows),
                         "note": "sha256 and bytes are of the extracted rows; the layer has no file to hash"})
            return blob, meta, NRI_SERVICE
        rows, header, inner = parse_nri(data)
        write_csv(out / "nri_counties.csv", header, rows)
        return data, {"name": "FEMA National Risk Index, county table", "file": inner, "rows": len(rows),
                      "via": "download"}, URLS["nri"]

    def price():
        base = fetch(URLS["price"])
        hist, _, _ = best_sheet(excel_sheets(base, URLS["price"]), parse_prices)
        data = fetch(URLS["price_latest"])
        sheets = excel_sheets(data, URLS["price_latest"])
        try:
            latest, sheet, seen = best_sheet(sheets, parse_price_latest)
        except SystemExit:
            for name, rows in sheets[:1]:
                sys.stdout.write(f"price_latest sheet {name!r}, first rows for the next fix:\n")
                for r in rows[:8]:
                    sys.stdout.write(f"  {r[:12]}\n")
            raise
        sys.stdout.write(f"price: latest-year sheets {seen}; using {sheet!r}; base from the {hist[0][1]} workbook\n")
        by = {r[0]: r for r in hist}
        rows = [[st, y, ind, com, by[st][1] if st in by else "", by[st][2] if st in by else ""]
                for st, y, ind, com in latest]
        write_csv(out / "eia_state_prices.csv",
                  ["state", "year", "industrial_cents_kwh", "commercial_cents_kwh", "base_year",
                   "industrial_base_cents_kwh"], rows)
        return data, {"name": "EIA Electric Power Annual table 2.10, average price by state and sector",
                      "year": rows[0][1], "rows": len(rows), "sheet": str(sheet),
                      "base": {"url": URLS["price"], "year": hist[0][1],
                               "sha256": hashlib.sha256(base).hexdigest()}}, URLS["price_latest"]

    def mix():
        data = fetch(URLS["mix"])
        rows, sheet, seen = best_sheet(excel_sheets(data, URLS["mix"]), parse_mix)
        sys.stdout.write(f"mix: sheets {seen}; using {sheet!r}\n")
        write_csv(out / "eia_state_mix.csv", ["state", "year", "source", "share"], rows)
        return data, {"name": "EIA net generation by state and energy source, all producers",
                      "year": rows[0][1], "rows": len(rows), "sheet": str(sheet)}, URLS["mix"]

    # Each source stands alone: one refusing does not stop the others, and a
    # failed source keeps its last good snapshot and SOURCES entry. Any
    # failure still ends the run red.
    def reliability():
        this = int(stamp[:4])
        data = year = None
        tried = []
        for y in range(this, this - 4, -1):
            try:
                blob = fetch(EIA861_URL.format(year=y), timeout=300)
            except SystemExit as exc:
                tried.append(f"{y}: {str(exc)[:90]}")
                continue
            if not blob.startswith(b"PK"):
                # EIA answers a year it has not published with a web page, not a 404.
                tried.append(f"{y}: not a zip ({len(blob)} bytes, starts {blob[:15]!r})")
                continue
            data, year = blob, y
            break
        require(data is not None, f"no EIA-861 zip found: {tried}")
        url = EIA861_URL.format(year=year)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()

            def member(prefix):
                hit = [n for n in names if n.rsplit("/", 1)[-1].lower().startswith(prefix) and n.lower().endswith(".xlsx")]
                require(hit, f"no {prefix}* workbook in {url}: {names}")
                return hit[0], zf.read(hit[0])

            rname, rdata = member("reliability")
            tname, tdata = member("service_territory")
        rel_sheets = excel_sheets(rdata, rname)
        try:
            rel, rsheet, _ = best_rows(rel_sheets, parse_reliability)
        except SystemExit:
            dump(rel_sheets, rname)
            raise
        ter_sheets = excel_sheets(tdata, tname)
        territory = []
        for sheet, rows in ter_sheets:
            try:
                territory += parse_territory(rows)
            except SystemExit as exc:
                sys.stdout.write(f"reliability: territory sheet {sheet!r} skipped: {exc}\n")
        if not territory:
            dump(ter_sheets, tname)
            fail(f"no territory sheet parsed in {tname}")
        write_csv(out / "eia861_reliability.csv",
                  ["utility", "state", "name", "year", "saidi_all", "saidi_no_med", "saifi_all", "saifi_no_med"], rel)
        write_csv(out / "eia861_territory.csv", ["state", "county", "utility"], sorted(set(territory)))
        sys.stdout.write(f"reliability: {rname} sheet {rsheet!r}, {len(rel)} utilities; "
                         f"{tname}, {len(set(territory))} utility-county rows\n")
        return data, {"name": "EIA-861 reliability and service territory", "year": year, "rows": len(rel),
                      "files": [rname, tname], "territory_rows": len(set(territory))}, url

    for key, job in (("nri", nri), ("price", price), ("mix", mix), ("reliability", reliability)):
        try:
            blob, meta, url = job()
        except (SystemExit, Exception) as exc:  # noqa: BLE001 - one source failing must not stop the others
            failures.append(str(exc))
            sys.stdout.write(f"{key}: FAILED: {exc}\n")
            continue
        meta.update({"url": url, "fetched_at": stamp, "licence": LICENCE,
                     "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob)})
        sources[key] = meta
        sys.stdout.write(f"{key}: {meta['rows']} rows, {meta['bytes']} bytes, sha256 {meta['sha256'][:16]}\n")
    if len(failures) < 4:
        old_path.write_text(json.dumps(sources, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if failures:
        fail(f"{len(failures)} of 4 sources failed; the others were written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
