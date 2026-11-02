# RigGrade host certificate and badge: method v0 (draft)

Status: draft for review; nothing is built from it until the coordinator has
read it, and the build sits behind a product switch (`hostcert` in
`RIGGRADE_PRODUCTS`) that sells nothing until the owner sets a price and the
coordinator records it. Owner direction 2 Nov 2026: go on the design, no price
set. Written to the Message 259 conditions and the four named conditions of
Message 261b, each marked **[261b-n]** where it lands.

## 1. What it is

A host (the operator of a GPU rig for rent) runs the existing RigGrade grade
on their own rig: a session with drawn parameters, the pinned benchmark
container, the result bundle, the paid grade. Today that grade produces labels
and an evidence pack for the payer. The host certificate re-issues the same
measured facts as a signed, dated, independently verifiable certificate the
host may publish: a public verify page, a JSON form, and a badge image the host
may show, each pointing at the verify page.

Nothing new is measured. The certificate is the grade's facts with a public face
and a signature anchored to the published key set.

## 2. What the certificate states, and how it is worded

Every certificate, its verify page and its badge print, in words:

- **the grade kind** **[261b-1]**: "point grade" or "full grade", exactly as the
  grade's `form` field reads. A point grade certificate prints "Reliability not
  measured (point grade)" on the certificate, on the page and on the badge, so
  none of the three can read as a full grade (Revision 5 discussion decisions 9
  and 10; `docs/grading-spec.md` section 2);
- the peer class and workload class; the grade (or "insufficient data" where the
  class bar is not yet set) and the pillar that capped it;
- the pillar values as measured, and the throughput figures from the bundle;
- **the declared runner** ("declared runner: host" or "declared runner: buyer",
  exactly as the session declared it), the words "declared, not observed by
  RigGrade", the date and time of the run, and the pinned image digest the
  bundle carries. A certificate never reads as a run observed by this service
  (Revision 5, discussion decision 2: a declaration is stated as a declaration);
- **the full evidence tag** **[261b-2]** as the grade carries it
  (`declared:<buyer|host> attestation:<attested|unattested>`, with `host-paid`
  and `provisional` where they apply), printed on the certificate, on the page
  and on the badge. The badge never carries the declared runner without the
  attestation state beside it (Revision 5 discussion decision 2);
- the date of issue, and the sentence "valid as of the run date; a later run
  supersedes this certificate";
- the statement: "Measured, not promised. What this rig delivered during the
  named session, against published bars for its class; no promise about future
  delivery."

The badge image carries: the grade and its kind (with the reliability line for
a point grade), the class, the run date, the full evidence tag, and the verify
URL. Nothing else: no stars, no "verified host", no ranking words. Badge text
passes the same promise-word lint as grade labels (`riggrade.label.lint`).

## 3. Independently verifiable, in the anchored sense only

Signed exactly as the cluster certificate since PR 60: an Ed25519 signature over
the canonical body, `keys_url` inside the signed body, verifiable with
`riggrade-verify` or any Ed25519 library against the published key set at
`/.well-known/riggrade-keys.json`. The phrase "independently verifiable" is used
only in that anchored sense (Messages 247 and 249); a check against the embedded
key alone proves only self-consistency, and the page says so in those words.

## 4. Who may publish, consent, and anti-gaming

- **Who may ask.** The payer of the grade may ask for the certificate, at grade
  time (a `publish` flag on the grade request) or later by session id with the
  same wallet's authorisation. A grade without the flag stays private as today.
- **Consent where the payer is not the host** **[261b-3]**. A certificate is a
  publication about the host's rig. Where the payer is the host (a host-paid
  session, `declared runner: host`, payer wallet equal to the session's
  `host_wallet`), the payer's flag is the host's consent and the rule is met.
  Where a buyer paid for a grade of someone else's rig, the buyer's flag alone
  publishes nothing: the certificate issues only on consent given through the
  host's registered wallet, as a 0 USDC x402 authorisation to the payout address
  from `host_wallet` naming the session id (verified by the facilitator, never
  settled, screened like a payer, the same form as the free-session operator
  claim). Until that consent is recorded the request answers 403 with the reason
  and nothing is issued or charged (Revision 5 discussion decision 3).
- **The run is the ordinary grade.** Parameters drawn at session start and
  revealed only then, the pinned image refused otherwise, the bundle checked as
  today. A host cannot tune a run in advance any more than a buyer can.
- **One certificate per session.** Renewal is a new session and a new
  certificate. Nothing is deleted; an older certificate stays verifiable as what
  it was and shows its date.
- **Supersession rests on the recorded rig identity** **[261b-4]**. The verify
  page of an older certificate links to a newer one only when the two sessions'
  evidence packs record the same rig: the `rig_id` the runner derives from the
  cards' hardware UUIDs (`runner/identity.py`, a hash of the sorted UUIDs), with
  the same device name, memory and card count. A rig name or id typed by the
  host, or carried in a request body, never drives the link. Where the recorded
  identities differ, the page shows no supersession link and says why ("a later
  certificate exists for a different recorded rig" is not printed; nothing is
  printed, so no link is implied).
- The host's wallet is the operator for spec 13 purposes, as today for a
  host-paid session; the operator-concentration rules on class references are
  unchanged.

## 5. Marketplace terms, checked before any badge is offered for a listing

A badge may be offered for use in a given marketplace's listings only after that
marketplace's terms have been read in full and recorded in this method, with the
clause that allows or forbids third-party images, links or benchmark claims in
listings quoted. Until a marketplace is recorded here as permitting it, the
badge is offered for the host's own pages only, and the verify page is the
public artefact. Grok task 010 (research input, untrusted) points at the pages;
the primary terms are read and recorded here before any entry.

Marketplaces recorded: none yet.

## 6. Price and scope

Price: open owner decision. The grade price (3 USDC consumer, 5 USDC data
centre) stays as is; whether the certificate is included in the grade or priced
separately is the owner's call, recorded by the coordinator before the switch is
set. No listing, no outreach, no spend.

## 7. What it cannot say

A certificate states one session on one rig at one time, from a declared runner.
It is not a marketplace verification, not a reliability record over time (the
full grade's trailing windows are a different product), and not a statement
about any other rig the host operates.

## 8. Build, once this text is accepted

Build state, 2 Nov 2026 (riggrade PR 74, after the coordinator's acceptance in
board Message 268): built as listed below, behind the `hostcert` switch, off,
with no price. One deviation, stated plainly: the certificate is issued only by
the later route (`POST /riggrade/v1/host/certificate` with the session id, paid
by the wallet that paid the grade); the `publish` flag on the grade request waits
on the owner's price decision, because whether the certificate is included in
the grade price or priced separately decides what that flag settles. Marketplaces
recorded for the badge: none. Routes: `POST /host/certificate`, `POST
/host/consent` (0 USDC host authorisation), `GET /host/{id}` (JSON or page),
`GET /host/{id}/badge.svg`.


- `riggrade/hostcert.py`: build the certificate body from the labels and the
  evidence pack (grade kind, reliability line, full evidence tag, declared runner
  wording, recorded rig identity), sign (reuse `acceptance.attach_ed25519` and
  the HMAC), store under `hostcert:<id>`.
- `GET /riggrade/v1/host/<id>` (JSON, and the printable page by content
  negotiation, as the cluster certificate); `GET /riggrade/v1/host/<id>/badge.svg`.
- A `publish` flag on the grade request; the host-consent authorisation where the
  payer is not the host; the product switch entry `hostcert`, off by default;
  no price constant until the owner sets one (`RIGGRADE_HOSTCERT_PRICE_ATOMIC`,
  as the ledger feed).
- Tests for wording (grade kind and reliability line, full tag on the badge,
  declared runner, dates, lint), signatures (anchored), one per session, consent
  refusal for a buyer-paid session, supersession only on matching recorded rig
  identity, and the switch off by default.
