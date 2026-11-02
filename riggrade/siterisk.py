"""Site context for a declared compute site (docs/site-risk-method.md, v0 draft).

Not a grade. Reads the public-source snapshots under riggrade/site/ (written by
the site-sources workflow) and the hand-kept primary-read tables beside them,
and, when given preflight reports from rigs at the site, the measured site
signals in those reports. Every line names its source and date. No score, no
weighting: the ratings printed are the sources' own.

Off in the service until Phase 2; usable now from the command line:

    python -m riggrade.siterisk --state TX --county Travis [--report preflight-report.json ...]
"""

import argparse
import csv
import json
import sys
from pathlib import Path

METHOD_VERSION = "site-context-v0-draft"
SITE_DIR = Path(__file__).resolve().parent / "site"

POSTURE = (
    "Site context, not a grade: nothing here changes a grade, a certificate, a record or a price.",
    "Each line names its public source and the date that source was fetched or read.",
    "The location is declared by the host, at county level, and is never inferred from a network address.",
    "Ratings are the source's own, relative to other US counties; RigGrade adds no score and no weighting.",
)
READ_LAST = "A missing line is a missing read, not a clean bill."

HAZARDS = {
    "HWAV": "Heat wave", "RFLD": "Riverine flooding", "CFLD": "Coastal flooding",
    "HRCN": "Hurricane", "WFIR": "Wildfire", "TRND": "Tornado", "SWND": "Strong wind",
    "ISTM": "Ice storm", "WNTW": "Winter weather", "CWAV": "Cold wave", "DRGT": "Drought",
    "ERQK": "Earthquake", "LTNG": "Lightning", "HAIL": "Hail", "LNDS": "Landslide",
}
RATING_WORDS = {
    "VH": "Very High", "RH": "Relatively High", "RM": "Relatively Moderate", "RL": "Relatively Low",
    "VL": "Very Low", "NR": "No Rating", "NA": "Not Applicable", "ID": "Insufficient Data",
}
WATCH = ("VH", "RH")

# What each section bears on. Fixed text, the same for every site.
BEARS_ON = {
    "physical": "Outage, cooling load and physical damage at the site; insurability.",
    "energy": "Running cost and its exposure to fuel prices; the source mix behind the grid.",
    "local": "Permits, moratoria, zoning, large-load tariffs and incentives that can stop or reprice a site.",
    "country": "Sanctions and export rules that decide whether the hardware may sit or be paid for there.",
    "measured": "What rigs at the site actually showed: cooling headroom and power events under load.",
}
NOT_COVERED = (
    "Water stress for cooling (a public dataset is named for v1; drought above is the nearest line).",
    "Grid reliability at the utility level (outage duration and frequency).",
    "Fibre route diversity and carrier count.",
    "Operator financial health and concentration.",
    "Insurance terms, physical security and spare-parts turnaround.",
    "Any site outside the United States: the physical and energy sources here are US-only.",
)
SAMPLE_MIN_CARDS = 10  # docs/gpu-record-method.md section 4a


class SiteError(ValueError):
    pass


def _rows(name, site_dir):
    path = Path(site_dir) / name
    if not path.exists():
        return None
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _sources(site_dir):
    path = Path(site_dir) / "SOURCES.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _norm(county):
    c = " ".join(county.lower().replace(".", "").split())
    for suffix in (" city and borough", " county", " parish", " borough", " census area", " municipality"):
        if c.endswith(suffix):
            return c.removesuffix(suffix)
    return c


def resolve(site, nri):
    """The NRI county row for a declared site: {"state": "TX", "county": "Travis"}
    or {"fips": "48453"}. Raises SiteError on no match or an ambiguous name."""
    if (site.get("country") or "US").upper() != "US":
        return None
    fips = str(site.get("fips") or "").strip()
    if fips:
        hit = [r for r in nri if r["fips"] == fips.zfill(5)]
    else:
        state, county = (site.get("state") or "").upper(), site.get("county") or ""
        if not state or not county:
            raise SiteError("declare a site as state and county, or a five-digit county FIPS")
        hit = [r for r in nri if r["state"] == state and _norm(r["county"]) == _norm(county)]
    if not hit:
        raise SiteError(f"no US county matches {site}")
    if len(hit) > 1:
        raise SiteError(f"{len(hit)} counties match {site}; declare the FIPS: "
                        + ", ".join(f"{r['fips']} {r['county']}" for r in hit))
    return hit[0]


def physical(row, src):
    if row is None:
        return {"status": "not covered", "lines": []}
    when = (src or {}).get("fetched_at", "date unknown")
    lines = [{"item": HAZARDS[h], "rating": RATING_WORDS[row[h]], "code": row[h]} for h in HAZARDS if h in row]
    return {"status": "read", "source": (src or {}).get("name", "FEMA National Risk Index"),
            "fetched_at": when, "url": (src or {}).get("url"),
            "overall_risk": RATING_WORDS[row["risk"]],
            "social_vulnerability": RATING_WORDS[row["sovi"]],
            "community_resilience": RATING_WORDS[row["resl"]],
            "watch": [ln["item"] for ln in lines if ln["code"] in WATCH],
            "lines": lines}


def energy(state, prices, mix, src):
    out = {"status": "not covered", "lines": []}
    if not state or prices is None:
        return out
    price = next((r for r in prices if r["state"] == state), None)
    us = next((r for r in prices if r["state"] == "US"), None)
    if price is None:
        return out
    ind = float(price["industrial_cents_kwh"])
    line = {"item": "Industrial retail price", "value": f"{ind:.2f} cents/kWh", "year": int(price["year"])}
    if us:
        line["us_average"] = f"{float(us['industrial_cents_kwh']):.2f} cents/kWh"
    if price.get("industrial_base_cents_kwh"):
        base = float(price["industrial_base_cents_kwh"])
        line["change"] = f"{(ind - base) / base * 100:+.0f}% since {price['base_year']}"
    lines = [line, {"item": "Commercial retail price", "value": f"{float(price['commercial_cents_kwh']):.2f} cents/kWh",
                    "year": int(price["year"])}]
    shares = sorted(((r["source"], float(r["share"])) for r in (mix or []) if r["state"] == state),
                    key=lambda t: -t[1])
    if shares:
        lines.append({"item": "Generation mix, state, all producers",
                      "value": ", ".join(f"{s} {v * 100:.0f}%" for s, v in shares if v >= 0.02),
                      "year": int(next(r for r in mix if r["state"] == state)["year"])})
    return {"status": "read", "source": "EIA Detailed State Data",
            "fetched_at": (src.get("price") or {}).get("fetched_at", "date unknown"),
            "scope": "state level: a site's own tariff or supply contract can differ", "lines": lines}


def hand_kept(rows, match):
    if rows is None:
        return {"status": "table missing", "lines": []}
    hit = [r for r in rows if match(r)]
    return {"status": "read" if hit else "nothing read yet", "lines": hit}


def measured(reports):
    """Site signals from preflight reports of rigs at the site."""
    if not reports:
        return {"status": "no reports given", "lines": []}
    cards, temps, thermal, brake, hw, shared = 0, [], 0, 0, 0, 0
    warn = 85
    for rep in reports:
        lim = (rep.get("limits") or {}).get("temperature_c")
        if isinstance(lim, (list, tuple)) and lim:
            warn = lim[0]
        per = (rep.get("measures") or {}).get("per_gpu") or {}
        cards += len(rep.get("cards") or per or [None])
        seen = set()
        for g in per.values():
            if isinstance(g.get("peak_temperature_c"), (int, float)):
                temps.append(g["peak_temperature_c"])
            seen |= set(g.get("reasons") or [])
        thermal += bool(seen & {"sw_thermal_slowdown", "hw_thermal_slowdown"})
        brake += "hw_power_brake_slowdown" in seen
        hw += "hw_slowdown" in seen
        shared += not rep.get("exclusive_box_assumed", True)
    lines = [{"item": "Reports and cards", "value": f"{len(reports)} reports, {cards} cards"}]
    if temps:
        lines.append({"item": "Hottest card under load", "value": f"{max(temps):.0f} C",
                      "headroom": f"{warn - max(temps):.0f} C to RigGrade's {warn} C warning line"})
    lines += [
        {"item": "Reports with a thermal slowdown under load", "value": thermal},
        {"item": "Reports with an external power brake under load", "value": brake},
        {"item": "Reports with a hardware slowdown under load", "value": hw},
    ]
    if shared:
        lines.append({"item": "Reports from shared or sliced boxes", "value": shared})
    note = ("These lines describe these runs. "
            + (f"Fewer than {SAMPLE_MIN_CARDS} cards is below the minimum sample, so they are never a site claim."
               if cards < SAMPLE_MIN_CARDS else
               "A site claim still needs the nonce-drawn sample of docs/gpu-record-method.md section 4a."))
    return {"status": "read", "lines": lines, "note": note}


def context(site, reports=(), site_dir=SITE_DIR):
    nri = _rows("nri_counties.csv", site_dir)
    src = _sources(site_dir)
    country = (site.get("country") or "US").upper()
    row = resolve(site, nri) if nri is not None and country == "US" else None
    state = row["state"] if row else (site.get("state") or "").upper() or None
    fips = row["fips"] if row else None
    doc = {
        "method": METHOD_VERSION,
        "read_this_first": list(POSTURE),
        "site": {"country": country, "state": state, "county": row["county"] if row else site.get("county"),
                 "fips": fips, "declared": True},
        "physical": physical(row, src.get("nri")) if nri is not None else {"status": "not yet fetched", "lines": []},
        "energy": energy(state, _rows("eia_state_prices.csv", site_dir), _rows("eia_state_mix.csv", site_dir), src),
        "local": hand_kept(_rows("local_actions.csv", site_dir),
                           lambda r: r["state"] == state and (not r["county_fips"] or r["county_fips"] == fips)),
        "country": hand_kept(_rows("countries.csv", site_dir), lambda r: r["country"].upper() == country),
        "measured": measured(list(reports)),
        "bears_on": BEARS_ON,
        "not_covered": list(NOT_COVERED),
        "read_this_last": READ_LAST,
    }
    return doc


def render(doc):
    s = doc["site"]
    out = [f"# Site context: {s.get('county') or '?'}, {s.get('state') or '?'}, {s['country']}"
           + (f" (FIPS {s['fips']})" if s.get("fips") else ""), ""]
    out += [f"> {p}" for p in doc["read_this_first"]] + [""]
    p = doc["physical"]
    out.append("## Physical hazards")
    out.append(f"_{BEARS_ON['physical']}_")
    if p["status"] == "read":
        out.append(f"Source: {p['source']}, fetched {p['fetched_at']}.")
        out.append(f"Overall risk {p['overall_risk']}; social vulnerability {p['social_vulnerability']}; "
                   f"community resilience {p['community_resilience']}.")
        out.append("Rated Relatively High or Very High: " + (", ".join(p["watch"]) or "none") + ".")
        out += [f"- {ln['item']}: {ln['rating']}" for ln in p["lines"]]
    else:
        out.append(f"Status: {p['status']}.")
    e = doc["energy"]
    out += ["", "## Energy", f"_{BEARS_ON['energy']}_"]
    if e["status"] == "read":
        out.append(f"Source: {e['source']}, fetched {e['fetched_at']}; {e['scope']}.")
        for ln in e["lines"]:
            extra = "; ".join(f"{k.replace('_', ' ')} {v}" for k, v in ln.items() if k in ("us_average", "change"))
            out.append(f"- {ln['item']} ({ln['year']}): {ln['value']}" + (f"; {extra}" if extra else ""))
    else:
        out.append(f"Status: {e['status']}.")
    for key, title in (("local", "Local and political"), ("country", "Country")):
        sec = doc[key]
        out += ["", f"## {title}", f"_{BEARS_ON[key]}_", f"Status: {sec['status']}."]
        for r in sec["lines"]:
            out.append(f"- {r.get('kind')}: {r.get('summary')} ({r.get('status')}; read {r.get('read_on')} "
                       f"by {r.get('read_by')} at {r.get('source_url')})")
    m = doc["measured"]
    out += ["", "## Measured at the site", f"_{BEARS_ON['measured']}_", f"Status: {m['status']}."]
    for ln in m["lines"]:
        out.append(f"- {ln['item']}: {ln['value']}" + (f" ({ln['headroom']})" if ln.get("headroom") else ""))
    if m.get("note"):
        out.append(m["note"])
    out += ["", "## Not covered in v0"] + [f"- {x}" for x in doc["not_covered"]]
    out += ["", f"> {doc['read_this_last']}", ""]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Site context for a declared US county (not a grade).")
    ap.add_argument("--state", help="two-letter state code, e.g. TX")
    ap.add_argument("--county", help="county name, e.g. Travis")
    ap.add_argument("--fips", help="five-digit county FIPS, instead of state and county")
    ap.add_argument("--country", default="US")
    ap.add_argument("--report", action="append", default=[], help="a preflight-report.json from a rig at the site")
    ap.add_argument("--json", action="store_true", help="print the JSON document instead of the page")
    ap.add_argument("--site-dir", default=str(SITE_DIR))
    args = ap.parse_args(argv)
    reports = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.report]
    try:
        doc = context({"country": args.country, "state": args.state, "county": args.county, "fips": args.fips},
                      reports, args.site_dir)
    except SiteError as exc:
        ap.error(str(exc))
    sys.stdout.write(json.dumps(doc, indent=2, sort_keys=True) + "\n" if args.json else render(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
