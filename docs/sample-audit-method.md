# Sample audit of a GPU network's listed supply: method

Status: v0, written 3 November 2026, before the first run. Owner approval for the first run: up to 20 USD of
rentals. Published before any box is rented, so the rules cannot be fitted to the results.

## The question

Of GPUs a network lists as available, how many can a renter actually get, and do they behave like the card they
are listed as? A network's own supply figure cannot answer that. A renter can, by renting a few and checking them.

## The sample

- **Network:** io.net for the first run. It publishes the largest gap between verified and active GPUs and the
  least way to check it from outside.
- **Size:** up to 20 boxes, or as many as 20 USD of rentals buys, whichever comes first.
- **Selection.** For each of the 5 GPU models the renter flow offers most of, take the listed offers in the order
  the flow shows them. Number them and pick 4 with random numbers drawn before the rental (Python
  `random.sample` seeded with the run's UTC date). A model with fewer than 4 offers gives what it has; the shortfall
  is not refilled from other models. Nobody chooses a box by its host, price or reviews.
- **Image:** the network's own PyTorch/CUDA image, so the load checks can run.

## On each box

Within 30 minutes of the box being ready, one command, then the box is released:

    pip install -q riggrade==0.3.1 && riggrade-preflight --skip-download --seconds 20 >/dev/null 2>&1; \
      python3 -c "import json; from runner import preflight as p; print('RGAUDIT <box> ' + json.dumps(p.anonymise(json.load(open('preflight-report.json')))))"

`anonymise` is the same summary the preflight's opt-in share sends: the model name the driver reports, GPU count,
memory, driver, the verdict and each check's status, and the measured numbers. No host name, address, card id or
serial is in it. The run sheet records, per box tried: the listed model and region, the listed hourly price, the
outcome, minutes to ready, and minutes billed.

## Outcomes, decided before the run

| Outcome | Meaning |
|---|---|
| `deploy_failed` | the rental did not start, or the network refused or timed out the deploy within 15 minutes |
| `not_reachable` | the rental started but no shell or command ran within 15 minutes |
| `gone_before_check` | the box vanished before the command finished |
| `ready` | the command ran; its line is counted |
| `ready_no_report` | ready, but no summary line came back (counted apart, never as a pass) |

For each ready box: the preflight verdict (pass, warn, fail); whether the reported card matches the listing
(`runner.sample_audit.model_matches`: the listed model's name appears in what the driver reports, and a different
variant such as "4090 D" for "4090" is a mismatch); whether the load checks ran; and which checks failed.

## Reporting

- Totals by `python3 -m runner.sample_audit --sheet sheet.csv --lines lines.txt`: counts per outcome, verdicts,
  model matches, failed checks, cost. Every box tried is counted, including the ones that never started.
- Published with this method, the run date, the seed and the run sheet. No host is named, and no box's line can
  be traced to its host.
- **What it is not.** A count of a small sample, measured once, at rental time. It is not an estimate of the whole
  network, not a grade of any host, and not a judgement of the network. It says nothing about any token.
- Under RigGrade's independence rules (`docs/independence.md`): RigGrade pays for this run itself, and no network
  or host has seen or approved it.
