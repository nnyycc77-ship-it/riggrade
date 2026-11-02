# RigGrade compute supply ledger: method, v0 (draft)

Status: draft, 2 Nov 2026. No value is published under this method until it
has been reviewed. The method is written before any number, so the rules
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
any is published; none is a forecast.

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
  it.
- **Delivery gap.** Link 6 delivered-versus-claimed ratio for a class is
  below 0.9 at the median across at least five rigs. Reads: listed capacity
  of that class is underperforming its own claims. Published only at that
  sample size.

Every signal names the series, the period and the threshold it used, and
links to the published values. A signal that fails to fire is not reported
as absence of the condition; it is reported as "no signal", with the series
that were missing, if any.

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
