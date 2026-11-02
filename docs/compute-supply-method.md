# RigGrade compute supply ledger: method, v0 (draft)

Status: v0, adopted in review on 2 Nov 2026 for the Revision 6 batch, with
the conditions now written into sections 4, 4a and 4b. No value is published
until those conditions are met in the page wording too. The method is written before any number, so the rules
cannot be fitted to the data. It is information, not an offer, a rate, a
forecast or investment advice; RigGrade runs no trading, contracts or
tokens on it.

## 1. What it is

A bottom-up view of the AI compute buildout, read from where the marginal
GPU-hour actually clears. Six links, top to bottom, each a small set of
published series with a named source. The two links at the bottom are
RigGrade's own measurements; the four above are context, taken from public
records so the bottom can be reconciled against them.

The claim the ledger supports is narrow: *what listed capacity does once it
lands*. It does not measure hyperscaler fleets, which never list on
marketplaces, and it does not predict anything. It states facts with dates.

## 2. The six links

| Link | Series (per week unless stated) | Source and licence | Cadence | Who gathers |
|---|---|---|---|---|
| 1 Chips | Data-centre GPU revenue; implied units at disclosed or estimated ASP, with the estimate's source named; HBM and advanced-packaging capacity statements | Issuer filings and earnings calls (NVIDIA, AMD, SK Hynix, Micron, TSMC); public documents only | Quarterly | Research helper first pass; Adviser 1 verifies every number against the filing |
| 2 Buildout | Announced capacity in megawatts; power interconnection queue totals; stated transformer and cooling lead times; data-centre lease rates where a public broker report states them | Filings, utility and ISO queue publications, broker reports whose terms permit citation | Quarterly | Research helper, Adviser 1 verifies |
| 3 Sponsors | Capex per GPU, debt per GPU, assumed utilisation and term, as disclosed; GPU-backed loan terms where public | Filings, prospectuses, loan documents, rating reports | Quarterly, and on each new disclosure | Research helper, Adviser 1 verifies |
| 4 Marketplace | Listed on-demand price per peer class (median, provider median, min, max); offering count; provider count; spread (max over min) | The reconstructed index (`docs/index-reconstructed-method.md`), GetDeploying CC BY 4.0 weekly files | Weekly | Adviser 1, live today |
| 5 Secondary | Used H100 and A100 resale asking prices | Broker and listing pages whose terms permit republication of aggregates; each source checked and recorded before use, as for the index | Weekly | Research helper reads; Adviser 1 records only cleared sources |
| 6 Rig | Delivered-versus-claimed throughput ratio; consistency; degradation over repeat runs; effective cost after disk and egress; acceptance verdict mix | RigGrade grades, preflight results (pooled only if the owner so decides), acceptance certificates | As measured | Adviser 1 |

A link with no cleared source publishes nothing and says so. A series whose
source changes its terms is suspended under a published reason.

## 3. Derived figures (arithmetic on link 4; labelled as such)

For each peer class and week:

- **Implied gross revenue per GPU-year** = median listed price per GPU-hour
  × 8,760 × utilisation band, published at three bands, 40%, 60% and 80%.
  Listed, not paid; gross, before power, hosting, support and the
  marketplace's cut. The bands are assumptions and are printed beside every
  figure.
- **Spread** = max listed price over min listed price in the week. A widening
  spread with a falling median is recorded, not interpreted.

No derived figure uses a value from links 1 to 3 or 5; those are shown
beside it for reconciliation, never combined with it into one number.

## 4. Reconciliation signals

A signal is a sentence with a date, produced only when every series it
names has a published value for the period. Each is defined here before
any is published; none is a forecast. Every signal, when produced, prints
the value of each leg it names, the window it used and the threshold, so a
reader sees the size of a move and not only its direction.

**Tolerance for "flat".** A series is flat over a window when its end
value is within 5% of its start value and no intermediate weekly value
leaves a 10% band around the start value. The tolerance is printed with
the signal.

**Chip-to-class mapping for the secondary link.** Resale asks are quoted
per chip (H100, A100); rental asks are per peer class. The comparison uses
the published mapping table of the reconstructed index: H100 resale
against the chip-family series `chip:h100` (and, where it exists, the
h100-sxm-80 and h100-pcie-80 classes shown separately); A100 resale
against `chip:a100` and the a100 classes likewise. No chip maps to a class
it does not name.

**Sponsor classes and debt service.** A sponsor class is a group of
sponsors with the same disclosed financing shape, named in the method
when first used (for example "GPU-backed term loans, 2025 to 2026
vintage"). Debt service per GPU-year is disclosed principal and interest
due per year divided by the disclosed GPU count the facility covers, taken
only from the primary document (the prospectus, loan agreement summary or
filing), with the sponsor's own utilisation assumption printed beside it.
A sponsor whose documents do not disclose enough for that arithmetic is
not placed in a class and is not named by a signal.

- **Absorption gap.** Link 1 implied units rose over the last four quarters
  while link 4 offering counts for the matching classes rose and the median
  listed price fell. Reads: capacity is reaching the long tail faster than
  long-tail demand.
- **Hyperscale absorption.** Link 1 implied units rose while link 4 offering
  counts and medians were flat. Reads: new capacity is not reaching the
  marginal market; the long tail is not yet where it clears.
- **Secondary lead.** Link 5 resale asking prices for a chip fell by more
  than link 4 listed rental prices for the same chip's classes over the same
  eight weeks. Reads: owners are selling ahead of renting.
- **Yield shortfall.** Link 3 disclosed debt service per GPU-year for a
  sponsor class exceeds link 4 implied gross revenue per GPU-year at the 80%
  band for the matching peer class. Reads: at listed prices and the most
  generous utilisation, the marginal market does not cover the disclosed
  debt service. Printed with the sponsor's own utilisation assumption beside
  it. This is an arithmetic comparison of two published numbers and is never
  worded as a credit judgement, a rating or a recommendation.
- **Delivery gap.** Link 6 delivered-versus-claimed ratio for a class is
  below 0.9 at the median across at least five rigs. Reads: listed capacity
  of that class is underperforming its own claims. Published only at that
  sample size.

Every signal names the series, the period and the threshold it used, and
links to the published values. A signal that fails to fire is not reported
as absence of the condition; it is reported as "no signal", with the series
that were missing, if any.

## 4a. Primary sources only

A context value (links 1, 2, 3 and 5) is recorded only after it has been
checked against the primary document for that value: the filing, the
company's own announcement, the published term sheet, the utility's queue
publication, the source's own dataset file. A secondary summary, a press
report or a research helper's reading is never enough on its own; it can
point at the document, not stand in for it. The record of each value names
the document and the page or field it came from.

## 4b. Posture on every page

Any published note, page or feed that shows a figure or a signal from this
method carries, on that same page, these four statements: "information,
not advice, and not an offer, a rate or a forecast"; "this ledger covers
the long-tail listings market only and says nothing about hyperscaler
fleets or sponsors whose capacity never lists"; "marketplace listings are
the marginal market, not fleet utilisation"; and "listed prices are asks,
not paid prices". A page that cannot carry them does not carry the figure.

## 5. Rules that do not change silently

As the index methods: the method is versioned; a change is posted at least
14 days before it applies, with the reason, and the version in force is
stated next to every value; history is never rewritten, a wrong value gets a
correction appended beside it; gaps stay gaps; no value is adjusted by hand;
a source suspected of manipulation is suspended under a published reason;
every import of a third-party file records URL, time, bytes and SHA-256.

## 6. What it cannot say

- **Long tail only.** Marketplace listings are the marginal market. The
  ledger says nothing about fleet utilisation at hyperscalers or at sponsors
  whose capacity never lists.
- **Listed is not paid.** Prices are asks. A listing that never rents is a
  price with no counterparty.
- **Thin measurement.** Link 6 will be thin for months and is gated at five
  rigs per class.
- **Other people's methods.** Links 1 to 3 and 5 are third-party figures;
  which offers a source counted and how it took its median are its own
  choices.
- **Classes, not fleets.** The peer classes cover a few chips; sponsors own
  mixed fleets.

## 7. Publication

Nothing is published under this method before its review. The first note,
when it comes, carries link 4 (live), the derived figures of section 3, and
one context series from link 5 once a source has cleared terms; links 1 to
3 enter as their first verified quarterly values are recorded. The note is
free. A per-call feed for agents is a separate decision for the owner.

## 6. Delivery (added 2 Nov 2026)

Two forms of the same data, same posture on both:

- **Free weekly note.** `python -m pricewatch.ledger_note --out docs/ledger/<date>.md`
  writes the week's note from link 4 (the reconstructed index). The four 4b statements
  open and close the page; every derived figure carries its band and "listed, not paid";
  every signal that needs an unpublished series reads "no signal" with the series named.
  The first note (`docs/ledger/2026-11-02.md`) published on 2 November 2026 after its
  page posture was shown for review and accepted (board Messages 261b and 268); later
  notes are written by the Tuesday index workflow after each import.
- **Paid feed, per call.** `POST /riggrade/v1/ledger` with `{"weeks": N}` (1 to 52) and an
  optional `classes` list returns `riggrade.ledger.feed`: the link 4 window per class, the
  implied gross revenue bands as labelled arithmetic, every section 4 signal with its state
  and missing series, provenance, and the posture statements first and last. The route
  ships off: it sells only when `RIGGRADE_PRODUCTS` names `ledger` **and** the owner has set
  `RIGGRADE_LEDGER_PRICE_ATOMIC` (USDC atomic units). With no price it answers 503 with the
  reason, appears in no descriptor, catalogue or OpenAPI document, and nothing is charged.
  The free preview in the 402 names the classes, the week count and the latest week, never
  a figure. No signal is ever computed from a missing series on either form.
