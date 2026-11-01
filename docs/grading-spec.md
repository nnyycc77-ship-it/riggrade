# RigGrade grading spec, v0.1

Status: v0.1. It folds in the coordinator's review of v0 (board Messages 143b and 146) and the corrections found while building the engine. It implements Revision 5 of the GPU quality grading plan (31 October 2026). Where this spec has to choose something the plan left open, the choice is marked **[proposed]** and listed again in section 15, so the reviewer can accept or change it in one place. Numbers marked **[proposed]** are design choices, not measured facts.

Nothing here switches a sale on. The endpoint ships with the sale off, and going live is an owner-gated step.

## 1. Terms

- **Rig.** One host machine and the GPUs attached to it, graded as one unit (plan Decision 5).
- **Slice.** Part of a rig sold as its own unit, for example 2 cards of 8. A grade attaches to the pair (rig, sharing configuration as tested) and never transfers between a whole rig and a slice.
- **Peer class.** The hardware as bought: chip model, form factor and memory variant. Power limits, cooling and site are what the grade measures, so they never split a class.
- **Workload class.** The kind of work graded. v0 has two: `inference` and `training`.
- **Point grade.** A grade from one paid session: gate, speed and consistency. Reliability is printed as "not measured (point grade)".
- **Full grade.** A grade that also covers reliability, from the host subscription's heartbeat record over the trailing 30 days.
- **Runner.** Whoever runs the container: a buyer, a host, or an agent for one of them. The runner is always recorded as *declared*, never as verified.
- **Session.** One run of the container, bound to one nonce.

## 2. Peer classes, v0

| Class id | Chip | Form | Memory | Price tier |
|---|---|---|---|---|
| `h200-sxm-141` | H200 | SXM | 141 GB HBM3e | data centre |
| `h100-sxm-80` | H100 | SXM | 80 GB HBM3 | data centre |
| `h100-pcie-80` | H100 | PCIe | 80 GB HBM2e | data centre |
| `a100-sxm-80` | A100 | SXM | 80 GB HBM2e | data centre |
| `a100-pcie-80` | A100 | PCIe | 80 GB HBM2e | data centre |
| `a100-pcie-40` | A100 | PCIe | 40 GB HBM2 | data centre |
| `l40s-48` | L40S | PCIe | 48 GB GDDR6 | data centre |
| `rtx5090-32` | RTX 5090 | PCIe | 32 GB GDDR7 | consumer |
| `rtx4090-24` | RTX 4090 | PCIe | 24 GB GDDR6X | consumer |
| `rtx3090-24` | RTX 3090 | PCIe | 24 GB GDDR6X | consumer |

The container identifies the class from the driver's reported device name and memory size. If the reported identity does not match a class, the session is graded `unrated` with reason `unknown_class`. A new class is added by spec version, with its own reference and bars; existing classes are never touched by that.

The price tier sets the per-call and subscription price (plan, decided items 1). It has no effect on how the grade is computed.

## 3. Workload rulers, v0 [proposed]

Fixed open workloads, so every rig in a class runs the same work under the same pinned image.

- **Inference ruler.** Qwen2.5-7B-Instruct (Apache-2.0), BF16 weights, served by vLLM under the pinned image, on one card. The request mix is drawn from the session parameters (section 6): prompt lengths, output lengths and concurrency.
  - Measures: output tokens per second at the drawn concurrency, time to first token, p50 and p99 per-token latency.
- **Training ruler.** A fixed GPT-2 124M configuration (nanoGPT-style, MIT licence), trained on synthetic random tokens, so no dataset is downloaded and no data licence is involved. One card, plus a data-parallel run across every card in the rig or slice when it has more than one.
  - Measures: training tokens per second per card. Model FLOPs utilisation is computed and printed as information (section 5.1).

Both rulers are deliberately small, so a session fits a short rental and the weekly cadence. Larger rulers can arrive as a later benchmark version.

## 4. Capability gate

The gate is pass or fail per workload class. Speed only counts for a class whose gate passed. A failed gate grades that class `unrated` with the failing check named.

| Check | Inference | Training |
|---|---|---|
| Per-card memory | at least 20 GB free after driver load | at least 12 GB free |
| Memory integrity | device memory test over 90% of free memory, zero errors | same |
| Matrix multiply | BF16 GEMM runs and returns correct results | same |
| Memory bandwidth | device-to-device copy completes | same |
| Card-to-card link (more than 1 card) | not required | NCCL all-reduce completes on every card |
| Host link | PCIe host-to-device copy completes | same |

The gate checks that the work can run. How fast it runs belongs to the speed pillar. Micro-benchmark numbers (GEMM throughput, memory bandwidth, all-reduce bus bandwidth, host link bandwidth) go into the evidence pack, and they are what the spoofed-model check in section 8.3 reads.

## 5. Pillars, metrics and bars

The grade for a workload class is the lowest pillar grade among the pillars measured (plan Decision 7). The label names the pillar that capped it. There is no averaging. A point grade has no reliability pillar.

All bars below are **[proposed] v0 values** for review. Once a benchmark version is published, its bars are fixed for that version (plan Decision 8).

### 5.1 Speed

`speed_ratio = measured ruler throughput / class reference throughput`, for the same workload class and the same benchmark version. Training uses per-card throughput, so rigs of different sizes in one class are comparable.

| Grade | To be awarded | To be kept while already held |
|---|---|---|
| Grade 1 | at least 0.95 | at least 0.92 |
| Grade 2 | at least 0.85 | at least 0.82 |
| Grade 3 | gate passed, below Grade 2 | gate passed |

The gap between the award bar and the keep bar is the buffer that stops weekly bouncing. A grade that falls below its keep bar goes on watch (section 10).

Model FLOPs utilisation for training is computed against the vendor's published peak for the class and printed for information only. It never sets a bar, because vendor peaks are marketing numbers (plan Decision 8).

### 5.2 Consistency

Three measures. The pillar takes the worst of the three, and inference adds the third.

| Measure | Grade 1 | Grade 2 | Otherwise |
|---|---|---|---|
| Coefficient of variation of ruler throughput across benchmark passes | at most 3% | at most 7% | Grade 3 |
| Throughput decay over a sustained 60-minute run (first 5 minutes against last 5) | at most 5% | at most 10% | Grade 3 |
| p99 / p50 per-token latency (inference only) | at most 2.0 | at most 3.0 | Grade 3 |

For a slice, the controlled noisy-neighbour isolation score (section 9) counts as a fourth measure: a throughput drop under the published aggressor load of at most 5% for Grade 1 and at most 12% for Grade 2.

### 5.3 Reliability (full grades only)

Measured from the heartbeat chain (section 7) over the trailing 30 days, stated as allowed downtime per 30 days.

| Grade | Allowed downtime per 30 days |
|---|---|
| Grade 1 | at most 3.6 hours (99.5% up) |
| Grade 2 | at most 14.4 hours (98% up) |
| Grade 3 | more than 14.4 hours |

Where a marketplace exposes availability data through an interface, it is recorded as a cross-check. If the cross-check and the heartbeat disagree by more than 2 hours in the window, the pillar is held at the lower of the two, and the disagreement is printed in the evidence pack.

### 5.4 Minimum evidence

- **Point grade.** One session holding at least 3 timed passes of the ruler, plus the 60-minute sustained run. The CV is computed across those passes. The label carries `"evidence_basis": "single session"`, and the badge reads "point grade, single session". The plan's '3 benchmark runs on separate days' rule applies to full grades only; this was accepted in review (Message 143b).
- **Full grade.** At least 3 sessions on separate days within the trailing 7 days for speed and consistency. A heartbeat chain covering at least 25 of the trailing 30 days for reliability, with the not-yet-covered days counted as down. Below either minimum, that pillar prints Insufficient data.

### 5.5 Top-grade rule

Grade 1 in any pillar becomes the overall Grade 1 only if the session evidence is **attested** (section 8.2), or one of our own re-checks matches it within tolerance in the same window. Re-checks run only on access the provider or host grants under paid work (plan, discussion decision 9). The matching tolerance is ±3% on the speed ratio and the same consistency grade. Any other evidence caps the overall grade at Grade 2, whoever declares they ran it.

## 6. Session protocol

All endpoints are under `/riggrade/v1/`. Every request and response is JSON.

1. **Nonce.** `POST /riggrade/v1/session` with `{workload_classes: [...], declared_runner: "buyer"|"host", host_wallet?: "0x..."}`. This returns `{session_id, nonce, expires_at}`. Issuing a nonce is free. Here `expires_at` is the completion window: **[proposed]** 3 hours from issue.
2. **Parameters at run start.** The container calls `POST /riggrade/v1/session/{id}/start` and receives the run parameters: batch sizes, prompt and output lengths, concurrency, pass order and the aggressor schedule for slices. Parameters are drawn from the published distribution (`spec/param-distribution.json`) each field drawn with `HMAC-SHA256(server_secret, nonce || field)`. They are revealed only at this call, so they cannot be tuned for in advance. The draw can be reproduced by anyone holding the secret, which lets an appeal re-derive it.
3. **Run.** Gate, one unmeasured warm-up pass per workload, the timed ruler passes, the sustained run, and the slice tests where they apply, all under the pinned image. The warm-up pass is kept in the raw outputs and never enters the CV. Attestation is captured where available, with the session nonce as the attestation nonce.
4. **Submit.** `POST /riggrade/v1/session/{id}/bundle` with the result bundle (section 11). The endpoint checks the nonce, the window, the image digest and the parameter echo. A bundle that fails any check is refused, and nothing is charged.
5. **Pay and grade.** The grade is returned behind x402. The payment adapter verifies the payment, screens the payer under the program's compliance rules and only then settles, and every sale is appended to the sale log. An unpaid request receives 402 with the price for the classes requested and a preview: gate pass or fail, and the count of pillars measured. It never shows a grade. Where a class has no reference yet (section 13), the preview carries the notice "reference-building session, speed not graded" before any payment. A session with an unknown class, or with no class that passed its gate, is not sold at all: the preview already says so, and a grade that can only read unrated is refused with nothing charged. After payment, the response is the label and the evidence pack, and the sale record is appended to the hash-chained sale log, following the shelf's existing pattern.
6. **Publication.** A grade from a declared buyer run is private to the paying wallet. It is published only when the host's registered wallet signs a publish request (plan, discussion decision 3).

## 7. Heartbeat (subscription)

- The subscription container calls `POST /riggrade/v1/heartbeat` every 5 minutes with `{rig_id, prev, sig}`. Here `prev` is the hash returned by the previous call, and the endpoint returns the next hash and a short-lived challenge, which the next call must echo.
- **Downtime.** A gap longer than 10 minutes between accepted heartbeats counts as down for its whole length minus 5 minutes. A broken chain, a wrong `prev` or a failed echo, restarts the chain and counts as down until the next accepted heartbeat.
- The chain is tagged with the declared runner and the attestation state, like any other evidence. The heartbeat proves only that something answered on schedule; it does not prove the rig was serving work. That limit is printed with the reliability pillar.

## 8. Evidence tags and integrity

### 8.1 Tag format

`declared:<buyer|host>` + `attestation:<attested|unattested>` + optionally `host-paid` + optionally `provisional`.

Examples:
- `declared:buyer attestation:unattested provisional`
- `declared:host attestation:attested host-paid`

### 8.2 Attestation

On hardware that supports confidential computing attestation (H100 class and newer), the container collects GPU attestation evidence with the session nonce, and the endpoint verifies it. Most marketplace rentals will not have confidential computing mode on, and consumer cards have no attestation at all, so most sessions are expected to be unattested at launch. That is accepted under plan discussion decision 2.

### 8.3 Spoofed-model check

The micro-benchmarks are compared with the class reference envelope: memory size, memory bandwidth and GEMM throughput. A session whose identity claims one class but measures outside that class's envelope by more than 25% on any of the three is graded `withdrawn` with reason `identity_mismatch`. It is still charged if paid, because the fee is for measurement (plan, fee honesty), and the reason is printed.

## 9. Slices

The label records:
- `slice`: cards in the slice and cards in the rig, for example `2/8`;
- `isolation`: `passthrough`, `mig`, `vgpu` or `time_sliced`;
- `interconnect_in_slice`: `nvlink`, `pcie` or `none`;
- `cpu_cores`, `host_memory_gb`, `network_gbps`, as allocated;
- `neighbour_condition`: `idle`, `controlled` or `uncontrolled`.

The controlled isolation test runs only where the host grants access to two slices of the same rig under paid work, or where the fee for that specific check has already been received and covers both slices. It never runs on a rental made ahead of revenue (plan boundary: no owner capital at risk). One slice runs the ruler while the other runs the published aggressor workload, which loads memory bandwidth, the card link, the host network and the CPU. The isolation score is the ruler's throughput drop against an idle-neighbour pass.

Where only uncontrolled conditions are available, the consistency pillar uses the field passes, labelled `neighbours uncontrolled`. Where neither method can run, consistency prints Insufficient data for that slice configuration.

## 10. Lifecycle

States: `assigned`, `affirmed`, `on_watch`, `demoted`, `expired`, `withdrawn`.

- `assigned`: first grade from a session.
- `affirmed`: a full grade refreshed in a week with all pillars at or above their keep bars.
- `on_watch`: the first week below a keep bar. Watch prints on the label.
- `demoted`: a second consecutive week below. The grade lands on what the numbers earn, not one level down as a courtesy.
- `expired`: no refresh in 7 days for speed and consistency.
- `withdrawn`: integrity failure, identity mismatch, a refused re-check, or a lost evidence pack. It is immediate, with no watch period.

A material change to the tested configuration (a card swapped, the link changed, the rig moved) ends the grade for the old configuration. A published grade stays public through every state, in the same record.

## 10a. Appeals

Plan Decision 15, restated for payers and in the endpoint descriptor. A host may appeal within 30 days of a grade. The appeal is decided on the evidence already held, with no paid retest. A proven error is corrected by appending a correction to the record, never by silently editing the original grade. A buyer complaint triggers a review, and does not by itself put a grade on watch.

## 11. Result bundle and evidence pack

The result bundle (from the container) holds:
- `session_id`, `nonce`, `bench_version`, `image_digest`;
- the echoed parameters;
- the declared identity: device names, memory sizes, driver and CUDA versions, PCI topology;
- the raw outputs of every gate check, micro-benchmark and ruler pass, with timestamps;
- the attestation evidence where present;
- `sha256` of each raw output file.

The evidence pack (from the endpoint), kept for 24 months, holds:
- everything in the bundle;
- the class and the bar set applied, with `bench_version`;
- the pillar grades, the cap and the overall grade;
- the evidence tag, the declared runner and the paying wallet;
- for full grades, the heartbeat chain summary and any marketplace cross-check;
- the status history and any appended corrections, with the original entries left visible.

## 12. Label and badge

The machine-readable label:

```json
{
  "product": "RigGrade",
  "bench_version": "v0.1",
  "rig_id": "rg_...",
  "peer_class": "rtx4090-24",
  "workload_class": "inference",
  "grade": 2,
  "form": "point",
  "evidence_basis": "single session",
  "capped_by": "consistency",
  "pillars": {"speed": 1, "consistency": 2, "reliability": "not measured (point grade)"},
  "status": "assigned",
  "evidence_tag": "declared:buyer attestation:unattested provisional",
  "slice": null,
  "window": {"from": "2026-11-02T10:00:00Z", "to": "2026-11-02T11:40:00Z"},
  "statement": "Measured, not promised. A label of measured quality under the named test and window; it makes no promise about future delivery."
}
```

The text form, for the badge:

> Grade 2 · RTX 4090 class · Inference · point grade, single session · capped by Consistency · declared buyer run, unattested · provisional bars · 2 Nov 2026

A public badge is a live link to the current record, never a static image, and the badge page links to the aggregate counts (plan, discussion decision 5). Promise words ("guaranteed", "guarantee", "certified", "certification", "warranty", "safe") must never appear in label, badge or listing text. A lint test in the endpoint package will enforce this.

## 13. Provisional bars at launch

Class references need runs, and the capital boundary rules out rentals ahead of revenue. So v0 launches with **provisional references**: for each class, the median of the ruler throughputs from the first paid sessions in that class. Until at least 5 distinct rigs stand behind it, no speed grade is issued in that class, and speed prints Insufficient data. This replaces the plan's "published results for the open benchmark", which cannot exist for our own pinned image and rulers; accepted in review (section 15, item 2).

- **Before payment.** While a class has fewer than 5 rigs behind its reference, the preview tells the payer "reference-building session, speed not graded". The price does not change, because prices are decided items (Message 146).
- **Published basis.** The endpoint descriptor and every evidence pack publish, per class, the number of rigs behind the reference, how many are attested, and the basis used.
- **Steering defences.** At most one session per rig identity counts. Once 5 attested rigs exist, the median uses attested rigs only. Controlled reference runs replace the provisional median as soon as granted access exists, without waiting for a fixed count.

Every grade under provisional references carries the `provisional` tag. References switch to controlled reference runs, taken on access granted under paid work, through the 4-week version overlap (plan Decision 14).

## 14. Versioning

- The `bench_version` covers the pinned image digest (driver, CUDA and framework versions), the rulers, the parameter distribution, the gate checks and the bar set.
- Any change to any of them is a new version.
- Bars never change within a version.
- A new version is announced with a fixed start date and cutover date, and runs a 4-week overlap with both grades shown. During the overlap the old version governs watch and demotion.

## 15. Open items for review

1. **Point-grade evidence.** Accepted in review (Message 143b).
2. **Provisional references.** Accepted in review (Message 143b), with the disclosure, publication and steering conditions now written into section 13. This is an agreed amendment to plan discussion decision 11, for the next plan revision.
3. **v0 bars.** All values in section 5 are proposals: speed 0.95/0.92 and 0.85/0.82; CV 3%/7%; decay 5%/10%; p99/p50 2.0/3.0; isolation 5%/12%; downtime 3.6 h/14.4 h.
4. **Rulers.** Qwen2.5-7B-Instruct on vLLM for inference, and GPT-2 124M on synthetic tokens for training (section 3). The inference gate's 20 GB free minimum leaves 16 GB cards unrated for inference in v0. (v0 said 24 GB free, which no 24 GB card can show after driver load; corrected while building the engine.)
5. **Completion window.** 3 hours from nonce issue (section 6).
6. **Identity mismatch threshold.** 25% outside the class envelope (section 8.3).
7. **Licence for the open container.** Not chosen yet. The owner decides it before step 2, since the container is published then.
8. **Reliability cross-check rule.** A disagreement of more than 2 hours between the heartbeat and the marketplace data holds the pillar at the lower of the two (section 5.3).
9. **Heartbeat minimum.** A chain covering at least 25 of the trailing 30 days (section 5.4).
10. **Pre-payment preview.** Gate result, pillar count and the reference-building notice, never a grade (section 6).
