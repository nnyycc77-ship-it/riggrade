# Sample audit of a GPU network's listed supply: method

Status: v2, 3 November 2026, before the first rental. v2 adds grade sessions on the same rentals, inside the same
20 USD (owner direction, 3 Nov 2026: no extra money). v0 was published before any box was rented; v1 writes in
the coordinator's review conditions (board Messages 332 and 333) and the draw exactly as the first run recorded it,
also before any rental. Owner approval for the first run: up to 20 USD of rentals.

## The question

Of GPUs a network lists as available, how many can a renter actually get, and do they behave like the card they
are listed as? A network's own supply figure cannot answer that. A renter can, by renting a few and checking them.

## The sample

- **Network:** io.net for the first run. It publishes the largest gap between verified and active GPUs and the
  least way to check it from outside.
- **Size:** up to 20 boxes, or as many as 20 USD of rentals buys, whichever comes first.
- **Selection.** In the renter flow, count the deployable offers per GPU model by the exact model label on each
  offer, in the order the fully loaded flow shows them. The 5 models with the most offers are sampled; among models
  tied on count, the ones appearing first in that display order take the remaining places. Number each model's
  offers 1 to n in display order. The seed is the run's UTC date as an integer (YYYYMMDD, e.g. 20261103). For each
  model, in its own draw, `random.Random(seed).sample(range(n), min(4, n))`, plus one, gives the offer numbers
  picked; every model reseeds, so the draw does not depend on the order the models are taken in. A model with
  fewer than 4 offers gives what it has; the shortfall is not refilled. Nobody chooses a box by its host, price or
  reviews. The picks (seed, models, offer counts, picked numbers, and each pick's listed model, GPU count, region
  and hourly rate) are recorded and saved before the first rental, and anyone can reproduce them from the seed.
- **Attempt order and the running spend stop.** The picks are attempted in ascending listed hourly rate, so the
  spend cap tries as many picks as it can; price orders the attempts only, never the selection. The listed hourly
  rate is recorded before each rental. After each release the credits balance is read, and spend so far is the
  starting balance minus it. Where the console states no per-minute billing, each box is assumed to bill a full
  hour at its listed rate. No new box is rented when spend so far plus that box's listed hourly rate would pass
  20 USD. Picks left untried by the cap are reported as `not_attempted`: counted, shown, and never dropped.
- **Image:** the network's own PyTorch/CUDA image, so the load checks can run.

## On each box

Within 30 minutes of the box being ready, one command, then the box is released:

    pip install -q riggrade==0.3.1 && riggrade-preflight --skip-download --seconds 20 >/dev/null 2>&1; \
      python3 -c "import json; from runner import preflight as p; print('RGAUDIT <box> ' + json.dumps(p.anonymise(json.load(open('preflight-report.json')))))"

`anonymise` is the same summary the preflight's opt-in share sends: the model name the driver reports, GPU count,
memory, driver, the verdict and each check's status, and the measured numbers. No host name, address, card id or
serial is in it. The run sheet records, per box tried: the listed model and region, the listed hourly price, the
outcome, minutes to ready, and minutes billed. `listed_model` is the model label exactly as the renter flow
names it (memory included where the flow gives it, e.g. `V100_32G`), and the listed GPU count goes in
`listed_gpus`, not in the model name.

## Outcomes, decided before the run

| Outcome | Meaning |
|---|---|
| `deploy_failed` | the rental did not start, or the network refused or timed out the deploy within 15 minutes |
| `not_reachable` | the rental started but no shell or command ran within 15 minutes |
| `gone_before_check` | the box vanished before the command finished |
| `ready` | the command ran; its line is counted |
| `ready_no_report` | ready, but no summary line came back (counted apart, never as a pass) |
| `not_attempted` | picked, but left untried because the spend stop was reached (counted apart, never as tried) |

For each ready box: the preflight verdict (pass, warn, fail); whether the reported card matches the listing; whether
the reported GPU count matches the listed count; whether the load checks ran; and which checks failed.

**Model match** (`runner.sample_audit.model_matches`), on name parts, not on a substring:
- vendor and brand words (NVIDIA, GeForce, Tesla, Quadro, RTX, GTX) and form descriptors (SXM, PCIe, HBM, NVL)
  are set aside, so a chip-only listing matches the full driver name: A100 matches A100-SXM4-80GB, H100 matches
  H100 80GB HBM3, A6000 matches RTX A6000;
- the remaining model parts must be the same, and a performance variant on one side only refuses a match:
  RTX 4090 does not match 4090 Ti or 4090 D, and RTX 4080 does not match 4080 SUPER; L40S does not match L40;
- where the listing names a memory size (V100_32G), the driver name's memory, or else the measured memory, must
  agree within 10%; if neither shows memory, the match is not proven and counts as a mismatch.

**Compute measured** means at least one load check (clocks under load, slowdown, temperature, memory bandwidth)
has a pass, warn or fail. A box without torch reads compute warn with basis "not measured" and has no load-check
result, so it is never counted as compute measured.

## Grade sessions on the same rentals (v2)

The owner asked that the same rentals also add grade sessions to RigGrade's reference data, inside the same 20 USD.
The audit comes first and is never changed by the grade step.

- **Which boxes.** Only single-GPU picks whose model has a grading class (`riggrade/classes.py`): A100, H100 and
  L40S in the first run. In attempt order, the first eligible box of each such model is graded. Grading spec
  section 13 counts at most 2 rigs per operator in a class; the first run grades 1 per model. No other choice is
  made: a box is graded because of its place in the attempt order, never its host, price or reviews.
- **Order on the box.** The audit command runs first and its `RGAUDIT` line is copied. Only then does the grade
  session start, with the pinned benchmark container named in `container/bench-image.env` (the server accepts no
  other). The grade never changes the box's audit outcome or line.
- **What it needs.** (1) The network must let a renter run that container image; if it does not, no grade
  session runs and the audit goes ahead unchanged. (2) Each session is a free reference session, so it needs the
  operator claim: a 0 USDC x402 authorisation to the payout address, signed by the operator's own wallet, verified
  and never settled (grading spec section 13). No money moves. Without the claim the session is not graded.
- **Time and the spend stop.** A grade session runs about 1.5 hours (model download, gate, warm-up, ruler passes,
  the 60-minute sustained run), so a graded box is counted as 2 billed hours in the running spend stop. The stop
  applies to grade and audit together: no new box, and no grade session, starts if spend so far plus its billed
  hours at the listed rate would pass 20 USD. A grade session left unstarted by the cap is reported as such; the
  box's audit result stands.
- **Workload.** The first run's grade sessions run the inference workload only (`--workloads inference`). Each
  session counts only toward its class's inference provisional reference, never toward training, and the run
  sheet and the audit report record the workload beside the session id. No other workload is counted from a
  session unless it was run in full under the grading spec (Coordinator Message 339).
- **What it records.** Each graded rig counts toward its class's provisional reference under the operator rules,
  tagged as declared by the buyer and unattested. No speed grade is issued in a class until its reference meets
  the spec (5 rigs, 3 distinct verified operators); RigGrade's own rentals can never meet that alone. The audit
  report lists which boxes were graded and their session outcomes; grades are published under the grading spec,
  not in the audit report.

### Grade steps on a graded box (run book)

Only for the boxes named above, only after the box's audit line is copied, and only if the network lets the renter
run the pinned image by digest:

1. Start the pinned benchmark container by digest, never by tag. Image:
   `ghcr.io/nnyycc77-ship-it/riggrade-bench@sha256:6c2087bee4ee2d31b139fcfe96679d2d35e806d3e5d6def5fd32a71b3b3a767d`
   (the digest in `container/bench-image.env`). Environment: `RIGGRADE_IMAGE_DIGEST` set to that same
   `sha256:...` value, and `RIGGRADE_ENDPOINT=https://riggrade-674098696181.us-central1.run.app/riggrade/v1`.
2. Inside it, run the session for the inference workload as the buyer:
   `python -m runner.main --endpoint https://riggrade-674098696181.us-central1.run.app/riggrade/v1 --workloads inference --runner buyer`.
   It takes about 1.5 hours and ends by printing the session id and the grade URL.
3. Write the session id and the workload (inference) on the run sheet against the box, then release the box.
4. The claim is signed afterwards from the operator's wallet: on the service's checkout page
   (`/riggrade/v1/checkout`), "Grade a benchmark session", paste the session id, sign the 0 USDC claim. No money
   moves. For the first run the owner directed (3 Nov 2026, in his own chat with the coordinator) that the
   coordinator does this step in the browser with a dedicated claims wallet the owner set up and that holds no
   funds; that wallet is the operator on the record. Before every signature the signer checks that the amount is
   exactly 0 USDC and the recipient is the payout address the page shows, and signs nothing else.

If any step fails, the box's audit stands as recorded and the run sheet notes what failed.

## Reporting

- Totals by `python3 -m runner.sample_audit --sheet sheet.csv --lines lines.txt`: counts per outcome, verdicts,
  model matches, failed checks, cost. Every box tried is counted, including the ones that never started.
- **The tilt, in words.** With a full billed hour assumed per box and attempts in ascending listed rate, the
  tried set leans toward single-GPU and lower-rate picks. The report says so in words and shows the
  `not_attempted` counts beside the tried counts (Coordinator Message 336).
- Published with this method, the run date, the seed and the run sheet. No host is named, and no box's line can
  be traced to its host.
- **What it is not.** A count of a small sample, measured once, at rental time. It is not an estimate of the whole
  network, not a grade of any host, and not a judgement of the network. It says nothing about any token.
- Under RigGrade's independence rules (`docs/independence.md`): RigGrade pays for this run itself, and no network
  or host has seen or approved it.
