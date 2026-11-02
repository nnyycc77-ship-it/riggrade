# Fleet rails: where paying agents will appear first, and what RigGrade needs

Status: readiness note, 1 Nov 2026. Buyer-side payment plumbing is going
generally available before buyer budgets exist. When a fleet switches on a
per-call budget, it shows up as a step change in unique payers on one rail.
This note records what each rail needs from a seller and where RigGrade
stands. Nothing here changes a price, a rule or a listing.

## 1. AWS Bedrock AgentCore Payments (GA 18 Aug 2026)

Discovers paid tools through the Coinbase Bazaar MCP server and pays by
x402 (exact scheme on Base; "upto" also offered). What it needs from a
seller: a Bazaar-listed x402 v2 resource with a request schema and a
one-line reason to call.

RigGrade: the technical requirements are met (x402 v2 exact on Base; the
bazaar extension with a request schema in every paid 402; a "Use when"
line on every listing since PR 52), but **RigGrade is not in the Bazaar
catalogue**: a full read of the discovery list (about 59,700 resources,
2 Nov 2026 01:11 UTC) and the Bazaar search both returned no RigGrade
resource. The bazaar extension spec says cataloguing happens only when the
paying client echoes the 402's `bazaar` extension into its payment payload;
the one settlement so far was made by a client that evidently did not. From
PR 61 every sale record carries whether the client echoed the extension and
what the facilitator reported about cataloguing, so the next sale shows the
cause. A listing therefore needs one paid call from a client that echoes
the extension (the current official x402 clients do); making that call is
an owner step, since it is a payment. **Not listed; cause traced to the
client side; the next echoing sale lists it.**

## 2. Cloudflare Monetization Gateway (closed beta since 30 Sep 2026)

Charges agents per request for APIs, MCP tools and datasets over x402,
USDC on Base, for US-based sellers and buyers. What it needs: an
application by the account holder, the origin behind Cloudflare, and a
price per route.

RigGrade: the service answers x402 natively, so the gateway would be a
second front door, not a replacement. **Owner step:** the beta application
is made from the owner's own Cloudflare account; residency rules apply.

## 3. Stripe Machine Payments Protocol (mainnet 18 Mar 2026)

A separate protocol (Tempo chain, USDC), with its own 402 flavour and a
services directory. Not x402-compatible on the wire.

RigGrade: not supported. Adding it means a second payment adapter and a
Stripe account in the owner's name. **Deferred** until a buyer on that rail
asks; recorded so the decision is visible.

## 4. The "upto" scheme (metered calls)

x402 v2 lets a payer authorise up to an amount and the seller settle the
amount used. The natural RigGrade use is a metered preflight comparison
for fleets: free for single calls, settled per call above a daily number.

RigGrade: the free preflight runs on the renter's own box and makes no
server call, so there is nothing to meter yet. The metered product would
be a server-side comparison against pooled preflight results, which waits
on the owner's data-retention decision. **Deferred** until that product
exists; building the scheme first would be payment code with no caller.

## 5. What to watch

The daily digest's Buyers section (repeat, one-shot, sweep) and the
challenges-against-settlements line. A jump in new payers that settle on
the first call, from a user agent naming one of the rails above, is the
signal that a fleet switched on.
