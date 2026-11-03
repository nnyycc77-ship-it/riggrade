# Rental check: a design note (draft for the coordinator's review)

Status: design only, nothing built. Written 3 November 2026 by Adviser 1 at the owner's direction in Adviser 1's
chat ("build a marketplace with our grade for agents to pick a GPU rental channel"). That is owner direction on
Adviser 1's side; it binds nothing until the coordinator records the owner's confirmation in his own chat. No
price, listing, outreach or spend is proposed for action here.


**v0 built (3 November 2026, owner go):** both routes are live, free, with no payment and no wallet, and store
nothing. The plan returns no site context, because the site layer stays internal until the owner launches it. The
check does not add runs to the GPU record yet; that waits for the record method's consent step. Any price is set
later by the owner directly.

## 1. The question and the answer in one paragraph

An agent about to rent a GPU wants to know where to rent and whether the box it got is the box it paid for.
RigGrade will not run a marketplace: holding hosts, renters, payments and disputes is a different business, and
selling rentals would end the independence that makes the grade worth anything. It also cannot be a
price-comparison router over live listings, because no live listing source has cleared our terms check. Vast's
terms forbid using its data "to construct, publish, or maintain any index, benchmark, pricing-comparison service,
market-data product" without a written agreement (`docs/index-method.md`, section 2). What RigGrade can do is
sell the **rental check loop**: a plan before renting, and a keep-or-cancel answer in the first minutes after
renting, measured on the box itself. Every rental an agent makes then becomes a call, and every check adds to the
record. That is the usage and the data the exit plan lacks.

## 2. The loop

1. **Plan (before renting).** The agent says what it will run and what it needs. RigGrade answers with the listing
   filters that matter for that workload, the site context for any regions the agent names, and the exact
   preflight command to run once the box is up.
2. **Rent.** The agent rents on the marketplace of its choice, with its own account. RigGrade never sees the
   account, the rental or the money for it.
3. **Check (first minutes after renting).** The agent runs the preflight and sends the report. RigGrade answers
   keep or cancel, with the measured reasons, the card history for each card it has seen before, and the
   marketplace's own cancellation window stated as the agent's to check, never as a promise.
4. **Record.** With the consent the report already carries, the check adds the run to the GPU record under the
   card's `card_id`. A card that failed before shows that history to the next agent that rents it.

## 3. Routes (v0)

Both follow the existing pattern: a free preview, the full answer paid by x402 from the agent's own wallet, every
payer screened, every sale in the hash-chained sale log. During the free trial both are free and log
`free_trial`.

- `POST /riggrade/v1/rental/plan`
  - In: `workload` (one of the grading spec's workload classes, `inference` or `training` in v0), `gpus` (count
    and peer class), `hours`, optional `needs` (ECC on, minimum host memory, storage throughput, PCIe width,
    interconnect), optional `regions` (US state or country codes the agent is choosing between).
  - Out: `filters` (each requirement, why it matters for the workload, and the preflight check that will
    confirm it); `site` (the site context lines for each region named, no score); `preflight` (the install line
    and command, `pip install riggrade` then `riggrade-preflight`, with the expected run time); `keep_rules` (the
    published thresholds the check will apply).
  - It names no marketplace, no host and no price.
- `POST /riggrade/v1/rental/check`
  - In: the `preflight-report.json` from the rented box, and optionally the `plan` answer it follows.
  - Out: `verdict` (keep, keep with warnings, or cancel); `reasons` (each failed or warned check, measured value
    against the rule); `history` (per `card_id`: earlier records with their evidence tags, or "first sighting");
    `fixit` (the count of findings the fix-it report would address, as today's preview); `recorded` (whether the
    run was added, under its consent).
  - Cancel is advice with its measurements. The agent decides, under its marketplace's own terms.

No new data is collected. The plan uses the published spec and the site layer. The check uses the preflight, the
grading rules and the GPU record, all built and tested.

## 4. The keep rules

Published with the method, versioned, no hidden weights. v0 draft:

- **Cancel** on any preflight fail that the plan named as a requirement (for example ECC off when the plan asked
  for ECC), on a peer class that does not match the one rented, or on a card whose record shows the same failure
  within the last 30 days.
- **Keep with warnings** on a warn finding, or a failure the plan did not name as a requirement.
- **Keep** otherwise.

The thresholds are the grading spec's own. The check never invents a threshold the free preflight does not print.

Conditions from the coordinator's review (Message 308), part of the method:

- **History-based cancels print their evidence.** A cancel that rests on a card's record history prints the
  evidence tag and attestation state beside it. An unattested history never reads as proof that the same physical
  card failed before.
- **Site lines keep their grain.** Any site context line the plan returns for a region states its grain in the
  site layer's scope wording, so a state-level line never reads as a county finding.
- **The verdict is advice at that moment.** The check answer says the verdict was measured at that moment, and that
  cancelling and any refund sit under the marketplace's own terms.
- **Ranking agent-supplied offers stays off, fail closed,** until a primary terms read for a marketplace is recorded
  as permitting it. No public claim about competing aggregators is made until that question is checked.

## 5. What it does not do, and why

- **No live listings, no prices, no host ranking.** No source is cleared for it (section 1). A ranking of offers
  the agent brings itself is section 7's question 1, not v0.
- **No marketplace money.** No referral or affiliate fee. The agent pays, so the answer stays neutral. Taking
  marketplace money is an owner decision, and this note advises against it.
- **No custody.** RigGrade never rents, holds an account, or touches rental funds.
- **No location capture.** The preflight still sends no hostname, address or location. Regions in the plan are
  what the agent declares.

## 6. How we will know it works

Read from the sale log and the digests, as records and never as rigs or users:

- checks a week from outside wallets, kept apart from the owner's and the advisers' runs;
- the share of checks that follow a plan call (the loop being used, not only the preflight);
- records added through checks, and cards seen more than once (the history starting to matter);
- cancel verdicts with their reasons (evidence of the problem the product exists for).

At the end of the free trial (31 December 2026) these four numbers go to the owner with the price question.

## 7. Open questions

For the coordinator (long reads and method review):

1. **Agent-supplied offers.** If an agent sends offers it fetched itself under its own marketplace account, may
   RigGrade rank them? This turns on each marketplace's terms for its users, not ours. Vast first, then RunPod and
   one more. Until a marketplace is recorded as permitting it, v1 ranking stays off.
2. **The keep rules** in section 4, as a method change, before anything is built.
3. **Wording of cancel advice**, so it never reads as a promise of a refund or as a claim about a host's conduct.
4. **Existing aggregators** (for example Shadeform's unified API) and whether any already sells an independent
   per-box check after rental. Unverified on our side.

For the owner:

5. Whether to build v0 now, free inside the trial window.
6. The price after the trial, and whether plan and check are priced apart or together.
7. Any marketplace conversation (a data licence, or permission to rank offers), which is outreach.

## 8. Build estimate

Once the coordinator accepts sections 3 and 4: about two to three days for both routes, the descriptor and
discovery entries, tests, and the docs, mostly reuse of the preflight, grading, record and site code. Nothing
deploys without the usual owner confirmation recorded by the coordinator.
