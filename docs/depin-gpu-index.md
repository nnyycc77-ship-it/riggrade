# DePIN GPU reality index: method

Status: v0, 3 November 2026. Published weekly on RigGrade's X account. Information, not advice, an offer or a
forecast. It says nothing about any token, and RigGrade holds no position on it in this method.

## What it shows

How many GPUs are in use on decentralized GPU networks, as each network itself publishes it. RigGrade reads each
network's own public figures once an hour, keeps the readings for 60 days, and posts a 7-day average once a week.

| Network | Published figure read | What the index prints |
|---|---|---|
| Akash | `console-api.akash.network/v1/gpu`: GPUs allocatable and allocated | share of GPUs allocated, 7-day average, change on the week before |
| Clore | `api.clore.ai/v1/marketplace`: every listed server and its rented flag; GPUs from each server's `Nx model` line | share of listed GPUs on rented servers, 7-day average, change on the week before |
| io.net | `api.io.solutions/v1/io-explorer/network/info`: active, verified and registered GPUs | the latest active and verified counts, as io.net reports them |

## Rules

- **Each network's own numbers.** Nothing is estimated, adjusted or filled in. A network that does not answer in an
  hour has no reading for that hour; a week with no readings has no post.
- **The same words for every network.** No network is called better or worse, and none is ranked.
- **io.net is quoted, not divided.** Its endpoint does not define "active" or say whether "verified" GPUs are online,
  so the index prints its two counts and does not compute a share from them.
- **What it is not.** Listed or allocated is not proof that a GPU is real, online or the model it claims to be. That
  takes a check on the box itself: `pip install riggrade` then `riggrade-preflight` (`docs/preflight.md`).
- **No figures of RigGrade's own,** no token, price or market commentary.

Sources not included yet: Vast and RunPod (a full count needs an account), Nosana (its public stats are token figures,
not GPU use), Render (no public GPU-use figure found).
