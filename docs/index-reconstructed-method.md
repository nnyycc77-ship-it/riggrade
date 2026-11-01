# RigGrade GPU index: reconstructed history, method v0 (draft)

Status: draft, first values published 1 Nov 2026 under this method as written
(the mapping table in section 3.1 was fixed before the first file was read).

## 1. What it is, and what it is not

The reconstructed series estimates past listed GPU prices, by peer class,
from third-party records. It is a separate series:
- **Labelled "reconstructed"** wherever it is shown. Every view carries the
  source attribution required by section 2.
- **Never joined** to the live series of `docs/index-method.md`. The two may
  be shown side by side but are not spliced into one line, and no value of
  one is used to fill a gap in the other.
- **Prices only.** Past grades are never reconstructed. The
  performance-adjusted price (live method section 5) has no reconstructed
  form, because grades exist only where sessions ran.

Like the live index, it is information, not an offer, a rate or advice.

## 2. Sources

A source is used only once its licence or terms allow reuse and
republication of derived figures, and that check is recorded here before its
first use.

| Source | Granularity | Licence / terms | Status |
|---|---|---|---|
| GetDeploying GPU price history | weekly, per GPU model and billing type, across providers | checked 1 Nov 2026: the published dataset files (`/dataset/gpu-prices/weekly.csv`, `weekly.json`, and each model's `history.csv` or `history.json`) are licensed CC BY 4.0, under section 8 of their terms. The licence covers those files only, not the site, its HTML or its API. Section 5 bars scraping the site, so only the published files are downloaded, never site pages | permitted: download the published files only, with attribution |
| Vast.ai public pricing feed | daily median per model, about 90 days | checked 1 Nov 2026: **not permitted**. The feed is free for display and citation, but "use in any index, benchmark, or derivative product requires a data license" | not used |

**Attribution.** Every view of a series built from a CC BY 4.0 source names
the source, links to it and to the licence, and says the figures were
changed: mapped to peer classes and re-aggregated.

## 3. Mapping to peer classes

- **Model names.** A source's model name maps to a v0 peer class only through
  a published mapping table, kept in this repository and versioned with this
  method. A model with no entry in the table is dropped and counted.
- **Ambiguous names.** A name that could fit two classes (for example an
  "H100" with no form or memory stated) maps to neither and is dropped. It
  is never split or guessed.

### 3.1 Published mapping table (GetDeploying, method reconstructed-v0-draft)

The table lives in `pricewatch/reconstructed.py` (`MAPPING`) and is
versioned with this method; the service and the published files carry it.

| Source name | Peer class | Note |
|---|---|---|
| nvidia-h200 | h200-sxm-141 | one v0 class for the chip |
| nvidia-l40s | l40s-48 | |
| nvidia-rtx-5090 | rtx5090-32 | |
| nvidia-rtx-4090 | rtx4090-24 | |
| nvidia-rtx-3090 | rtx3090-24 | |
| nvidia-h100 | dropped | form not stated: could be h100-sxm-80 or h100-pcie-80 |
| nvidia-a100 | dropped | memory not stated: could be a100-sxm-80, a100-pcie-80 or a100-pcie-40 |

Every other source name is dropped and counted (`dropped_rows_by_source_name`
in the published file). Only `ON_DEMAND` billing rows are used; reservation,
spot and custom rows are not.

**Proposed amendment for review (not in force):** a chip-level series for
H100 and A100, labelled by chip family rather than peer class, so the two
most-rented data-centre chips are not absent. Under section 3 as written they
are dropped. If accepted, it takes effect 14 days after the notice (section 5).

### 3.2 Import record

Each weekly import appends to `imports` in the published file: retrieval
time, URL, byte count and SHA-256. The published files are
`riggrade/index/reconstructed-getdeploying.json` (the series, the mapping,
the corrections and every import record) and
`riggrade/index/reconstructed-getdeploying.csv` (one row per class and week,
attribution on every row). They are served free at `GET /riggrade/v1/index`
(latest week per class) and by the MCP tool `gpu_price_index`, and are
refreshed weekly by the `index` workflow (Tuesdays 04:13 UTC).

First import: 2026-11-01T21:36:00Z, 4062677 bytes, SHA-256 20e445304c88a18750d6d831121317fea85135d56f5ea91a993580e93d26868a, source latest week 2026-10-26.

## 4. Figures

- **Native granularity.** Each source's figures keep their own granularity,
  weekly or daily. Weekly figures are never turned into daily ones.
- **One week, one value.** For each class and week, a source's figure is the
  value it states for that week, as it states it (median or mean), with that
  basis recorded next to it. Figures with different bases are not combined.
- **Each source stays separate.** No combined figure across sources is
  published for the reconstructed series. Their methods differ and are not
  ours to reconcile.
- **Gaps stay gaps.** A missing week stays missing.

## 5. Rules shared with the live index

These rules are as in `docs/index-method.md` section 6:
- the method is versioned;
- any change gets 14 days' notice;
- history is append-only, with corrections appended beside the original;
- nothing is adjusted by hand.

On top of those, each import records the source file's URL, retrieval time,
byte count and SHA-256.

## 6. Known weaknesses

- **Other people's methods.** These are third-party figures. Which offers a
  source counted, and how it took its median, are its own choices and are
  not visible to us.
- **Survivorship.** A source's past figures may reflect only the providers it
  tracked at the time.
- **Coarse class mapping.** Many sources name a chip family rather than a
  form and memory size, so some classes will have no reconstructed series.
