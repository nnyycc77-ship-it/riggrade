# Site context method, v0 (accepted with named conditions, written in)

Status: accepted with named conditions (coordinator, Message 288, 2 November 2026); conditions A to F are
written in below and marked. Built for our own decisions now. Offered to anyone only once the owner has said
so in his own chat; no route, price or offering is authorised by this text. [F]
Code: `riggrade/siterisk.py` (reader and page), `pricewatch/site_sources.py` (fetcher),
`.github/workflows/site-sources.yml` (monthly), snapshots in `riggrade/site/`.

## 1. What it is, and what it is not

The grade says what a rig delivered when we measured it. A lender or a buyer of compute also carries the
site's risks: power, weather, local rules, the country. This method puts those beside the record as
**site context**, a page of lines, each with its source and date.

It is not a grade and not a score. Five sentences open every page and the last line closes it:

1. Site context, not a grade: nothing here changes a grade, a certificate, a record or a price.
2. Each line names its public source and the date that source was fetched or read.
3. The location is declared by the host, at county or state level, and is never inferred from a network address.
4. Ratings are the source's own, relative to other US counties; RigGrade adds no score and no weighting.
5. Information only: not advice, and not a lending, credit, insurance or investment judgement. [A]

Last line: a missing line is a missing read, not a clean bill.

## 2. The sections

| Section | What it bears on | Source in v0 | Level |
|---|---|---|---|
| Physical hazards | outage, cooling load, damage, insurability | FEMA National Risk Index, county table: 15 hazard ratings, overall risk, social vulnerability, community resilience | county |
| Energy | running cost, fuel-price exposure, the mix behind the grid | EIA: industrial and commercial average retail price for the latest year, its change since 2020, the US average; net generation share by source, all producers | state |
| Grid reliability (v1, accepted with conditions, Message 293) | how long and how often the utilities EIA names for the county cut power | EIA-861: SAIDI (minutes a year per customer) and SAIFI (interruptions a year per customer), IEEE standard, for every utility EIA names for the county; the figure without major event days leads for both, and the all-events figure prints beside it in full for both [G3]; the US median, with its basis printed [G2] | county, by utility |
| Local and political | permits, moratoria, zoning, large-load tariffs, incentives | hand-kept table, one row per primary read | state or county |
| Country | sanctions and export rules for the hardware and the payment; information only, never a replacement for the payer screening and logging on every paid call [A] | hand-kept table, one row per primary read | country |
| Measured at the site | what rigs there showed under load | preflight reports from rigs at the declared site | rigs |

The physical section lists the hazards the source itself rates Relatively High or Very High. That list is a
filter on the source's own ratings, not a judgement of ours. Beside it the page prints how many of the rated
hazards are listed, and that hazards not listed were rated lower by the source and are not absent. [C]

The energy section is state level. A site's own tariff or supply contract can differ, and the page says so.

## 3. Sources

All four fetched sources are US federal government works in the public domain. The monthly workflow runs on
GitHub's runner, writes small extracts and a `SOURCES.json` naming each file's URL, fetch time, byte count and
SHA-256. The fetcher stops with a red run on any shape it does not recognise: a missing column, an unknown
rating word, fewer than 3,000 counties, fewer than 50 states.

| Key | Dataset | URL |
|---|---|---|
| nri | FEMA National Risk Index, county table | hazards.fema.gov NRI_Table_Counties.zip |
| price | EIA Electric Power Annual table 2.10, average price by state and sector, latest two years | eia.gov/electricity/annual/xls/epa_02_10.xlsx |
| price base | EIA average retail price by state and sector, 1990 to 2020; the base year for the change line | eia.gov/electricity/data/state/avgprice_annual.xlsx |
| mix | EIA net generation by state and energy source | eia.gov/electricity/data/state/annual_generation_state.xls |
| reliability | EIA-861 annual: the reliability and service territory workbooks, newest data year available | eia.gov/electricity/data/eia861/zip/f861YYYY.zip |

Grid reliability is read under the IEEE standard columns, never the "other standard" columns some utilities
also report. A utility is listed for a county when EIA's service territory table names that county for it.
The scope line reads: the utilities named are the ones EIA names for this county; the site's own utility may
be one of them, a listing is not proof that the site buys from any named utility, and the site's own feed can
differ. A retail supplier, a private feed or a gap in the territory table can sit outside the list. [G1]
The count line on the page reads "EIA names N utilities for this county; M report reliability figures."
Utilities EIA names for the county that report no figures are counted, not hidden. Up to eight utilities are
named, from public EIA filings; any more are counted. (Message 293, point 1.)

The US median is arithmetic on the same table: the median of every utility-state row that reports SAIDI
without major event days, each row counted once (a utility reporting in two states counts twice), not
weighted by customer count. The page prints that basis and the number of rows beside the figure. [G2] A state
median may be added later as an extra line; it never replaces the US figure.

Each utility's line leads with the figure without major event days, for SAIDI and for SAIFI, and prints the
all-events SAIDI and SAIFI beside it in full, so the impact of major events stays on the line. [G3]

The fetcher takes the newest EIA-861 year published, trying this year and the three before it; EIA answers a
year it has not published with a web page, which counts as not published. The workbook's three header rows
(standard, event basis, measure) are read so that exactly one IEEE column is taken per measure for "All
Events" and for "Without Major Event Days"; the "Loss of Supply Removed" columns are never read. The first
complete fetch (2 November 2026) read data year 2025: 757 utility-state rows with figures and 11,803
utility-county links. Many utilities that serve a county report no figures; the page counts them.

FEMA and EIA spell county names differently, so the join folds accents, case, dots, apostrophes, hyphens and
spaces ("Doña Ana" and "Dona Ana", "Prince George's" and "Prince Georges", "De Witt" and "DeWitt") and drops
the county-type suffix. An independent city that shares a name with a county (Baltimore, St. Louis, and
Virginia's cities, county codes 510 and up) is told apart by its FIPS code and joins EIA's "<name> City"
row, never the county's. Where a county still does not join, the status says why instead of reporting zero
utilities: "state not in the EIA-861 territory file" (Puerto Rico and the territories); "not joined" with
the reason where county lines changed after the list EIA uses (Connecticut's 2022 planning regions; seven
Alaska areas); or "no utility listed for this county" where EIA names none (Galax, Lynchburg and Roanoke
City, Virginia, on the 2025 file). Before this fix 148 of 3,232 counties failed the join; after it, every
remaining failure is one of those three kinds, and a test holds the snapshot to that.

FEMA's download address for the county table now forwards to its Resilience Analysis and Planning Tool page
(observed 2 November 2026). The fetcher tries the download first, then the same county table served as a
hosted ArcGIS layer, and uses that layer only when the organisation owning it names itself as FEMA. The
owner's name, the layer name and its last-edit time are recorded in `SOURCES.json` and printed in the source
line. The first complete fetch (2 November 2026) read layer NRI_Counties_Prod, owner "FEMA AGOL", last edited
14 October 2026: 3,232 counties. If neither route answers, the physical section reads "not yet fetched" and
the run is red.

The county table has renamed one hazard between versions: riverine flooding (RFLD) became inland flooding
(IFLD) in the hosted layer. The fetcher takes whichever is present and the page prints the name of the one
read.

EIA workbooks are read sheet by sheet, and the sheet reaching the latest year is used; its name is recorded.

Each fetched line names the dataset's release or vintage where the source gives one, not the fetch date alone:
the hosted layer's last-edit date for the county table, and the data year on every EIA line. [E]

The two hand-kept tables follow the board's primary-read rule: a row is added only after a reader who carries
the decision reads the source at its own address, and the row records who read it and when. A copy fetched by
someone else is not a primary read. Both tables ship empty. An empty table prints "nothing read yet". Any row
that enters a page used in a decision or offered to anyone is read by the coordinator. Rows are added as a
declared site needs one; there is no standing schedule. (Message 288, point 4.)

## 4. Location

The host declares the site as a US state and county, or a five-digit county FIPS code. RigGrade never infers
a location from an IP address or a network path. County is the finest level held: no street address, no
coordinates. A host may declare a state only; the county lines then read "county not declared" and are never
filled from a guess. (Message 288, point 1.) An ambiguous county name is refused with the candidate FIPS
codes listed. A site outside the United States reads "not covered" in the physical and energy sections rather
than borrowing another country's data.

## 5. Measured signals

Read only from preflight reports of rigs whose host declared that site for this use; never from the
anonymised pool, whose summary drops the slowdown reasons in any case. A page published for a declared site
rests on that declaration as the host's consent to county-level publication. [B] Since this release the
preflight keeps the slowdown reasons per GPU in its report.

Counts on the measured lines carry the GPU record method's protection (section 5, C4): while fewer than 20
cards stand behind the page every count rounds to the nearest five and anything under five reads "fewer than
5". [B] Exact counts print only on an internal page marked "not for publication", for our own decisions.

- **Hottest card under load**, and its headroom to RigGrade's own temperature warning line (85 C today). This
  is headroom to our line, not to the card's own limit.
- **Reports with a thermal slowdown** (software or hardware thermal reasons): a cooling signal.
- **Reports with a power brake asserted from outside the card** (hw_power_brake_slowdown). Site or chassis
  power is a possible source only; the cause is not separable on this line. [D]
- **Reports with a hardware slowdown**: cause not separable from the card.

Fewer than 10 cards is below the minimum sample of the GPU record method, section 4a, and the page says these
lines describe those runs and are never a site claim. At 10 cards or more it says a site claim still needs
the nonce-drawn sample of section 4a.

## 6. Not covered in v0

Printed on every page so its absence is visible:

- Water stress for cooling. Second for v1: WRI Aqueduct 4.0. No value is imported until its licence terms are
  read in full at the source and recorded, and the CC BY 4.0 attribution is carried on the page.
- Fibre route diversity and carrier count.
- Operator financial health and concentration.
- Insurance terms, physical security and spare-parts turnaround.
- Any site outside the United States.

## 7. Delivery

Now: a command-line page for our own decisions, and a JSON document.

    python -m riggrade.siterisk --state TX --county Travis --report preflight-report.json

No route serves it. Its intended home, later, is beside a site sample report, behind a product switch in the
same pattern as the ledger feed and the host certificate: off until the owner names it and sets a price, or a
zero price with an end date, in his own chat. [F]

## 8. Review record

Coordinator verdict, Message 288: accept with named conditions A to F, all written in above. The five review
points were answered as follows. County stays the finest level, with a state-only declaration allowed. The
high-rating list stays, with the note of condition C. The measured wording changed under condition D. The
hand-kept tables are read by the coordinator for any row used in a decision or offered. The v1 order is grid
reliability first, water stress second.

Coordinator verdict, Message 293: grid reliability (v1) accepted with named conditions A to C (scope line,
median basis, lead order), all written in above and marked [G1], [G2] and [G3] so they are not confused with
the conditions of Message 288. Confirmation read, Message 295: accepted with no condition outstanding; the
count line is quoted in section 3 per its non-blocking note.
