# Products beyond the point grade

Owner decision, 1 November 2026: two paid products built on data the free
tools already collect. Each is on sale only when the service's
`RIGGRADE_PRODUCTS` setting names it; the descriptor at `/riggrade/v1`
shows `products` with `on_sale` per product. Prices are USDC on Base,
paid by x402 from the buyer's own wallet, screened like every payer, and
every sale is appended to the hash-chained sale log.

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
    {"evidence_pack": { ...acceptance.json... }}

The server recomputes every check and the verdict from the claims and
node reports with the same published rules. A pack whose verdict does
not recompute is refused (422) and nothing is charged. The free answer is
the recomputed verdict and check statuses. The paid answer is a
certificate: the claims, the images every node ran, each check with its
numbers, the verdict, the pack's SHA-256, and an HMAC signature by the
server. The certificate is stored, and anyone holding its id can fetch it at
`GET /riggrade/v1/cluster/acceptance/{certificate_id}`, where the service
checks the signature against its own secret and stored record and answers
`signature_valid`. That is verification by the service, not independent
verification: the key is symmetric, so a copy of the certificate alone
cannot be checked without asking the service. Measured, not
promised: it states what the cluster delivered during that test against
what its provider claimed.

## Turning a product on

Add its name to `RIGGRADE_PRODUCTS` on the service (`fixit`, `cluster`,
comma-separated). With the sale switch on and the product named, the
descriptor shows it `on_sale: true`. Both default to off.
