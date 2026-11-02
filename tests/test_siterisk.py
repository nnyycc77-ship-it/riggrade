"""Site context (docs/site-risk-method.md): the fetcher's parsers on synthetic
source shapes, and the context built from small snapshot tables. No network."""

import contextlib
import csv
import io
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from pricewatch import site_sources as ss
from riggrade import siterisk as sr


def nri_csv(n=3005, bad_rating=None, drop=None):
    cols = ["OID_", "STCOFIPS", "STATEABBRV", "COUNTY", "COUNTYTYPE", "RISK_RATNG", "SOVI_RATNG", "RESL_RATNG"]
    cols += [f"{h}_RISKR" for h in ss.HAZARDS] + ["EXTRA_COL"]
    if drop:
        cols.remove(drop)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for i in range(n):
        row = {c: "Relatively Low" for c in cols}
        row.update({"OID_": i, "STCOFIPS": str(1001 + i), "STATEABBRV": "AL", "COUNTY": f"C{i}",
                    "COUNTYTYPE": "County", "EXTRA_COL": "x", "HWAV_RISKR": "Very High",
                    "RFLD_RISKR": "", "SOVI_RATNG": "Relatively Moderate"})
        if bad_rating and i == 7:
            row["HAIL_RISKR"] = bad_rating
        w.writerow(row)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as zf:
        zf.writestr("NRI_Table_Counties.csv", "﻿" + buf.getvalue())
        zf.writestr("NRIDataDictionary.csv", "a,b\n")
    return raw.getvalue()


class FetcherParseTests(unittest.TestCase):
    def test_nri_rows_are_coded_and_padded(self):
        rows, header, inner = ss.parse_nri(nri_csv())
        self.assertEqual(inner, "NRI_Table_Counties.csv")
        self.assertIn("RFLD", header)
        first = rows[0]
        self.assertEqual(first[:3], ["01001", "AL", "C0"])
        hazards = dict(zip(ss.HAZARDS, first[6:], strict=True))
        self.assertEqual(hazards["HWAV"], "VH")
        self.assertEqual(hazards["RFLD"], "NR")  # blank reads as no rating
        self.assertEqual(first[4], "RM")

    def test_a_hazard_with_neither_name_is_refused_with_the_columns_listed(self):
        with self.assertRaises(SystemExit) as cm:
            ss.resolve_hazards({f"{h}_RISKR" for h in ss.HAZARDS if h != "RFLD"} | {"XFLD_RISKR"})
        self.assertIn("['RFLD']", str(cm.exception))
        self.assertIn("XFLD_RISKR", str(cm.exception))

    def test_nri_stops_on_unknown_rating_missing_column_or_short_table(self):
        with self.assertRaises(SystemExit):
            ss.parse_nri(nri_csv(bad_rating="Kinda High"))
        with self.assertRaises(SystemExit):
            ss.parse_nri(nri_csv(drop="WFIR_RISKR"))
        with self.assertRaises(SystemExit):
            ss.parse_nri(nri_csv(n=40))

    def test_prices_latest_year_and_base(self):
        rows = [["Average Price (Cents/kilowatthour) by State by Provider"], [],
                ["Year", "State", "Industry Sector Category", "Residential", "Commercial", "Industrial",
                 "Transportation", "Other", "Total"]]
        for st in [f"S{i}" for i in range(50)] + ["US"]:
            for year, ind in ((2019, 6.0), (2024, 7.5)):
                rows.append([year, st, "Total Electric Industry", 13, 11.0, ind, 10, None, 11])
                rows.append([year, st, "Full-Service Providers", 13, 11.0, 99.0, 10, None, 11])
        out = ss.parse_prices(rows)
        us = next(r for r in out if r[0] == "US")
        self.assertEqual(us, ["US", 2024, 7.5, 11.0, 2019, 6.0])

    def test_mix_shares_from_totals(self):
        rows = [["YEAR", "STATE", "TYPE OF PRODUCER", "ENERGY SOURCE", "GENERATION (Megawatthours)"]]
        for st in [f"S{i}" for i in range(51)]:
            rows += [[2024, st, "Total Electric Power Industry", "Total", 100.0],
                     [2024, st, "Total Electric Power Industry", "Natural Gas", 60.0],
                     [2024, st, "Total Electric Power Industry", "Wind", 40.0],
                     [2024, st, "Electric Generators, Electric Utilities", "Coal", 999.0],
                     [2023, st, "Total Electric Power Industry", "Total", 1.0]]
        out = ss.parse_mix(rows)
        self.assertIn(["S0", 2024, "Natural Gas", 0.6], out)
        self.assertNotIn("Coal", {r[2] for r in out})

    def test_shape_surprises_stop_loudly(self):
        with self.assertRaises(SystemExit):
            ss.parse_prices([["nothing", "here"]])
        with self.assertRaises(SystemExit):
            ss.parse_mix([["YEAR", "STATE", "TYPE OF PRODUCER", "ENERGY SOURCE", "GENERATION"]])


class LatestPriceTests(unittest.TestCase):
    def table(self):
        # The shape EIA served on 2 Nov 2026: two title rows, the sector row, then
        # "Year 2025" labels under each sector.
        rows = [["Table 2.10. Average Price of Electricity to Ultimate Customers by End-Use Sector,"] + [None] * 10,
                ["by State, 2025 and 2024 (Cents per Kilowatthour)"] + [None] * 10,
                [float("nan"), "Residential", None, "Commercial", None, "Industrial", None,
                 "Transportation", None, "All Sectors", None],
                ["Census Division\nand State"] + ["Year 2024", "Year 2023"] * 5,
                ["New England", 28, 27, 21, 20, 17, 16, 9, 9, 23, 22]]
        for name in list(ss.STATES)[:-2]:
            transport = "--" if name == "Maine" else 10  # EIA prints -- where a sector has no sales
            rows.append([name, 16.0, 15.0, 12.5, 12.0, 8.25, 8.0, transport, transport, 13, 12])
        rows.append(["U.S. Total", 16.48, 16.0, 12.7, 12.4, 8.1, 8.0, 11, 11, 13, 12])
        return rows

    def test_latest_year_columns_by_sector(self):
        out = ss.parse_price_latest(self.table())
        tx = next(r for r in out if r[0] == "TX")
        self.assertEqual(tx, ["TX", 2024, 8.25, 12.5])
        self.assertIn(["US", 2024, 8.1, 12.7], out)
        self.assertNotIn("New England", {r[0] for r in out})

    def test_a_state_listed_twice_stops(self):
        rows = self.table()
        rows.append(["Texas", 1, 1, 1, 1, 1, 1, 1, 1, 1, 1])
        with self.assertRaises(SystemExit):
            ss.parse_price_latest(rows)

    def test_a_table_without_the_sector_header_stops(self):
        with self.assertRaises(SystemExit):
            ss.parse_price_latest([["Texas", 1, 2, 3]])


class FetcherRunTests(unittest.TestCase):
    def test_one_failed_source_keeps_the_others_and_ends_red(self):
        import tempfile as tf

        def fake_fetch(url, timeout=120):
            if url in (ss.URLS["price"], ss.URLS["mix"], ss.URLS["price_latest"]):
                return b"sheet"
            ss.fail(f"{url}: HTTP 403 at https://moved.example/x; redirects: ['302 -> https://moved.example/x']")

        price_rows = [["Year", "State", "Industry Sector Category", "Commercial", "Industrial"]]
        price_rows += [[2024, f"S{i}", "Total Electric Industry", 11.0, 7.0] for i in range(50)]
        mix_rows = [["YEAR", "STATE", "TYPE OF PRODUCER", "ENERGY SOURCE", "GENERATION (Megawatthours)"]]
        for i in range(50):
            mix_rows += [[2024, f"S{i}", "Total Electric Power Industry", "Total", 10.0],
                         [2024, f"S{i}", "Total Electric Power Industry", "Wind", 10.0]]
        saved = ss.fetch, ss.excel_sheets
        ss.fetch = fake_fetch
        latest_rows = LatestPriceTests().table()
        ss.excel_sheets = lambda data, name: [("Sheet1", latest_rows if "epa_02_10" in name
                                               else price_rows if name.endswith(".xlsx") else mix_rows)]
        try:
            with tf.TemporaryDirectory() as d:
                Path(d, "SOURCES.json").write_text(json.dumps({"nri": {"fetched_at": "2026-10-01T00:00:00Z"}}))
                out = io.StringIO()
                with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
                    ss.main(["--out", d])
                kept = json.loads(Path(d, "SOURCES.json").read_text())
                self.assertEqual(kept["nri"]["fetched_at"], "2026-10-01T00:00:00Z")
                self.assertEqual(kept["price"]["rows"], 52)
                self.assertEqual(kept["price"]["base"]["year"], 2024)
                self.assertTrue(Path(d, "eia_state_mix.csv").exists())
                self.assertIn("1 of 3 sources failed", str(cm.exception))
                self.assertIn("redirects: ['302 -> https://moved.example/x']", out.getvalue())
        finally:
            ss.fetch, ss.excel_sheets = saved


class HostedLayerTests(unittest.TestCase):
    def answers(self, owner="Federal Emergency Management Agency (FEMA)", n=3005, page=1000):
        fields = ["OBJECTID", "STCOFIPS", "STATEABBRV", "COUNTY", "RISK_RATNG", "SOVI_RATNG", "RESL_RATNG"]
        fields += [f"{'IFLD' if h == 'RFLD' else h}_RISKR" for h in ss.HAZARDS]
        recs = []
        for i in range(n):
            r = {f: "Relatively Low" for f in fields}
            r.update({"OBJECTID": i, "STCOFIPS": f"{1001 + i:05d}", "STATEABBRV": "AL", "COUNTY": f"C{i}",
                      "WFIR_RISKR": "Very High", "HAIL_RISKR": "Data Unavailable" if i == 3 else "Very Low"})
            recs.append(r)
        calls = []

        def get(url):
            calls.append(url)
            if url == ss.NRI_PORTAL:
                return {"name": owner}
            if url.endswith("/0?f=json"):
                return {"name": "NRI_Counties", "maxRecordCount": page, "fields": [{"name": f} for f in fields],
                        "editingInfo": {"lastEditDate": 1760000000000}}
            off = int(re.search(r"resultOffset=(\d+)", url).group(1))
            chunk = recs[off:off + page]
            return {"features": [{"attributes": r} for r in chunk], "exceededTransferLimit": off + page < n}
        return get, calls

    def test_pages_the_layer_and_records_the_owner(self):
        get, calls = self.answers()
        rows, header, meta = ss.fetch_nri_service(get)
        self.assertIn("IFLD", header)
        self.assertNotIn("RFLD", header)
        self.assertEqual(len(rows), 3005)
        self.assertEqual(rows[0][:3], ["01001", "AL", "C0"])
        self.assertEqual(dict(zip(ss.HAZARDS, rows[0][6:], strict=True))["WFIR"], "VH")
        self.assertEqual(dict(zip(ss.HAZARDS, rows[3][6:], strict=True))["HAIL"], "DU")
        self.assertEqual(meta["owner"], "Federal Emergency Management Agency (FEMA)")
        self.assertEqual(meta["via"], "hosted layer")
        self.assertEqual(sum("resultOffset" in c for c in calls), 4)
        self.assertTrue(all("returnGeometry=false" in c for c in calls if "query" in c))

    def test_a_layer_not_owned_by_fema_is_refused(self):
        get, _ = self.answers(owner="Someone's copy")
        with self.assertRaises(SystemExit) as cm:
            ss.fetch_nri_service(get)
        self.assertIn("not FEMA", str(cm.exception))


class SheetTests(unittest.TestCase):
    def test_year_cells_and_latest_sheet(self):
        self.assertEqual([ss.year_of(v) for v in (2023, 2023.0, "2024P", " 2022 ", None, "Year", "Year 2025", 12345)],
                         [2023, 2023, 2024, 2022, None, None, 2025, None])

        def sheet(year):
            rows = [["Year", "State", "Industry Sector Category", "Commercial", "Industrial"]]
            return rows + [[year, f"S{i}", "Total Electric Industry", 11.0, 7.0] for i in range(50)]
        rows, name, seen = ss.best_sheet([("old", sheet(2020)), ("junk", [["x"]]), ("new", sheet("2024P"))],
                                         ss.parse_prices)
        self.assertEqual(name, "new")
        self.assertEqual(rows[0][1], 2024)
        self.assertEqual(seen["old"], 2020)
        self.assertIn("no header row", seen["junk"])


class FindNriTests(unittest.TestCase):
    def test_catalogue_search_prints_titles_owners_and_urls(self):
        answers = {
            ss.CATALOGUES["data.gov"]: {"result": {"results": [
                {"title": "National Risk Index Counties", "organization": {"title": "FEMA"},
                 "metadata_modified": "2026-01-01", "resources": [{"format": "ZIP", "url": "https://x.gov/nri.zip"}]}]}},
            ss.CATALOGUES["arcgis-hub"]: {"features": [
                {"properties": {"title": "NRI Counties", "owner": "fema_owner", "orgTitle": "FEMA", "url": "https://y/z"}}]},
        }
        saved = ss.fetch
        ss.fetch = lambda url, timeout=120: json.dumps(answers[url]).encode()
        try:
            out = io.StringIO()
            ss.find_nri(out)
        finally:
            ss.fetch = saved
        text = out.getvalue()
        self.assertIn("National Risk Index Counties | FEMA", text)
        self.assertIn("ZIP: https://x.gov/nri.zip", text)
        self.assertIn("owner fema_owner | org FEMA | https://y/z", text)


def snapshot(d):
    d = Path(d)
    hz = [h for h in sr.HAZARDS if h != "IFLD"]  # a snapshot carries one flooding column, here the older name
    with open(d / "nri_counties.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["fips", "state", "county", "risk", "sovi", "resl", *hz])
        w.writerow(["48453", "TX", "Travis", "RH", "RM", "RH", *["RL"] * len(hz)])
        w.writerow(["48113", "TX", "Dallas", "VH", "RH", "RM", *["VH" if h == "HWAV" else "VL" for h in hz]])
        w.writerow(["29510", "MO", "St. Louis", "RM", "RH", "RL", *["RL"] * len(hz)])
        w.writerow(["29189", "MO", "St. Louis", "RM", "RM", "RM", *["RL"] * len(hz)])
    with open(d / "eia_state_prices.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["state", "year", "industrial_cents_kwh", "commercial_cents_kwh", "base_year", "industrial_base_cents_kwh"])
        w.writerow(["TX", 2024, 6.5, 9.0, 2019, 5.0])
        w.writerow(["US", 2024, 8.1, 12.7, 2019, 6.8])
    with open(d / "eia_state_mix.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["state", "year", "source", "share"])
        w.writerows([["TX", 2024, "Natural Gas", 0.5], ["TX", 2024, "Wind", 0.25], ["TX", 2024, "Other", 0.01]])
    (d / "SOURCES.json").write_text(json.dumps({"nri": {"name": "FEMA National Risk Index, county table",
                                                        "fetched_at": "2026-11-03T04:00:00Z"},
                                                "price": {"fetched_at": "2026-11-03T04:00:00Z"}}))
    with open(d / "local_actions.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["state", "county_fips", "kind", "status", "summary", "effective", "source_url", "read_by", "read_on"])
        w.writerow(["TX", "", "large-load tariff", "adopted", "example state-wide row", "2026-01-01",
                    "https://example.invalid/order", "Coordinator", "2026-11-03"])
        w.writerow(["TX", "48113", "moratorium", "proposed", "example county row", "", "https://example.invalid/m",
                    "Coordinator", "2026-11-03"])
    (d / "countries.csv").write_text("country,kind,status,summary,source_url,read_by,read_on\n")


def report(peak=78, reasons=("sw_power_cap",), cards=2, shared=False):
    return {"limits": {"temperature_c": [85, 90]}, "exclusive_box_assumed": not shared,
            "cards": [{"card_id": f"gc_{i:016x}"} for i in range(cards)],
            "measures": {"per_gpu": {str(i): {"peak_temperature_c": peak, "reasons": list(reasons)}
                                     for i in range(cards)}}}


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        snapshot(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def ctx(self, site, reports=()):
        return sr.context(site, reports, self.tmp.name)

    def test_county_by_name_or_fips_and_no_score(self):
        a = self.ctx({"state": "tx", "county": "Travis County"})
        b = self.ctx({"fips": "48453"})
        self.assertEqual(a["site"]["fips"], "48453")
        self.assertEqual(a["physical"], b["physical"])
        self.assertEqual(a["physical"]["overall_risk"], "Relatively High")
        self.assertEqual(a["physical"]["fetched_at"], "2026-11-03T04:00:00Z")
        def keys(o):
            if isinstance(o, dict):
                return set(o) | set().union(*(keys(v) for v in o.values()))
            if isinstance(o, list):
                return set().union(*(keys(v) for v in o)) if o else set()
            return set()
        self.assertFalse({k for k in keys(a) if "score" in k or "weight" in k or k == "total"})

    def test_hosted_layer_provenance_is_named(self):
        src = json.loads(Path(self.tmp.name, "SOURCES.json").read_text())
        src["nri"].update({"via": "hosted layer", "layer": "NRI_Counties_Prod", "owner": "FEMA AGOL",
                           "layer_last_edit_ms": 1791992778545})
        Path(self.tmp.name, "SOURCES.json").write_text(json.dumps(src))
        p = self.ctx({"fips": "48453"})["physical"]
        self.assertIn("hosted layer NRI_Counties_Prod owned by FEMA AGOL, last edited 2026-10-14", p["source"])

    def test_watch_lists_only_the_sources_high_ratings(self):
        d = self.ctx({"state": "TX", "county": "Dallas"})
        self.assertEqual(d["physical"]["watch"], ["Heat wave"])

    def test_ambiguous_and_unknown_names_are_refused(self):
        with self.assertRaises(sr.SiteError) as cm:
            self.ctx({"state": "MO", "county": "St Louis"})
        self.assertIn("29510", str(cm.exception))
        with self.assertRaises(sr.SiteError):
            self.ctx({"state": "TX", "county": "Atlantis"})
        with self.assertRaises(sr.SiteError):
            self.ctx({"county": "Travis"})

    def test_state_only_declaration_leaves_county_lines_undeclared(self):
        d = self.ctx({"state": "TX"})
        self.assertEqual(d["physical"]["status"], "county not declared")
        self.assertEqual(d["energy"]["status"], "read")
        self.assertEqual(d["local"]["lines"][0]["kind"], "large-load tariff")  # state-wide rows still apply

    def test_energy_is_state_level_with_change_and_us_average(self):
        e = self.ctx({"fips": "48453"})["energy"]
        self.assertIn("state level", e["scope"])
        ind = e["lines"][0]
        self.assertEqual(ind["value"], "6.50 cents/kWh")
        self.assertEqual(ind["change"], "+30% since 2019")
        self.assertEqual(ind["us_average"], "8.10 cents/kWh")
        self.assertEqual(e["lines"][2]["value"], "Natural Gas 50%, Wind 25%")

    def test_local_rows_match_state_wide_and_own_county_only(self):
        travis = self.ctx({"fips": "48453"})["local"]
        dallas = self.ctx({"fips": "48113"})["local"]
        self.assertEqual([r["kind"] for r in travis["lines"]], ["large-load tariff"])
        self.assertEqual(len(dallas["lines"]), 2)
        mo = self.ctx({"fips": "29510"})["local"]
        self.assertEqual(mo["status"], "nothing read yet")

    def test_outside_the_us_is_not_covered_not_guessed(self):
        d = self.ctx({"country": "DE", "state": "BY", "county": "Munich"})
        self.assertEqual(d["physical"]["status"], "not covered")
        self.assertEqual(d["country"]["status"], "nothing read yet")

    def test_missing_snapshots_read_as_not_yet_fetched(self):
        with tempfile.TemporaryDirectory() as empty:
            d = sr.context({"state": "TX", "county": "Travis"}, (), empty)
        self.assertEqual(d["physical"]["status"], "not yet fetched")
        self.assertEqual(d["energy"]["status"], "not covered")
        self.assertEqual(d["local"]["status"], "table missing")

    def test_measured_signals_and_the_sample_floor(self):
        reps = [report(peak=82, reasons=("hw_power_brake_slowdown",)),
                report(peak=70, reasons=("sw_thermal_slowdown",), shared=True)]
        m = sr.context({"fips": "48453"}, reps, self.tmp.name, exact=True)["measured"]
        lines = {ln["item"]: ln for ln in m["lines"]}
        self.assertEqual(lines["Reports and cards"]["value"], "2 reports, 4 cards")
        self.assertEqual(lines["Hottest card under load"]["headroom"],
                         "3 C to RigGrade's 85 C warning line, not the card's own limit")
        brake = lines["Reports with a power brake asserted from outside the card"]
        self.assertEqual(brake["value"], 1)
        self.assertIn("possible source only", brake["note"])
        self.assertIn("not separable", brake["note"])
        self.assertEqual(lines["Reports with a thermal slowdown under load"]["value"], 1)
        self.assertIn("never a site claim", m["note"])
        self.assertTrue(m["note"].startswith("Internal: exact counts, not for publication."))
        self.assertIn("never the anonymised pool", m["note"])

    def test_measured_counts_carry_the_record_protection_by_default(self):
        m = self.ctx({"fips": "48453"}, [report(cards=2), report(cards=2)])["measured"]
        lines = {ln["item"]: ln for ln in m["lines"]}
        self.assertTrue(m["protected"])
        self.assertEqual(lines["Reports and cards"]["value"], "fewer than 5 reports, fewer than 5 cards")
        big = self.ctx({"fips": "48453"}, [report(cards=6, reasons=("sw_thermal_slowdown",))] * 2)["measured"]
        big_lines = {ln["item"]: ln for ln in big["lines"]}
        self.assertEqual(big_lines["Reports and cards"]["value"], "fewer than 5 reports, 10 cards")
        many = self.ctx({"fips": "48453"}, [report(cards=5), report(cards=5)])["measured"]
        self.assertIn("section 4a", many["note"])

    def test_page_names_sources_and_keeps_posture_first_and_last(self):
        page = sr.render(self.ctx({"fips": "48113"}, [report()]))
        self.assertTrue(page.splitlines()[2].startswith("> Site context, not a grade"))
        self.assertIn("> Information only: not advice, and not a lending, credit, insurance or investment judgement.",
                      page)
        self.assertIn("never replace the payer screening and logging on every paid call", page)
        self.assertIn("1 of the 15 rated hazards are listed. Hazards not listed were rated lower by the source; "
                      "they are not absent.", page)
        self.assertTrue(page.rstrip().endswith("not a clean bill."))
        self.assertIn("Source: FEMA National Risk Index, county table, fetched 2026-11-03T04:00:00Z.", page)
        self.assertIn("Rated Relatively High or Very High: Heat wave. ", page)
        self.assertIn("read 2026-11-03 by Coordinator at https://example.invalid/m", page)

    def test_cli_prints_page_and_json(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sr.main(["--fips", "48453", "--site-dir", self.tmp.name])
        self.assertIn("# Site context: Travis, TX, US (FIPS 48453)", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sr.main(["--state", "TX", "--county", "Travis", "--json", "--site-dir", self.tmp.name])
        self.assertEqual(json.loads(out.getvalue())["site"]["county"], "Travis")


class PreflightReasonsTests(unittest.TestCase):
    def test_reasons_kept_per_gpu_and_out_of_the_share(self):
        from runner import preflight as pf
        obs = {"load": {0: {"clock_ratio": 0.9, "peak_temperature_c": 70, "reasons": ["hw_power_brake_slowdown"]}}}
        m = pf.measures_of(obs)
        self.assertEqual(m["per_gpu"]["0"]["reasons"], ["hw_power_brake_slowdown"])
        shared = pf.anonymise({"measures": m, "findings": [], "gpus": []})
        self.assertNotIn("hw_power_brake", json.dumps(shared))


if __name__ == "__main__":
    unittest.main()
