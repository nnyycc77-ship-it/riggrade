# Products beyond the point grade

Decided 1 November 2026: two paid products built on data the free
tools already collect. Each is on sale only when the service's
`RIGGRADE_PRODUCTS` setting names it; the descriptor at `/riggrade/v1`
shows `products` with `on_sale` per product. Prices are USDC on Base,
paid by x402 from the buyer's own wallet, screened like every payer, and
every sale is appended to the hash-chained sale log.

## Paying from a browser

People without an x402 client can buy the fix-it report and the cluster
certificate at `GET /riggrade/v1/checkout`. The page posts the file to the
same paid route, reads the 402 challenge (price, payout address, preview),
asks the person's own browser wallet to sign an EIP-3009 USDC authorisation
for exactly that amount on Base, and posts again with the signed x402 v2
payload. Prices, screening, settlement and the sale log are the route's own;
the page holds no price, no address and no key, and loads no outside script.

## Rental check (free)

Before renting, `POST /riggrade/v1/rental/plan` with `{"workload": "inference"|"training", "needs": [...],
"gpus": {"peer_class": ...}}`. It returns what to require for that workload, which preflight check confirms each
requirement, and the keep rules. It names no marketplace, host or price. After renting, run `riggrade-preflight`
and `POST /riggrade/v1/rental/check` with `{"preflight_report": ..., "plan": ...}`. The answer is keep, keep with
warnings or cancel, with the measured reasons and each card's record history. That answer is advice measured at
that moment; cancelling and refunds sit under the marketplace's own terms. Free, with nothing stored
(`docs/rental-check-design.md`).

## Host fix-it report, 2 USDC

For a host or renter whose free preflight warned or failed. Send the
`preflight-report.json` the check wrote:

    POST /riggrade/v1/fixit
    {"preflight_report": { ...the report... }}

The free answer is a preview: how many findings the report would address
and which checks. The paid answer names, for each warn or fail finding,
the likely cause (for example "the host lowered the power limit to 150 W,
below the card's default 250 W"), the fix steps, who can act (host or
renter), the confidence, and the grade pillar it threatens. A report
where every finding passed is not sold (422). The causes are inferred
from the measurements; the fixes are the usual ones for each cause, and
the report says so.

## Cluster acceptance certificate, 25 USDC

For a renter of a multi-node cluster. Run the acceptance test
(`docs/cluster-acceptance.md`) on the nodes, then send the evidence pack
it wrote:

    POST /riggrade/v1/cluster/acceptance
    {"evidence_pack": { ...acceptance.json... }, "payer_role": "buyer"}

`payer_role` is optional: `buyer`, `provider`, `lender`, `insurer`, `other` or
`undeclared` (the default). The certificate prints the paying wallet and this
role, marked as the payer's own declaration and not verified. The price and the
rules are the same whoever pays, so a provider may certify its own cluster and
the certificate says so (`docs/independence.md` section 2).

The server recomputes every check and the verdict from the claims and
node reports with the same published rules. A pack whose verdict does
not recompute is refused (422) and nothing is charged. The free answer is
the recomputed verdict and check statuses. The paid answer is a
certificate: the claims, the images every node ran, each check with its
numbers, the verdict, the pack's SHA-256, an Ed25519 signature verifiable
offline with the public key at `/.well-known/riggrade-keys.json`
(`pip install riggrade`, then `riggrade-verify certificate.json`), and the server's own HMAC
check. The certificate is stored, and anyone holding its id can fetch it at
`GET /riggrade/v1/cluster/acceptance/{certificate_id}`, where the service
reports both checks and stored record and answers
`signature_valid`. That is verification by the service, not independent
verification: the key is symmetric, so a copy of the certificate alone
cannot be checked without asking the service. Measured, not
promised: it states what the cluster delivered during that test against
what its provider claimed.

## Price check, 0.01 USDC

Price set by the owner directly on 3 November 2026. An agent holding a
quote sends `POST /riggrade/v1/price/check` with
`{"peer_class": "h100-sxm-80", "usd_per_gpu_hour": 2.4}` and gets back,
for the latest listed week of that class in the free price index:

- the median, provider median, minimum and maximum, with provider and
  offering counts;
- where the price sits: below the listed minimum, between the minimum
  and the median, at the median (within 5%), between the median and the
  maximum, or above the listed maximum;
- its gap to the median in percent, and the last eight weekly medians.

The figures are the ones `GET /riggrade/v1/index` serves free; the call
only does the placing. It opens with the posture lines: information, not
advice, an offer, a rate or a forecast; listed asks, not paid prices; a
price says nothing about a box's health. It names no host or marketplace
and ranks no offers. A class with no listed week is refused with 422
before any charge; bad input is refused with 400 and never charged.

The route enters the OpenAPI document, the agent card and llms.txt only
while the product is on sale, so crawlers never probe it while it answers
503. Every sale is logged as `riggrade_price_check` with the class.

## Turning a product on

Add its name to `RIGGRADE_PRODUCTS` on the service (`fixit`, `cluster`,
`pricecheck`, comma-separated). With the sale switch on and the product named, the
descriptor shows it `on_sale: true`. Both default to off.
