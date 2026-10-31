# Payments (x402, keyless facilitators)

The sale is off. This page describes how a grade is paid once the owner turns
it on.

## How a payment moves

The paid resource is `POST /riggrade/v1/grade` with `{"session_id": ...}`, one
stable URL for every session. The older per-session route
`/riggrade/v1/session/{id}/grade` still works.

1. A grade request without payment answers `402 Payment Required` with one
   payment requirement:
   - the price in USDC on Base, from the decided prices;
   - the payout address;
   - the USDC contract;
   - a 300-second window.
2. The buyer signs a USDC transfer authorisation from their address to the
   payout address and sends it in the `X-PAYMENT` header.
3. `riggrade.payment.FacilitatorAdapter` then works through these steps, in
   order:
   - **Check the payment locally:** scheme, network, recipient and amount.
     A mismatch is refused before any network call.
   - **Screen the payer** against the blocked-address list. If the list is
     missing or unreadable, every payer is refused.
   - **Verify with the first facilitator that answers.** It moves to the next
     facilitator only when one cannot be reached. A payment one facilitator
     calls invalid is refused, not retried elsewhere.
   - **Settle with the facilitator that verified.** If the settle call gets no
     answer, the charge is recorded as `unknown` and nothing is resubmitted.
4. The grade is issued, and the sale is appended to the hash-chained sale log.

Funds go straight from the payer to the payout address. A facilitator checks
the signature and submits it on-chain. It never holds funds. Nothing in this
code holds a private key.

If money may have moved but no grade was issued (`charged` is `true` or
`unknown`), the case is appended to the sale log as `charged_without_grade`,
for reconciliation and a refund by hand.

The 402 answer also carries x402 Bazaar discovery metadata (`extensions.bazaar`):
how to call the resource, an example output, and tags. A catalogue lists a
service only after a paid call settles and echoes that block. Shipping it
therefore lists nothing on its own. Making the first paid call is the listing
step, and it is the owner's decision.

## Configuration

    PAYOUT_WALLET_ADDRESS=0x...        public address only, set at deploy time
    RIGGRADE_FACILITATORS=https://x402.dexter.cash,https://facilitator.payai.network
    RIGGRADE_SCREEN_LIST=/path/to/blocked-addresses.txt

Facilitators are listed main first, then backup. Neither needs an account or
an API key for this use, as described in the x402 documentation. PayAI
documents a free allowance, with credentials needed past it.

## Running it

    python3 -m riggrade.serve --host 127.0.0.1 --port 8402
    # or any WSGI host: riggrade.serve:application

The adapter is built only when `RIGGRADE_FACILITATORS` and
`RIGGRADE_SCREEN_LIST` are both set. Even then the sale opens only when every
other switch is set too. `GET /riggrade/v1` lists the reasons the sale is off.

## The payer screen list

Build it from OFAC's SDN.CSV:

    python3 -m riggrade.screenlist SDN.CSV --out blocked-addresses.txt

Every `0x` address listed as a "Digital Currency Address" is kept, whatever
its ticker. The file starts with a provenance header: the source URL, the
input SHA-256, the build time and the address count. An input with no
addresses is refused, because that means the file is wrong, not that the
list is clean. Rebuild the list whenever OFAC publishes an update.

## Before the sale switch

- **Check each facilitator live.** Call its `/supported` endpoint and confirm
  USDC on Base (`base`) with no key. Both hosts were unreachable from the
  build environment, so the `/verify` and `/settle` shapes are unverified.
  They follow the x402 v1 facilitator specification.
- **Build the blocked-address list** from a current SDN.CSV. OFAC's host is unreachable from the build environment, so the file comes from the coordinator.
- **Owner decisions:** the sale switch, the base image pin, the container
  licence, and the trademark search.
