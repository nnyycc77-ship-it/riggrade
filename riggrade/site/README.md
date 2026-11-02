# Site context snapshots

Read by `riggrade/siterisk.py` (method: `docs/site-risk-method.md`).

- `nri_counties.csv`, `eia_state_prices.csv`, `eia_state_mix.csv` and `SOURCES.json` are written by the
  `site-sources` workflow from public US federal datasets. Never edit them by hand.
- `local_actions.csv` and `countries.csv` are written by hand, one row per primary read: a reader who
  carries the decision reads the source at its own address and records `read_by` and `read_on`. A copy
  fetched by someone else is not a primary read. An empty file means nothing has been read yet, not that
  nothing exists.
