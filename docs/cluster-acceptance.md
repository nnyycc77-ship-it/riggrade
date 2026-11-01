# Cluster acceptance test: spec v0 (draft)

Status: v0 draft rules, on sale since 1 Nov 2026 as the cluster acceptance
certificate (25 USDC; see docs/offer-cluster-acceptance.md). Built and tested
on the CPU suite; the first run on a real cluster is still to come, so the
bars below are draft and versioned.

## Why

The largest GPU buyers pay for capacity only once it is "delivered to and
accepted by us", under a written acceptance testing plan (Anthropic S-1,
2026). A startup renting 8 to 64 GPUs a month from a smaller provider has no
such plan and no lawyers to write one. This test is that plan: run it
before the first payment, keep the evidence pack, and decide whether to
accept.

## What it checks against

Two things, so it needs no reference data and works from the first run:

1. **The provider's own claims.** The renter writes them down before the
   run, in a claims file:

       {"gpu_model": "NVIDIA H100 80GB HBM3", "gpus_per_node": 8, "nodes": 4,
        "inter_node_gbps_per_gpu": 400, "storage_read_gbs": 10}

   - `inter_node_gbps_per_gpu` is the nominal network link per GPU, in Gb/s.
     400 means 400G InfiniBand or RoCE per GPU.
   - `storage_read_gbs` is the claimed aggregate read rate from shared
     storage, in GB/s.
   - Any field can be left out. A claim that is not made is not tested; it
     is reported as "no claim", never as "met".
2. **The cluster against itself.** Synchronous training runs at the speed
   of the slowest GPU. Every node is compared with the cluster median, so
   one slow node shows up even when every node passes on its own.

## Checks and thresholds

| Check | Measured | Accept | Exception | Reject |
|---|---|---|---|---|
| Inventory | nodes and GPUs that answered, and the GPU model reported | matches the claims | | fewer nodes or GPUs than claimed, or a different model |
| Image | the container image each node ran (`RIGGRADE_IMAGE_DIGEST`) | every node the same pinned image | an unpinned image | nodes ran different images |
| Node health | the preflight check on every node | every node passes | a node warns | a node fails |
| Straggler | each node's GEMM TFLOPS against the cluster median | all at least 90% | a node at 80 to 90% | a node below 80% |
| Inter-node fabric | NCCL all-reduce bus bandwidth across all nodes, against the claimed link per GPU | at least 80% of claim | 60 to 80% | below 60% |
| Storage | aggregate read rate from shared storage, against the claim | at least 80% of claim | 60 to 80% | below 60% |

The overall verdict is the worst result across the checks: **accept**,
**accept with exceptions** (the evidence lists them) or **reject**. A check
that could not run is shown as not measured, never as passed. If a check
claimed by the provider was not measured, the verdict is at best accept
with exceptions.

The 80% fabric bar allows for protocol overhead, since NCCL bus bandwidth
over a healthy link typically lands well above it. It is published here so
anyone can recheck a verdict by hand.

## How it runs

1. Every node runs the same pinned container:

       python3 -m runner.cluster node --rank R --nodes N --master HOST:PORT --out node-R.json

   Each node runs the preflight probes and a GEMM measurement. All nodes
   then join one NCCL all-reduce across the cluster, which measures the
   fabric.
2. When a shared storage path is given (`--storage PATH`), every node times
   reads from it straight after the fabric test, at about the same moment.
   The evaluator adds the per-node rates together to get the aggregate.
   Each file is read once, so the page cache cannot flatter a second read.
   The start is not synchronised, so the aggregate is approximate. The
   evidence pack says so next to the figure.
3. Any machine then joins the node reports and the claims:

       python3 -m runner.cluster evaluate --claims claims.json node-*.json --out acceptance.json

## Evidence pack

`acceptance.json` holds:
- the claims as given;
- the container image every node ran, so a verdict ties to the exact code
  behind it;
- each node's report;
- the fabric and storage measurements;
- each check's result with the numbers behind it;
- the verdict;
- the method version.

Every input file is recorded with its SHA-256, so the pack can be checked
later. Nothing is sent anywhere unless the renter chooses to share it.

## What it is not

It is not a grade, it compares against no peer class, and it makes no
statement about the future. It shows what one cluster delivered during one
test, against what its provider said it would deliver.
