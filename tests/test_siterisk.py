"""Site context (docs/site-risk-method.md): the fetcher's parsers on synthetic
source shapes, and the context built from small snapshot tables. No network."""

import contextlib
import csv
import io
import json
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
        rows, inner = ss.parse_nri(nri_csv())
        self.assertEqual(inner, "NRI_Table_Counties.csv")
        first = rows[0]
        self.assertEqual(first[:3], ["01001", "AL", "C0"])
        hazards = dict(zip(ss.HAZARDS, first[6:], strict=True))
        self.assertEqual(hazards["HWAV"], "VH")
        self.assertEqual(hazards["RFLD"], "NR")  # blank reads as no rating
        self.assertEqual(first[4], "RM")

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


def snapshot(d):
    d = Path(d)
    hz = list(sr.HAZARDS)
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
            self.ctx({"state": "TX"})

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
        m = self.ctx({"fips": "48453"}, [report(peak=82, reasons=("hw_power_brake_slowdown",)),
                                         report(peak=70, reasons=("sw_thermal_slowdown",), shared=True)])["measured"]
        lines = {ln["item"]: ln for ln in m["lines"]}
        self.assertEqual(lines["Reports and cards"]["value"], "2 reports, 4 cards")
        self.assertEqual(lines["Hottest card under load"]["headroom"], "3 C to RigGrade's 85 C warning line")
        self.assertEqual(lines["Reports with an external power brake under load"]["value"], 1)
        self.assertEqual(lines["Reports with a thermal slowdown under load"]["value"], 1)
        self.assertIn("never a site claim", m["note"])
        many = self.ctx({"fips": "48453"}, [report(cards=5), report(cards=5)])["measured"]
        self.assertIn("section 4a", many["note"])

    def test_page_names_sources_and_keeps_posture_first_and_last(self):
        page = sr.render(self.ctx({"fips": "48113"}, [report()]))
        self.assertTrue(page.splitlines()[2].startswith("> Site context, not a grade"))
        self.assertTrue(page.rstrip().endswith("not a clean bill."))
        self.assertIn("Source: FEMA National Risk Index, county table, fetched 2026-11-03T04:00:00Z.", page)
        self.assertIn("Rated Relatively High or Very High: Heat wave.", page)
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
