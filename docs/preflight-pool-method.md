# Pooled preflight results: method, v0 (draft)

Status: draft, 2 Nov 2026. Collection is on (owner decision, 2 Nov 2026:
yes to pooling anonymised preflight results). Publication of the
aggregates is off until this method has been reviewed; the service answers
`GET /riggrade/v1/index/quality` with 503 and this document's address until
then. The method is written before any aggregate is shown.

## 1. What is collected, and only with consent

A renter who runs the free preflight may opt in with `--share` (CLI) or
`share: true` (MCP tool). Nothing is sent otherwise. What is sent is the
anonymised summary the runner builds from its own report, and the renter
can print it before sending (`preflight-report.json` holds the same
`measures`; the summary is that plus statuses, minus every detail string).

The service keeps exactly these fields per shared report and drops
everything else (`riggrade/pool.py`, `clean()`):

| Field | Why |
|---|---|
| hour of receipt (UTC, rounded down) | ordering and age; no finer time |
| GPU model name, count, memory size, driver version, CUDA versions | peer-class mapping and software context |
| peer class (mapped by the same rule as grading) | the aggregation key |
| verdict; each check's status (pass, warn, fail, skipped) | the shares published |
| measured numbers: clock ratio, peak temperature, memory bandwidth, download rate, disk write rate, TFLOPS, ECC counts, host load, memory and swap | the medians published |
| filesystem type of the disk test (no path), CPU governor name | context for the disk and host findings |
| whether the box was declared exclusive | the foreign-use checks' basis |

Not kept, and not accepted even if sent: hostnames, IP addresses, user
agents, rig ids, session ids, wallet addresses, mount paths, error
strings, detail strings, or any free text. The service does not record the
sender's address.

## 2. What is published

Per peer class, once the class holds at least **5** records: the count,
the share of pass, warn and fail verdicts, the share of each status per
check (per-GPU checks folded to the check name), and the median of each
measure with the number of records behind it. A class under 5 records
publishes only its count and "insufficient". Unknown classes are kept and
published under `unknown_class`, with the same gate.

Nothing is published as an individual record. The aggregates are
information, not a grade: nothing here enters a grade pillar, a bar, a
reference or a consistency rule (the same guard as the diagnostics
thresholds, board record 1 Nov 2026).

## 3. Retention

Records are kept for as long as the series runs, so medians can be
recomputed when the method changes. They carry nothing that identifies a
person, a host or a box, which is why no deletion path per sender exists:
there is nothing to find a sender by. If the method is retired, the file is
deleted and the series ends; the published aggregates stay as history.

## 4. Rules that do not change silently

As the index methods: versioned; a change posted 14 days before it applies;
aggregates never rewritten (a recomputation under a new method is a new
series); gaps stay gaps; no value adjusted by hand; a class suspected of
manipulated submissions (one operator flooding it) suspended under a
published reason. Opt-in submissions can be gamed; the gate, the medians
and the per-check shares limit it, they do not remove it.

## 5. Known weaknesses

Opt-in bias: renters who share are not a random sample of renters. Self-
reported: the runner is open code on the renter's own box, and nothing
attests that it ran unmodified. Thin: five records per class is the floor
for publication, not for confidence.
