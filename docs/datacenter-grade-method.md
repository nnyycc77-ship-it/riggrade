# Data center grade method (draft v0, for the coordinator's review)

Status: draft, 3 November 2026, written by Adviser 1 at the owner's direction in Adviser 1's chat. Nothing is
built, nothing is published, and no route serves it. Per the exit plan, the first site sample is Phase 2's work
(February to April 2027). This draft binds nothing until the coordinator records the owner's confirmation from his
own chat. It follows `docs/independence.md`.

## 1. The principle

RigGrade grades a data center only by measuring the GPUs inside it. A site's grade is the grade of a sample of its
cards, drawn by the service, with its statistical bound printed. Public data about the site's surroundings (hazards,
power price, grid outages, local actions) is printed beside the grade as context and never changes it. RigGrade
does not grade a facility from public records, press reports or an operator's own claims.

## 2. What is graded

- **The unit is a declared site:** a facility an operator or host declares, with its county FIPS code, and
  optionally a facility name the operator chooses to show.
- **Per peer class, never pooled.** A site with two GPU classes gets two grades, as for site samples in the GPU
  record method.
- **Per workload class.** The grading spec's workload classes, `inference` and `training` in v0.

## 3. The sample

The sampling rule is the one already written in `docs/gpu-record-method.md` section 4, unchanged:

- the operator declares the inventory per box and per peer class first, and the declaration is bound to the
  draw before the session nonce is issued;
- the nonce selects the cards, so the operator never picks them;
- fewer than 10 sampled cards is "insufficient sample" and makes no site claim;
- the claim is the pass share with its one-sided 95% lower bound (exact binomial), beside the sample size and
  the declared inventory, for example "12 of 12 sampled cards passed; at least 78% of the declared 96 pass at 95%
  confidence";
- a shrinking inventory, or a card that does not match its slot, voids the entry;
- the attestation state is printed, and unattested cards carry the substitution caveat.

## 4. The grade (v0 draft, for review)

Each sampled card is graded under the grading spec, with the lowest pillar capping the grade. The site grade for a
peer class and workload class is then:

1. **The sample's median card grade.** This is a median over sampled cards, never an average of pillar scores.
   With an even number of sampled cards, the median is the worse of the two middle grades (fail closed).
2. **Capped by the pass bound:**
   - lower bound at least 90%: the median grade stands;
   - lower bound at least 75% and below 90%: one grade lower, and Grade 3 stays Grade 3;
   - lower bound below 75%: no site grade; the entry prints the pass share and the bound only.
3. **Printed with it:** the sample's median and range for speed ratio and consistency, never extrapolated to
   unsampled cards; the declared runner; who paid; the attestation state; the method version.

A worked example: 20 cards sampled, 19 passed, so the bound is 78%. That is the one-failure figure for 20 cards in
the record method. The median card is Grade 2, so the site grade is Grade 3 for that class, and the entry says why.

These bands are draft values for review, published per method version as the grading spec's bars are.

What the bands mean in practice (coordinator review, Message 308): with every sampled card passing, the bound is 74%
for 10 cards, so a 10-card sample can never give a site grade. A capped grade needs at least 11 cards, all passing
(bound 76%). An uncapped median grade needs at least 29 cards, all passing (bound 90%). Any failure raises those
minimums. These effective minimums are intended for v0; if they prove too strict, the bands or the minimum sample
change by a new method version before any site is graded, never for one site.

## 5. Context beside the grade

Each site entry prints the site context page's sections for the declared county, from
`docs/site-risk-method.md`: physical hazards (FEMA), energy (EIA, state level), grid reliability (EIA-861, the
utilities named for the county), local and political actions (hand-kept primary reads), and the country section
where relevant. Each line keeps its source, its grain and its scope wording. None of it enters the grade, and the
page says so in one line above the context.

## 6. Over time

- **Repeat samples.** A site keeps its grade only with a fresh sample at least every [proposed: 90] days. Past
  that, the entry prints "sample older than 90 days" and shows no current grade.
- **Supersession.** A new sample supersedes the last, and both are shown when they differ. Nothing is deleted.
- **No withdrawal.** An operator cannot remove a completed sample from the record, including a failed one.

## 7. Who may request a site grade

- **The operator or host,** on its own boxes. The entry carries `host-paid` and "declared runner: host".
- **A third party, such as a lender or a buyer,** only with the operator's consent to run on its boxes. The
  operator still declares the inventory, and the service still draws the sample. The entry names who paid.
- Either way, the price does not depend on the outcome, and the method is the same (`docs/independence.md`
  section 2).

- **A hall with several operators** (a colocation hall with tenants) is graded per operator and per facility,
  never pooled across operators. A hall-level page carries context only, and no grade. (Message 308.)
- **A third-party sample needs written operator consent,** reviewed before any such run, covering the inventory
  declaration, the consent to run, publication of the result including a failure, and no withdrawal.

## 8. What a site grade does not say

- Nothing about cards not sampled beyond the bound, and nothing about future delivery.
- Nothing about the site's ownership, its power contracts, its finances or its legal standing.
- It does not verify the declared location. The county is the operator's declaration, and every site entry prints
  "location declared by the operator, not verified" in the entry itself, not only in this method, until a location
  check exists. No site entry is public before that line is on it.
- It is not an accreditation, and it is not a rating of the operator as a company.

## 9. Open questions for the coordinator

1. The pass-bound bands in section 4 (90% and 75%), and whether the cap should be one grade or more.
2. The repeat-sample interval in section 6.
3. Whether a third-party-paid sample (section 7) needs a written consent text from the operator, and what it says.
4. Whether a location check is needed before any site entry is public. One option is a declared address matched
   against network latency from the boxes, but that is unproven and would need its own method.
5. How a site entry treats a facility with several operators, for example a colocation hall with tenants.

Answered by the coordinator in Message 308: the bands and the 90-day interval are accepted for v0 with their
effective minimums printed (section 4); a third-party sample needs a written consent text (section 7); the location
line goes in the entry (section 8); multi-operator halls are graded per operator and per facility (section 7). The
consent text itself is still to be written and reviewed.

## 10. Build, when Phase 2 comes

Nothing is built before an operator declares a site. Then: a site declaration route, the inventory binding and
nonce draw (the record method already specifies them), the site grade computation, and the site entry page with the
context sections. Roughly a week, reusing the grading engine, the record and the site layer.
