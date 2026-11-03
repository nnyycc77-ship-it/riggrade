# GPU health monitor for hosts: design note (draft for the coordinator's review)

Status: design, 3 November 2026, Adviser 1, at the owner's direction in Adviser 1's chat (the income plan's second
step, after the checkout and the home page). Under the standing rule (board Message 307) it counts as the owner's
decision once recorded on the board. The price is the owner's, under the price exception. Nothing here is built yet.

## 1. The problem and the product

A host with a few GPUs finds out a card went bad when a renter complains and cancels. That costs the host a rental,
and often a rating. The free preflight catches problems at one moment. The monitor runs the light part of it on a
schedule, tells the host the moment something degrades, and keeps a history a renter can see.

The code is open source, like everything else here. The product sold is the hosted part: the history kept by the
service, which the host cannot edit, and the public reliability page that shows it.

## 2. How it works

1. **The agent.** `riggrade-monitor` ships in the same pip package. The host runs it on each box, as a systemd timer
   or a loop. Every [proposed: 6] hours it runs the light checks:
   - ECC counts and retired pages (the change since the last run);
   - slowdown flags, clocks and temperature during a 10-second load;
   - PCIe width and generation;
   - memory already in use by someone else, before the load.
   It never runs while a renter's job holds the card: memory or compute already in use means "skipped, busy", never
   a fail.
2. **Alerts, local and free.** When a check moves from pass to warn or fail, the agent itself posts to the host's
   own webhook (Discord, Slack or any URL). The service never calls out to a host's URL, so it cannot be turned
   against other addresses.
3. **History, hosted and paid.** With a subscription, the agent also sends each run's result to
   `POST /riggrade/v1/monitor/report`. The report is the same anonymised shape as a shared preflight, plus the
   card ids, with no hostname, IP or path.
   - The service keeps the history append-only. A missed report shows as a gap, never as a pass.
   - Each run also goes into the GPU record under the card's `card_id`, with the evidence tag "declared:host,
     monitor".
4. **The reliability page.** `GET /riggrade/v1/monitor/{box}` shows, for a box the host chooses to publish:
   - the share of scheduled checks that passed, each failure kept visible, and the gaps;
   - over the last 30 and 90 days.
   A host can link it from its listing text. No marketplace has been recorded as permitting badges, so there is no
   badge in listings.

## 3. Payment and identity

- **A subscription is per box per month,** paid by x402 or at the checkout from the host's own wallet. The paying
  wallet is the box's operator, with the payer role "provider" printed.
- **[Owner decision] The price.** Proposed: free for the first month, then 5 USDC per box per month, in line with
  uptime monitors at $5–20 a month. The price never depends on the results.
- **The token.** The subscription returns a random monitor token. The agent keeps it on the host and sends it with
  each report. It is the host's own secret, never logged by the service, and can be rotated.

## 4. What it does not do

- It does not grade the box. The reliability page shows measured shares and failures, and the grade stays the
  grading spec's.
- It does not see renters' work or data. It runs only when the card is idle, and sends no file names, processes or
  addresses.
- It does not alert renters or marketplaces. Alerts go only to the host.
- A paying host cannot hide a failure. History is append-only, and a host that stops paying stops new reports. Past
  entries in the GPU record stay.

## 5. Measuring it

The digest's funnel gains lines for monitor subscriptions, the boxes reporting, and reports a day. The checkpoint at
31 January 2027: if fewer than [proposed: 10] outside boxes report after the outreach has run, the paid tier is
reconsidered.

## 6. Open questions

For the coordinator:

1. The light check set and the 6-hour default, as a method.
2. "Skipped, busy" when a renter holds the card: is that the right fail-closed reading?
3. The reliability page's wording, so a high share never reads as a promise.

For the owner:

4. The price after the free month.

## 7. Build estimate

About five days: the agent and its local alerts, the report route and history store, the reliability page, the
subscription through the existing x402 and checkout paths, tests, and docs. The first two days (the agent and its
local alerts) are useful on their own, free, before any paid part exists.
