# Site context method, v0 (draft for coordinator review)

Status: draft, 2 November 2026. Built for our own decisions now; offered to anyone only from Phase 2 of the
exit plan, and only once the coordinator has accepted this text and the owner has said so in his own chat.
Code: `riggrade/siterisk.py` (reader and page), `pricewatch/site_sources.py` (fetcher),
`.github/workflows/site-sources.yml` (monthly), snapshots in `riggrade/site/`.

## 1. What it is, and what it is not

The grade says what a rig delivered when we measured it. A lender or a buyer of compute also carries the
site's risks: power, weather, local rules, the country. This method puts those beside the record as
**site context**, a page of lines, each with its source and date.

It is not a grade and not a score. Four sentences open every page and the last line closes it:

1. Site context, not a grade: nothing here changes a grade, a certificate, a record or a price.
2. Each line names its public source and the date that source was fetched or read.
3. The location is declared by the host, at county level, and is never inferred from a network address.
4. Ratings are the source's own, relative to other US counties; RigGrade adds no score and no weighting.

Last line: a missing line is a missing read, not a clean bill.

## 2. The five sections

| Section | What it bears on | Source in v0 | Level |
|---|---|---|---|
| Physical hazards | outage, cooling load, damage, insurability | FEMA National Risk Index, county table: 15 hazard ratings, overall risk, social vulnerability, community resilience | county |
| Energy | running cost, fuel-price exposure, the mix behind the grid | EIA Detailed State Data: industrial and commercial average retail price, change over five years, US average; net generation share by source, all producers | state |
| Local and political | permits, moratoria, zoning, large-load tariffs, incentives | hand-kept table, one row per primary read | state or county |
| Country | sanctions and export rules for the hardware and the payment | hand-kept table, one row per primary read | country |
| Measured at the site | what rigs there showed under load | preflight reports from rigs at the declared site | rigs |

The physical section lists the hazards the source itself rates Relatively High or Very High. That list is a
filter on the source's own ratings, not a judgement of ours.

The energy section is state level. A site's own tariff or supply contract can differ, and the page says so.

## 3. Sources

All three fetched sources are US federal government works in the public domain. The monthly workflow runs on
GitHub's runner, writes small extracts and a `SOURCES.json` naming each file's URL, fetch time, byte count and
SHA-256. The fetcher stops with a red run on any shape it does not recognise: a missing column, an unknown
rating word, fewer than 3,000 counties, fewer than 50 states.

| Key | Dataset | URL |
|---|---|---|
| nri | FEMA National Risk Index, county table | hazards.fema.gov NRI_Table_Counties.zip |
| price | EIA average retail price by state and sector | eia.gov/electricity/data/state/avgprice_annual.xlsx |
| mix | EIA net generation by state and energy source | eia.gov/electricity/data/state/annual_generation_state.xls |

FEMA's download address for the county table now forwards to its Resilience Analysis and Planning Tool page
(observed 2 November 2026). The fetcher tries the download first, then the same county table served as a
hosted ArcGIS layer, and uses that layer only when the organisation owning it names itself as FEMA. The
owner's name, the layer name and its last-edit time are recorded in `SOURCES.json`. If neither route
answers, the physical section reads "not yet fetched" and the run is red.

EIA workbooks are read sheet by sheet, and the sheet reaching the latest year is used; its name is recorded.

The two hand-kept tables follow the board's primary-read rule: a row is added only after a reader who carries
the decision reads the source at its own address, and the row records who read it and when. A copy fetched by
someone else is not a primary read. Both tables ship empty. An empty table prints "nothing read yet".

## 4. Location

The host declares the site as a US state and county, or a five-digit county FIPS code. RigGrade never infers
a location from an IP address or a network path. County is the finest level held: no street address, no
coordinates. An ambiguous county name is refused with the candidate FIPS codes listed. A site outside the
United States reads "not covered" in the physical and energy sections rather than borrowing another
country's data.

## 5. Measured signals

Read from preflight reports of rigs at the declared site. Since this release the preflight keeps the slowdown
reasons per GPU in its report (the anonymised pool summary still drops them).

- **Hottest card under load**, and its headroom to RigGrade's own temperature warning line (85 C today). This
  is headroom to our line, not to the card's own limit.
- **Reports with a thermal slowdown** (software or hardware thermal reasons): a cooling signal.
- **Reports with an external power brake** (hw_power_brake_slowdown): asserted from outside the card, so a
  site or chassis power signal.
- **Reports with a hardware slowdown**: cause not separable from the card.

Fewer than 10 cards is below the minimum sample of the GPU record method, section 4a, and the page says these
lines describe those runs and are never a site claim. At 10 cards or more it says a site claim still needs
the nonce-drawn sample of section 4a.

## 6. Not covered in v0

Printed on every page so its absence is visible:

- Water stress for cooling. Candidate for v1: WRI Aqueduct 4.0, CC BY 4.0.
- Grid reliability at the utility level. Candidate for v1: EIA-861 reliability tables (outage duration and
  frequency), public domain.
- Fibre route diversity and carrier count.
- Operator financial health and concentration.
- Insurance terms, physical security and spare-parts turnaround.
- Any site outside the United States.

## 7. Delivery

Now: a command-line page for our own decisions, and a JSON document.

    python -m riggrade.siterisk --state TX --county Travis --report preflight-report.json

No route serves it. From Phase 2, the intended home is beside a site sample report, behind a product switch
in the same pattern as the ledger feed and the host certificate: off until the owner names it and sets a
price, or a zero price with an end date.

## 8. Points for the coordinator's review

1. Whether county is the right finest level, or whether a host may declare only a state.
2. Whether the "rated Relatively High or Very High" list stays, since it is the one place the page selects.
3. The wording of the measured lines, in particular "external power brake" as a site signal.
4. Whether the hand-kept local and country tables should be read by the coordinator alone, as the primary-read
   rule implies, and on what schedule.
5. The v1 order: water stress first, or grid reliability first.
