"""Host certificate and badge (docs/host-certificate-method.md, v0).

The grade's measured facts re-issued as a signed, dated certificate the
host may publish: a JSON form, a printable verify page and a badge image,
each pointing at the verify page. Nothing new is measured. The product
ships off: it sells only when RIGGRADE_PRODUCTS names "hostcert" and the
owner has set RIGGRADE_HOSTCERT_PRICE_ATOMIC.
"""

import hashlib
import html

from . import acceptance, certificate_page, signing
from .classes import label_name
from .grading import INSUFFICIENT
from .label import WORKLOAD_NAMES, lint

PRODUCT = "riggrade_host_certificate"
METHOD_VERSION = "host-certificate-v0"
STATEMENT = ("Measured, not promised. What this rig delivered during the named session, "
             "against published bars for its class; no promise about future delivery.")
VALIDITY = "valid as of the run date; a later run supersedes this certificate"
DECLARED = "declared, not observed by RigGrade"
VERIFY_NOTE = ("Independently verifiable in the anchored sense only: the Ed25519 signature is checked "
               "against the published key set at keys_url; a check against the embedded key alone "
               "proves only that the certificate matches itself.")
SCOPE = ("One session on one rig at one time, from a declared runner. Not a marketplace "
         "verification, not a reliability record over time, and not a statement about any "
         "other rig the host operates.")


def grade_kind(label):
    return "point grade" if label.get("form") == "point" else "full grade"


def reliability_line(label):
    """[261b-1] A point grade prints this on certificate, page and badge."""
    return "Reliability not measured (point grade)" if label.get("form") == "point" else None


def identity_key(pack):
    """[261b-4] The rig identity as the evidence pack records it: the
    runner's hash of the cards' hardware UUIDs with device, memory and
    card count. Never a string the host typed."""
    ident = (pack or {}).get("identity") or {}
    return [ident.get("rig_id") or "rg_unknown", ident.get("device_name"),
            ident.get("memory_gb"), ident.get("card_count")]


def consent_state(session, grade):
    """[261b-3] Who may publish. Returns one of:
    host_paid: the grade's payer is the session's registered host wallet;
    consented: the host wallet gave a 0 USDC authorisation naming the session;
    needed: a host wallet is registered and has not consented;
    impossible: no host wallet is registered, so nobody can consent."""
    host = (session.get("host_wallet") or "").lower()
    payer = (grade.get("payer") or "").lower()
    if host and payer and host == payer:
        return "host_paid"
    consent = (session.get("host_consent") or "").lower()
    if host and consent == host:
        return "consented"
    return "needed" if host else "impossible"


def build(session, grade, now, cert_id, keys_url, secret, signer=None, paying_wallet=None):
    labels = grade.get("labels") or []
    pack = grade.get("pack") or {}
    att = "attested" if pack.get("attested") else "unattested"
    tag = labels[0]["evidence_tag"] if labels else f"declared:{session.get('declared_runner')} attestation:{att}"
    items = []
    for lab in labels:
        items.append({"peer_class": lab.get("peer_class"), "workload_class": lab.get("workload_class"),
                      "grade": lab.get("grade"), "grade_kind": grade_kind(lab),
                      "reliability": reliability_line(lab), "capped_by": lab.get("capped_by"),
                      "pillars": lab.get("pillars"), "status": lab.get("status"),
                      "evidence_tag": lab.get("evidence_tag"), "window": lab.get("window"),
                      "measured": (pack.get("classes") or {}).get(lab.get("workload_class"))})
    cert = {"product": PRODUCT, "method": METHOD_VERSION, "certificate_id": cert_id,
            "issued_at": now, "session_id": session["session_id"],
            "run": {"started_at": session.get("started_at"), "graded_at": grade.get("at"),
                    "image_digest": pack.get("image_digest"), "bench_version": pack.get("bench_version")},
            "declared_runner": session.get("declared_runner"), "declared_note": DECLARED,
            "evidence_tag": tag, "attested": bool(pack.get("attested")),
            "consent": consent_state(session, grade),
            "rig_identity": identity_key(pack),
            "grades": items,
            "pack_sha256": hashlib.sha256(acceptance.canonical(pack)).hexdigest(),
            "validity": VALIDITY, "statement": STATEMENT, "scope": SCOPE,
            "verify_note": VERIFY_NOTE, "keys_url": keys_url, "paying_wallet": paying_wallet}
    cert["signature"] = acceptance.sign(cert, secret)
    acceptance.attach_ed25519(cert, signer)
    return cert


def badge_lines(cert, verify_url, date_text):
    """[261b-1][261b-2] The badge prints the grade and its kind (with the
    reliability line for a point grade), the class, the run date, the full
    evidence tag and the verify URL. Nothing else."""
    lines = []
    for g in cert.get("grades") or []:
        head = "Insufficient data" if g["grade"] == INSUFFICIENT else f"Grade {g['grade']}"
        work = WORKLOAD_NAMES.get(g["workload_class"], g["workload_class"])
        line = f"{head} · {label_name(g['peer_class'])} class · {work} · {g['grade_kind']}"
        if g.get("reliability"):
            line += " · " + g["reliability"]
        lines.append(line)
    lines.append("evidence: " + cert["evidence_tag"])
    lines.append(f"run {date_text} · {DECLARED}")
    lines.append("verify: " + verify_url)
    bad = lint(lines)
    if bad:
        raise RuntimeError(f"promise words in badge text: {bad}")
    return lines


def badge_svg(cert, verify_url, date_text):
    lines = badge_lines(cert, verify_url, date_text)
    width = 12 + 7 * max(len(line) for line in lines)
    height = 16 + 18 * len(lines)
    rows = "".join(f'<text x="8" y="{20 + 18 * i}">{html.escape(line)}</text>'
                   for i, line in enumerate(lines))
    label = html.escape(" | ".join(lines))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" role="img" '
            f'aria-label="{label}" font-family="ui-monospace,Menlo,monospace" font-size="12">'
            '<rect width="100%" height="100%" rx="4" fill="#f4f6fa" stroke="#182848"/>'
            f'<g fill="#182848">{rows}</g></svg>')


def render(cert, signature_valid, json_url, ed25519_valid, keys_url, superseded_by=None):
    e = certificate_page._e
    rows = []
    for g in cert.get("grades") or []:
        rel = g.get("reliability") or ""
        cells = [label_name(g["peer_class"]), WORKLOAD_NAMES.get(g["workload_class"], g["workload_class"]),
                 g["grade"], g["grade_kind"], rel, g.get("capped_by") or ""]
        rows.append("<tr>" + "".join(f"<td>{e(c)}</td>" for c in cells) + "</tr>")
    pillars = []
    for g in cert.get("grades") or []:
        for k, v in sorted((g.get("pillars") or {}).items()):
            work = WORKLOAD_NAMES.get(g["workload_class"], g["workload_class"])
            pillars.append(f"<tr><td>{e(work)}</td><td>{e(k)}</td><td>{e(v)}</td></tr>")
    ed = cert.get("ed25519") if isinstance(cert.get("ed25519"), dict) else None
    if ed25519_valid is True and ed:
        ed_line = (f"valid: Ed25519 signature by key <code>{e(ed.get('kid', ''))}</code>, in the published "
                   f"key set at <a href=\"{e(keys_url)}\">{e(keys_url)}</a>. {e(VERIFY_NOTE)}")
    elif ed25519_valid is False:
        ed_line = "INVALID: the Ed25519 signature does not verify."
    else:
        ed_line = "none: this certificate carries no Ed25519 signature."
    sig = ("valid by this service's own check (HMAC): the stored certificate is unchanged."
           if signature_valid else "INVALID: the stored certificate does not match its signature")
    sup = (f"<p><b>Superseded:</b> a later certificate exists for the same recorded rig: "
           f"<a href=\"{e(superseded_by['url'])}\">{e(superseded_by['certificate_id'])}</a>.</p>"
           if superseded_by else "")
    run = cert.get("run") or {}
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RigGrade host certificate {id}</title><link rel="icon" href="/favicon.ico"><style>{css}</style></head>
<body>
<h1>Host certificate</h1>
<p class=muted>RigGrade &middot; certificate <code>{id}</code> &middot; issued {issued} &middot; method <code>{method}</code> &middot; {validity}</p>
{sup}
<h2>What was measured</h2>
<table><tr><th>Class</th><th>Workload</th><th>Grade</th><th>Kind</th><th>Reliability</th><th>Capped by</th></tr>
{rows}
</table>
<h2>Pillar values</h2>
<table><tr><th>Workload</th><th>Pillar</th><th>Value</th></tr>
{pillars}
</table>
<h2>Who ran it, and when</h2>
<p><b>Declared runner: {runner}</b> ({declared}). Evidence tag: <code>{tag}</code>. Run started {started}, graded {graded}, under the pinned image <code>{image}</code> (bench {bench}).</p>
<p>Recorded rig identity (the runner's hash of the cards' hardware identifiers, with device, memory and card count): <code>{rig}</code>. A later certificate links here only when its recorded identity matches.</p>
<p>Independent signature: {ed}</p>
<p>Service check: {sig}</p>
<p class=muted>{statement} {scope}</p>
<p class=muted>JSON form: <a href="{json}">{json}</a>; badge: <a href="{badge}">{badge}</a>. Evidence pack SHA-256 <code>{pack}</code>.</p>
</body></html>
""".format(**values(cert, run, rows, pillars, sup, ed_line, sig, json_url, keys_url))


def values(cert, run, rows, pillars, sup, ed_line, sig, json_url, keys_url):
    e = certificate_page._e
    return {"id": e(cert.get("certificate_id", "")), "css": certificate_page.CSS,
            "issued": certificate_page._when(cert.get("issued_at")), "method": e(cert.get("method", "")),
            "validity": e(VALIDITY), "sup": sup, "rows": "\n".join(rows) or "<tr><td colspan=6>none</td></tr>",
            "pillars": "\n".join(pillars) or "<tr><td colspan=3>none</td></tr>",
            "runner": e(cert.get("declared_runner", "")), "declared": e(DECLARED),
            "tag": e(cert.get("evidence_tag", "")),
            "started": certificate_page._when(run.get("started_at")),
            "graded": certificate_page._when(run.get("graded_at")),
            "image": e(run.get("image_digest") or "unrecorded"), "bench": e(run.get("bench_version") or ""),
            "rig": e(" / ".join(str(x) for x in cert.get("rig_identity") or [])), "ed": ed_line, "sig": sig,
            "statement": e(cert.get("statement", "")), "scope": e(SCOPE), "json": e(json_url),
            "badge": e(json_url.split("?")[0] + "/badge.svg"), "pack": e(cert.get("pack_sha256", ""))}


def keys_path():
    return signing.KEYS_PATH
