# Cluster acceptance certificate: the offer in one page

**For:** anyone about to pay for a rented GPU cluster of 2 to 64 nodes and
wanting proof it delivers what was promised before the first invoice, a long
run or a handover; and hosts who want a third-party record on their side of a
dispute.

**What you get:** a signed certificate, as JSON and as a printable page, that
states what the provider claimed, what the cluster delivered during a named
test, and a verdict: accept, accept with exceptions, or reject. The checks and
their bars are public (`docs/cluster-acceptance.md`), the evidence pack is
yours, and anyone holding it can recompute every check with the open rules.
See `docs/sample-certificate.html` for what a certificate looks like.

**What it checks:** inventory against the claim (nodes, GPUs, model); that
every node ran the same pinned image; node health (the free preflight on
every node); stragglers against the cluster median; inter-node fabric
(NCCL all-reduce bus bandwidth against the claimed link per GPU); shared
storage read rate against the claim. A claim not made is not tested and is
shown as "no claim", never as met.

**How it runs:** about ten minutes on the cluster.
1. Write the provider's claims into a small file.
2. Run the pinned container on every node (`python3 -m runner.cluster node`).
3. Evaluate the node reports into an evidence pack (`runner.cluster evaluate`).
4. POST the pack to the service; pay the x402 requirement from your own
   wallet (USDC on Base, no account, no signup); receive the certificate
   and its lookup URL.

**Price:** 25 USDC per certificate as listed by the service (the operator
sets it; the descriptor at `/riggrade/v1` is authoritative). Nothing is
charged for a pack that does not recompute, and a free preview states the
verdict before payment.

**For a dispute:** open the lookup URL in a browser and print it to PDF. The
page carries the verdict, every check with its numbers, the evidence-pack
hash, and the service's signature check. Give the counterparty the pack and
the hash; they can recompute.

**Limits, stated plainly:** the signature is the service's own HMAC, not an
independently verifiable one. The verdict covers the named test at the named
time; it is not a promise about future delivery. The rules are v0 draft and
versioned; a change gets notice and a new version string on the certificate.

Contact: entropoid.research@gmail.com. Code and rules: Apache-2.0 at
https://github.com/nnyycc77-ship-it/riggrade.
