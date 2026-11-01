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

RigGrade: listed on the Bazaar (every paid route settled once); x402 v2
exact on Base; request schema in the 402's bazaar extension; "Use when"
line on every listing since PR 52. **Ready.** No action.

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
