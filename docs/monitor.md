# GPU health monitor (free agent)

For hosts: run the light part of the preflight on a schedule, and get told on your own Discord, Slack or any webhook
the moment a GPU check gets worse, or recovers. Design and the planned paid part: `docs/monitor-design.md`.

    pip install riggrade
    riggrade-monitor --webhook https://discord.com/api/webhooks/...     # runs every 6 hours
    riggrade-monitor --once                                             # one run, then exit

Options: `--every HOURS` (default 6), `--label NAME` (how alerts name the box), `--state PATH` (default
`~/.riggrade-monitor.json`), `--seconds N` (load length, default 10).

## What each run does

1. Reads whether memory or compute is already in use. If it is (a renter's job, or anything else), the run is
   recorded as "skipped, busy": the card is never loaded and nothing reads as a failure.
2. Otherwise reads the ECC counters and runs a short load. Under the load it reads clocks, slowdown flags,
   temperature and the PCIe link, with the same rules and thresholds as the preflight (`docs/preflight.md`).
3. Compares each check with its last known status. A check that got worse (pass to warn or fail, warn to fail) or
   recovered is posted to your webhook. A check that stays bad is not repeated every run. A busy run changes
   nothing. On the very first run there is no last status, so any check that is not a pass is posted once,
   marked "new". If the webhook cannot be reached, the alert is tried again on the next run.

No results are sent to RigGrade. The only network call is the alert to the URL you give, and only when something
changed. The message body has `content` (read by Discord) and `text` (read by Slack), plus the events as JSON for
any other receiver.

## Grade this rig free while it is idle

Owner decision, 4 Nov 2026. Your idle GPU time can earn your rig a free RigGrade grade, which you can show to
renters. It also builds the class references that speed grades need (spec section 13).

1. Find your rig id: `riggrade-preflight` prints it after a pass (`rg_` and 16 characters).
2. On the [checkout page](https://riggrade-674098696181.us-central1.run.app/riggrade/v1/checkout), choose "Grade
   my rig free when idle", paste the rig id and sign with your wallet. It is a 0 USDC operator claim: verified,
   never settled, no money moves. You get a token valid for 30 days. Keep it like a password.
3. Run the monitor with it (Docker with GPU access is needed):

       riggrade-monitor --webhook https://discord.com/api/webhooks/... --grade-when-idle --claim-token rgc_...

When every card has been idle for `--idle-hours` (default 6) and the rig has not been graded in
`--grade-every-days` (default 30), the monitor runs the pinned benchmark container once, for about 1.5 hours on
the inference workload, then asks for the free reference grade with your token. It tells your webhook the result.
The first run downloads the benchmark image, which is several GB.

- **The card stays the renter's.** Every 30 seconds during the run the monitor lists the processes on the GPUs.
  If any of them is not inside the grade container, it stops the container at once, records "stopped, card
  needed" and starts the idle clock again.
- **Only a rig whose checks all passed is graded.** A warn or fail run never starts a grade.
- **What is sent.** With this option the monitor and the container talk to RigGrade: the benchmark session (the
  measured bundle, as for any grade) and the grade request with your token. Without it, nothing is sent.
- **The token.** It covers free reference sessions for that one rig only, until it expires. All operator rules
  still apply: at most 2 rigs per operator count toward a class reference.

## Running it as a service

A systemd unit, one per box. `ExecStart` must be the path pip installed the command to on that box: check it with
`command -v riggrade-monitor`; `/usr/local/bin` below is only an example.

    [Unit]
    Description=RigGrade GPU monitor
    After=network-online.target

    [Service]
    ExecStart=/usr/local/bin/riggrade-monitor --webhook https://hooks.example/your-url --label gpu-box-1
    Restart=on-failure

    [Install]
    WantedBy=multi-user.target

A warn or fail is a measurement of the box at that moment, not a grade. The fix-it report explains the likely cause
and the usual fix.
