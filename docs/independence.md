# Independence policy (draft for the coordinator's review)

Status: draft, 3 November 2026, written by Adviser 1 at the owner's direction in Adviser 1's chat ("we want to be
an independent grader for GPUs and data centers"). It binds nothing until the coordinator records the owner's
confirmation from his own chat. One item, section 4, waits on an owner decision.

RigGrade grades GPUs, hosts and clusters from measurements, and later data centers from measured samples
(`docs/datacenter-grade-method.md`). A grade is only worth something if the graded party cannot buy it, steer it or
hide it. This policy writes down the rules that make that true, so that a buyer, a lender or a host can check them.

## 1. What "independent" means here

- The grade comes only from measurements taken under a published method, and anyone can recompute the verdict
  from the evidence pack.
- No graded party pays for an outcome, holds a stake in RigGrade, or can edit, delay or suppress a grade.
- The interests of the people behind RigGrade in anything it grades are disclosed (section 4).
- Wording stays qualified. RigGrade is run by a single operator with open rules, and its public pages say so
  wherever they use the word "independent" (as settled in riggrade PR 56). It is not an accredited laboratory or a
  certification body, and it never says it is.

## 2. Who pays, and what payment cannot buy

- **The price never depends on the outcome.** A grade, a certificate or a check costs the same whether it passes
  or fails. A request whose evidence does not recompute is refused and charges nothing (422).
- **Who paid is printed.** A host-paid grade carries the `host-paid` flag and the declared runner, as the grading
  spec already requires. A buyer-paid grade of someone else's rig says so.
- **Free runs grade the same way.** The launch window and the free trial change the price, never the method or
  the verdict.
- **No paid retest after a result.** An appeal is decided on the evidence already held (grading spec 10a).

## 3. No money from anyone we grade

RigGrade takes no money from a marketplace, host, data center operator, chipmaker or cloud for anything other
than the published products at their published prices. In particular, it takes:

- no referral, affiliate or listing fee;
- no equity, token allocation, grant or sponsorship tied to a graded party;
- no consulting or paid advice to a graded party about how to pass;
- no paid placement, ordering or badge styling.

A data licence (the exit plan's first money from a lender or data vendor) sells access to records that already
exist. A licensee cannot edit, delay or suppress a grade, cannot see a grade before the graded party does, and
cannot change a method. If a licensee is itself a graded party, that is disclosed on its grades.

Any exception would be an owner decision, published before it takes effect and disclosed on every affected grade.

## 4. Interests of the people behind RigGrade

The owner, the coordinator and the advisers disclose any holding in, or paid relationship with, a network,
marketplace or operator that RigGrade grades or writes about. The advisers (Adviser 1, the coordinator) hold
none.

**[Owner decision pending]** How the owner's own holdings in graded networks are handled. The options are:

1. **Disclose.** A line on every affected grade, report and ledger note: "The owner of RigGrade holds the token
   of this network."
2. **Blackout.** No trading in an affected token from [proposed: 7] days before to [proposed: 7] days after
   RigGrade publishes anything about that network. Never trading on RigGrade data that is not yet published.
3. **Abstain.** RigGrade does not grade or write about networks the owner holds.
4. **Divest.** The owner sells the overlapping holdings.

Adviser 1 recommends 1 and 2 together. The specific holdings are not written in this repository or on the board;
the disclosure line names only the network a grade is about.

## 5. Methods first

- Every rule a grade applies is published, versioned and in force before the run it applies to.
- A method changes only by a new version with its date and reason. It never changes for one case.
- Method changes go to the coordinator for review before they take effect.
- Thresholds, bars and weights are printed with the grade. There are no hidden weights.

## 6. Corrections, appeals and supersession

- The record is append-only (`docs/gpu-record-method.md` section 6). A wrong entry gets a correction beside it.
  Nothing is overwritten.
- A graded party may appeal within 30 days, decided on the evidence held (`docs/grading-spec.md` section 10a).
- A later grade or sample supersedes an earlier one. When the two differ, both are shown.
- A graded party cannot withdraw a completed grade or sample from the record.

## 7. What an independent grade still cannot say

- Nothing about a card, a box or a site that was not measured, beyond the published bound.
- Nothing about future delivery.
- Without attestation, a measurement cannot rule out a substituted card, and the grade says so.
- The site context lines (hazards, power price, grid, local actions) are public data with their sources, not
  RigGrade findings, and they never change a grade.

## 8. Review

This policy is reviewed with the exit plan each month, and any change follows section 5.
