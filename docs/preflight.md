# Preflight check (free, not a grade)

A one-minute check to run on a GPU box right after renting it, or on your own
box before listing it. It answers one question: is this box behaving like the
card it reports?

The largest GPU buyers pay for capacity only once it passes acceptance
testing. Preflight is that first check for everyone else: a renter with no
lawyers and no test plan gets a verdict in a minute.

Install and run on the rented box (`pip install riggrade`; torch optional,
the GPU load checks need it):

    riggrade-preflight [--seconds 30] [--url URL] [--skip-download] [--dir PATH]
                       [--shared] [--fail-on-warn]
    # the same, from a checkout or inside the benchmark container:
    python3 -m runner.preflight ...

From an agent, run it as an MCP tool: `riggrade-mcp` on the rented box
exposes `gpu_preflight` (and `gpu_price_index`, `verify_certificate`,
`riggrade_service_info`).

No results are sent anywhere. Results print to the screen and to
`preflight-report.json`. The download check only reads a public file to time
it. Exit code 0 means pass or warn, 1 means fail; with `--fail-on-warn`, warn
exits 1 too. The report file always carries `pass`, `warn` or `fail` for every
check and for the overall `verdict`, so an agent gating a long run can tell a
warn from a clean pass.

## Share your result (opt in)

Add `--share` (or `share: true` on the MCP tool) and the runner sends an
anonymised summary to the RigGrade pool after the check: the GPU model and
driver, the verdict, each check's status and the measured numbers. No
hostname, path, id, address or error text is sent, and the service keeps
only those fields (`docs/preflight-pool-method.md`). The summary is what
`preflight-report.json` holds under `measures` plus the statuses. Once a
GPU class has five shared results and the method is reviewed, the class
aggregates publish free at `GET /riggrade/v1/index/quality`. Sharing never
changes the verdict, and a failed send is printed, not raised.

## Exclusive box assumed

Preflight in this form is built for an exclusive box: the whole card is
yours. On a shared or sliced box, use outside your slice can be a legitimate
neighbour rather than a fault. With `--shared`, or when MIG mode is on, the
foreign memory and foreign compute checks are shown as skipped with that
reason, and the report sets `exclusive_box_assumed` to false. The other
checks still run.

## What it checks

| Check | How | Warn | Fail |
|---|---|---|---|
| Foreign memory (exclusive box) | memory in use before the check starts | 1 GiB | 2 GiB |
| Foreign compute (exclusive box) | utilisation before the check starts | 10% | 30% |
| Clocks under load | median SM clock / max SM clock during a 30 s matmul load | below 80% | below 60% |
| Slowdown reasons | thermal or hardware slowdown flags seen during load | | any |
| Temperature | peak during load | 85 C | 90 C |
| PCIe link | generation and width during load against what the card supports | narrower or slower | |
| Driver vs CUDA | driver's CUDA version against the CUDA build of installed torch | older minor | older major |
| Download rate | MB/s pulling a public model file for up to 15 s | below 50 | below 10 |
| Disk write | MB/s writing 1 GiB with fsync, named with the filesystem and mount it ran on | below 200 | below 50 |
| ECC errors | corrected and uncorrected ECC errors since boot, on cards with ECC enabled | 10 corrected | any uncorrected, or retired pages pending |
| Host CPU load | 1-minute load average per CPU before the check's own load | 1.0 | 2.0 |
| Host memory | MemAvailable as a share of RAM | below 20% | below 10% |
| Host swap | swap in use, where swap exists | 10% | 50% |
| CPU governor | the frequency governor of CPU 0 | powersave | |
| Memory bandwidth | GB/s of device-to-device copies, a 3 s sample per GPU | below 100 | below 50 |

A power cap during full load is normal on most cards. It is reported, and it
does not fail the check. A check that cannot be measured is shown as skipped,
never as passed: ECC on a consumer card, a governor file the container
cannot read, a bandwidth sample without torch.

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
