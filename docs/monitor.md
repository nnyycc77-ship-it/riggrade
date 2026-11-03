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
