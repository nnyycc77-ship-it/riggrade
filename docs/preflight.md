# Preflight check (free, not a grade)

A one-minute check to run on a GPU box right after renting it, or on your own
box before listing it. It answers one question: is this box behaving like the
card it reports?

    docker run --gpus all <riggrade image> python3 -m runner.preflight
    # or, from a checkout with torch installed:
    python3 -m runner.preflight [--seconds 30] [--url URL] [--skip-download] [--dir PATH]

Nothing is sent anywhere. Results print to the screen and to
`preflight-report.json`. Exit code 0 means pass or warn, 1 means fail, so an
agent or script can gate a long run on it.

## What it checks

| Check | How | Warn | Fail |
|---|---|---|---|
| Foreign memory | memory in use before the check starts | 1 GiB | 2 GiB |
| Foreign compute | utilisation before the check starts | 10% | 30% |
| Clocks under load | median SM clock / max SM clock during a 30 s matmul load | below 80% | below 60% |
| Slowdown reasons | thermal or hardware slowdown flags seen during load | | any |
| Temperature | peak during load | 85 C | 90 C |
| PCIe link | generation and width during load against what the card supports | narrower or slower | |
| Driver vs CUDA | driver's CUDA version against the CUDA build of installed torch | older minor | older major |
| Download rate | MB/s pulling a public model file for up to 15 s | below 50 | below 10 |
| Disk write | MB/s writing 1 GiB with fsync | below 200 | below 50 |

A power cap during full load is normal on most cards. It is reported, and it
does not fail the check. A check that cannot be measured is shown as skipped,
never as passed.

These failures are the ones renters most often report, and no marketplace
listing shows them: a card that reads 99% busy while held to a fraction of
its clock by heat, memory already taken by something outside the container,
a driver older than the software's CUDA build, and download speeds far below
the advertised figure.

## What it is not

It is a short check of one box at one moment. It does not compare the box
with other boxes of its class, and it does not watch it over time. That is
what a RigGrade grade does: a full session measured against published bars
for the peer class and workload, with an evidence pack.
