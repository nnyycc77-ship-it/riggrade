# RigGrade GPU record: method v0 (draft, for review)

Status: accepted with named conditions by the coordinator on 2 Nov 2026 (board
Message 277); the conditions are written in below, marked **[C1]** to **[C6]**.
No record is kept or served until the switch is set after the coordinator's
confirmation read of this text. Owner direction 2 Nov 2026, on Adviser 1's side: grow the record
without sales, on free compute and through hosts running the free tool; no
spend; distribution held.

## 1. What it is

A per-card history of what a GPU delivered when it was measured: the
RigGrade preflight and grade results, keyed to the card, dated, with the
evidence tag of each run. The car trade has the VIN and the history report;
GPUs have neither. NVIDIA holds the strongest identity (a per-device
certificate on Hopper-class cards, used by confidential-computing
attestation) and no public history exists for any card. This record is the
history, built on identities the driver exposes, and on NVIDIA's attestation
where a host has it on. It never competes with NVIDIA on identity.

## 2. Identity, and what is written

- `card_id`: the SHA-256 of the driver's GPU UUID, first 16 hex digits,
  prefixed `gc_`. The UUID itself is never written anywhere.
- `serial`: the board serial as `nvidia-smi` reports it, where the driver
  exposes it (data-centre cards; GeForce cards usually report N/A). Written
  only in the host's own report and, with consent (section 3), in the record.
- `rig_id`: the existing hash of the sorted UUIDs of the box.
- The evidence tag of every run: `declared:<runner> attestation:<attested|unattested>`.
  A record built from unattested runs says so on every line; identity without
  attestation is readable, not provable. A card seen in a virtual machine
  carries the identity the hypervisor passes through; a virtual-GPU slice
  carries none, and the record says "virtualised" or "sliced" where the
  preflight found it.
- Never written: hostnames, IP addresses, wallets, owners, locations, marketplace
  account names, or any string the host typed.

## 3. Consent

Today's `--share` sends an anonymised summary with no identity at all; that
stays as it is. A record entry needs a separate, explicit opt-in on the
command line (`--record`), with the consent text printed first: what is sent
(the identity block of section 2 and the measured results), what is never
sent, that the entry is public once accepted, and that it cannot be withdrawn
(history is append-only; a later run supersedes, nothing is deleted). Where a
buyer runs the tool on someone else's rig, the record entry is held until the
host's registered wallet consents, the same rule as the host certificate.

**[C3]** The consent text printed before any send names in plain words: that
the board serial is included where the driver exposes it, and that it is a
persistent plain identifier of the card; that the entry is public through the
record lookup once accepted; that it is kept for 24 months; that it cannot be
withdrawn, a later run superseding and nothing deleted. The runner's copy of
the text and the service's are held equal by a test.

## 4. Sources of entries, in the order they start

1. **Free compute runs by RigGrade** (Kaggle, Colab and similar free tiers):
   real cards, few machine types, virtualised and shared; entries are labelled
   `operator: riggrade` and `virtualised`, and count toward nothing in the
   grading references beyond what the spec already allows an operator.
   **[C5]** Operator and virtualised entries form a separate labelled series:
   the record lookup lists them apart, and a virtualised entry never merges
   into the history of a physical card under the same `card_id`. Runs on the
   owner's own free-tier accounts are an execution step confirmed by the owner
   in his own chat before they are made.
2. **Hosts running the free preflight** with `--record`, with the host
   certificate as the incentive. **[C1]** The certificate's launch window is a
   price and a switch step: its dates are the owner's to give in his own chat,
   and until he gives them and confirms the window there, no launch variable is
   set, no certificate issues at zero price, and that code path stays behind
   its off switch (host certificate method, section 6).
3. **Agents running the preflight** before renting a box, through the MCP tool
   and the catalogues, with the same opt-in.
4. Not a source: scraped listings, forum posts, bug reports or any identifier
   collected without the operator of the card running the tool.

## 4a. Sites: a sample of cards, with its evidence stated (owner direction, 2 Nov 2026)

A data centre or a host with many cards is not graded card by card. A site
entry rests on a sample, and states its evidence in numbers on the page:

- **Who picks the cards.** The service draws the sample, never the host: the
  host declares the inventory first (`card_count` per box, boxes per site, per
  peer class), the declaration is recorded and bound to the draw before the
  session nonce is issued, and the nonce then selects card indices from it. A
  host that runs the tool on cards of its own choosing produces card records,
  not a site entry. The declared inventory is printed beside the sample so the
  coverage is visible (for example "12 of a declared 96 cards"). A declared
  inventory that shrinks, or a card that does not match its declared slot,
  voids the entry; nothing is edited.
- **Per class, never pooled.** The draw and the bound run per peer class; a
  site with two classes gets two entries.
- **Minimum sample.** Fewer than 10 sampled cards prints as "insufficient
  sample" and makes no site claim.
- **What is claimed.** Only the pass share of the sampled cards and its
  one-sided 95% lower bound (exact binomial), printed beside the sample size.
  With every sampled card passing, the bound on the site's pass share is: 5
  cards, 55%; 10 cards, 74%; 20 cards, 86%; 30 cards, 90%; 50 cards, 94%. With
  one failure among them: 10 cards, 61%; 20 cards, 78%; 30 cards, 85%. A site
  entry reads, for example, "12 of 12 sampled cards passed; at least 78% of the
  declared 96 pass at 95% confidence; declared inventory, declared runner".
  Measured quality (speed ratio, consistency) is reported as the sample's
  median and range, never extrapolated to the unsampled cards.
- **What is not claimed.** Nothing about cards not sampled beyond the bound;
  nothing about future delivery; nothing about the site's location or owner.
  A site entry never reads as a grade or a certificate for any unsampled card.
  A later sample supersedes, nothing is deleted, and the two are shown side by
  side when they differ.
- **Attestation printed.** The attestation state of the sampled cards prints
  on the entry; where cards are unattested the entry states plainly that a
  substituted card cannot be ruled out by the measurement alone.
- **Steering defences.** The nonce-drawn selection is the defence against
  cherry-picking; attestation where present is the defence against a
  substituted card; a declared inventory that shrinks between samples is
  printed as such. The operator-concentration rules of the grading spec apply
  to site samples as to single rigs.

## 5. What a record can say, and cannot

A record states what a named card delivered in named sessions on named dates,
each with its evidence tag. It is not a title, not proof of ownership, not a
location, and not a statement about any card not measured. Published counts
read "records", never "cards owned" or "hosts". **[C4]** Published counts carry
the locked-counts protection of Revision 5 discussion decision 5: any cell
under five reads "fewer than 5", and larger totals are rounded (to the nearest
five in this build; the decision's rounding rule governs and is confirmed at
the coordinator's read).

## 6. Supersession, corrections, retention

Append-only. A later entry for the same `card_id` is a new line, never an
edit. A proven error is corrected by a new line that names the old one. The
record keeps entries for 24 months, as the grading evidence packs.

## 7. Build, once accepted

- Runner: `--record` flag with the consent text; the identity block already
  written by the preflight (this draft's companion change).
- Service: `POST /riggrade/v1/record` (free, consented entries), `GET
  /riggrade/v1/record/{card_id}` (the history, with evidence tags), a
  per-class count of records on the free index page.
- Anchoring: **[C2]** out of the v0 build. A periodic hash of the record log
  written to Base would be spend; no anchor is written until a design note
  exists and the owner approves the design and its cost in his own chat.
- Tests: identity never carries a UUID or a typed string; consent text printed
  before any send; buyer-run entries held for host consent; append-only.
- **[C6]** No entry is kept or served from any run until this text carries
  [C1] to [C5] and the switch (`RIGGRADE_RECORD`) is set after the
  coordinator's confirmation read. Building behind the off switch proceeded
  as with the feed and the certificate.
