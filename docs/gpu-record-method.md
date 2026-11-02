# RigGrade GPU record: method v0 (draft, for review)

Status: draft for the coordinator's review; nothing is published from it and
no record is kept beyond each host's own preflight report until it is
accepted. Owner direction 2 Nov 2026, on Adviser 1's side: grow the record
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

## 4. Sources of entries, in the order they start

1. **Free compute runs by RigGrade** (Kaggle, Colab and similar free tiers):
   real cards, few machine types, virtualised and shared; entries are labelled
   `operator: riggrade` and `virtualised`, and count toward nothing in the
   grading references beyond what the spec already allows an operator.
2. **Hosts running the free preflight** with `--record`, with the host
   certificate as the incentive (its launch price is the owner's decision).
3. **Agents running the preflight** before renting a box, through the MCP tool
   and the catalogues, with the same opt-in.
4. Not a source: scraped listings, forum posts, bug reports or any identifier
   collected without the operator of the card running the tool.

## 5. What a record can say, and cannot

A record states what a named card delivered in named sessions on named dates,
each with its evidence tag. It is not a title, not proof of ownership, not a
location, and not a statement about any card not measured. Published counts
read "records", never "cards owned" or "hosts".

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
- Anchoring: a periodic hash of the record log written to Base, so an entry
  is provably unchanged without trusting this service (design note to follow;
  cost is cents per period, an owner decision).
- Tests: identity never carries a UUID or a typed string; consent text printed
  before any send; buyer-run entries held for host consent; append-only.
