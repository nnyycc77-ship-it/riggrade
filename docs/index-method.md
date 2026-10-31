# RigGrade GPU index: method, v0 (draft)

Status: draft. No index values are published yet. This method is published
before any number so that the rules cannot be fitted to the data.

## 1. What the index is

Two daily series for each v0 peer class (grading spec section 2):

1. **Listed price.** The listed on-demand price per GPU-hour on public GPU
   marketplaces.
2. **Performance-adjusted price.** The listed price divided by the
   throughput actually measured on graded rigs of that class. This
   shows what a unit of measured work costs, not what an hour of a card
   costs. It is published only once enough graded rigs exist (section 5).

The index is information. It is not an offer, a rate anyone is bound by, or
investment advice. RigGrade does not run trading, contracts or tokens on it.

## 2. Sources

A source is used only if it publishes its listings for programmatic reading
and its terms allow reading and republishing aggregates. Each source is
checked against its terms before its first use, and that check is recorded in
the source table below.

| Source | Status | Terms checked |
|---|---|---|
| Vast.ai public offers | adapter built, not yet run | not yet: host blocked from the build environment |

No host, machine or offer identifier is stored. Each run keeps only the
aggregates in section 3. Raw listings are read, aggregated in memory and
discarded.

## 3. Daily listed-price figures

For each source and class, one snapshot a day, taken at 00:00 to 01:00 UTC:

- **Offers in scope.** Listings that can be rented now, on demand, whose GPU
  model and memory map to a v0 class under the same `identify()` rule as
  grading. Interruptible, spot and bid listings are excluded. Listings that
  map to no class are counted and then dropped.
- **Price per GPU-hour.** The listing's total hourly price divided by its GPU
  count. Where a source's total includes a storage or bandwidth charge, it
  is included as listed, and the source table says so.
- **Published per class:**
  - offer count and GPU count;
  - 25th percentile, median and 75th percentile of price per GPU-hour,
    weighting each listing by its GPU count;
  - for sources that publish a verified flag, the share of GPUs that are
    verified.
- **Minimum sample.** A class with fewer than 10 offers from a source that
  day is published as "insufficient" for that source, with no price.
- **Combined figure.** The median across all in-scope GPUs from every source
  that met the minimum sample that day.

## 4. Listed is not paid

These are prices sellers ask for, not prices paid. A busy host rents above
its listing price only on bid platforms, which are excluded. An idle host may
sit at its listed price with no renter. The index says "listed" everywhere,
for this reason.

## 5. Performance-adjusted price

For each class and workload:

    performance-adjusted price = combined median listed price per GPU-hour
                                 / median measured throughput per GPU-hour

Throughput comes from graded sessions. It is the same ruler and the same
first session per rig used for the class references (grading spec section 13).
It is published only once at least 5 distinct rigs of that class have been
graded. Until then it reads "reference-building", the same rule as speed
grades.

## 6. Rules that do not change silently

- **Versioned method.** This document carries a version. A change is posted
  at least 14 days before it applies, with the reason. The version in force
  is stated next to every value.
- **History is never rewritten.** A wrong value gets a correction appended
  beside it, with a reason. The original stays visible.
- **Gaps stay gaps.** A day with no snapshot, or a source that fails, is shown
  as missing. It is never filled in.
- **No discretion.** No value is adjusted by hand. A source suspected of
  manipulation is suspended under a published reason. It is never quietly
  down-weighted.

## 7. Known weaknesses

- **Few sources.** Early on, one source can dominate the combined figure.
- **Thin graded data.** The performance-adjusted series will be thin for
  months.
- **Hosts can see the method.** Hosts can see the method and list to suit
  it. The minimum sample and the GPU-count weighting limit this; they do
  not remove it.
