"""A certificate as a page people can read, print and attach to a dispute:
the same stored object the JSON lookup returns, rendered with the service's
own signature check. Standard library only; every value is escaped."""

import datetime as dt
import html

CSS = ("body{font:15px/1.45 system-ui,sans-serif;max-width:52rem;margin:2rem auto;padding:0 1rem;color:#182848}"
       "h1{font-size:1.5rem;margin:0 0 .25rem}h2{font-size:1.1rem;margin:1.5rem 0 .5rem}"
       "table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.35rem .5rem;border-bottom:1px solid #ddd;vertical-align:top}"
       ".verdict{display:inline-block;padding:.2rem .6rem;border-radius:.3rem;font-weight:600}"
       ".accept{background:#d9f2e3}.exceptions{background:#fff1cc}.reject{background:#f8d7da}"
       ".ok{color:#1a7f3c}.bad{color:#a12622}code{font-size:.9em;word-break:break-all}"
       ".muted{color:#555}@media print{a[href]:after{content:' (' attr(href) ')'}}")


def _e(value):
    return html.escape(str(value), quote=True)


def _when(ts):
    try:
        return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OverflowError):
        return _e(ts)


def _rows(checks):
    out = []
    for c in checks or []:
        if not isinstance(c, dict):
            continue
        extra = {k: v for k, v in c.items() if k not in ("check", "status", "detail")}
        numbers = ", ".join("%s %s" % (_e(k), _e(v)) for k, v in sorted(extra.items()))
        out.append("<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td class=muted>%s</td></tr>" % (
            _e(c.get("check", "")), _e(c.get("status", "")), _e(c.get("detail", "")), numbers))
    return "\n".join(out) or "<tr><td colspan=4>No checks recorded.</td></tr>"


def render(cert, signature_valid, lookup_url="", ed25519_valid=None, keys_url=""):
    verdict = str(cert.get("verdict", "unknown"))
    css_class = "accept" if verdict == "accept" else ("exceptions" if "exception" in verdict else "reject")
    claims = cert.get("claims") or {}
    claim_rows = "\n".join("<tr><td><code>%s</code></td><td>%s</td></tr>" % (_e(k), _e(v))
                           for k, v in sorted(claims.items())) or "<tr><td colspan=2>No claims recorded.</td></tr>"
    exceptions = cert.get("exceptions") or []
    rejections = cert.get("rejections") or []
    lists = ""
    if exceptions:
        lists += "<h2>Exceptions</h2><ul>%s</ul>" % "".join("<li>%s</li>" % _e(x) for x in exceptions)
    if rejections:
        lists += "<h2>Rejections</h2><ul>%s</ul>" % "".join("<li>%s</li>" % _e(x) for x in rejections)
    sig = ("<span class=ok>valid by this service's own check (HMAC): the stored certificate is unchanged.</span>"
           if signature_valid else "<span class=bad>INVALID: the stored certificate does not match its signature</span>")
    ed = cert.get("ed25519") if isinstance(cert.get("ed25519"), dict) else None
    if ed25519_valid is True and ed:
        ed_line = ("<span class=ok>valid: Ed25519 signature by key <code>%s</code>, which is in this service's "
                   "published key set at <a href=\"%s\">%s</a>. Anyone can repeat the check with "
                   "<code>pip install riggrade</code> then <code>riggrade-verify certificate.json</code>, which fetches that key set "
                   "and anchors the result to it (a check against the embedded key alone proves only "
                   "that the certificate matches itself).</span>"
                   % (_e(ed.get("kid", "")), _e(keys_url), _e(keys_url)))
    elif ed25519_valid is False:
        ed_line = "<span class=bad>INVALID: the Ed25519 signature does not verify.</span>"
    else:
        ed_line = ("<span class=muted>none: this certificate predates independently verifiable signatures; "
                   "the recomputation from the evidence pack is the independent check.</span>")
    is_sample = str(cert.get("certificate_id", "")).startswith("rc_sample")
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RigGrade cluster acceptance certificate %(id)s</title><link rel="icon" href="/favicon.ico"><style>%(css)s</style></head>
<body>
<h1>%(sample)sCluster acceptance certificate</h1>
<p class=muted>RigGrade &middot; certificate <code>%(id)s</code> &middot; issued %(issued)s &middot; rules <code>%(rules)s</code></p>
<p><span class="verdict %(cls)s">Verdict: %(verdict)s</span></p>
<p>Independent signature: %(ed)s</p>
<p>Service check: %(sig)s</p>
<h2>What the provider claimed</h2>
<table><tr><th>Claim</th><th>Value</th></tr>
%(claims)s
</table>
<h2>What the cluster delivered</h2>
<p class=muted>%(nodes)s node(s) ran the pinned image(s) %(images)s. Each check is measured against the claim or against the cluster median. Fabric is NCCL all-reduce bus bandwidth in GB/s against the claimed link per GPU (400 Gb/s per GPU is 50 GB/s). A check that could not run is shown with status "not measured", never as passed.</p>
<table><tr><th>Check</th><th>Status</th><th>Detail</th><th>Numbers</th></tr>
%(checks)s
</table>
%(lists)s
<h2>Evidence</h2>
<p>Evidence pack SHA-256 <code>%(pack)s</code>. Anyone holding the pack can recompute every check with the open rules (<code>python3 -m runner.cluster evaluate</code>) and compare the hash.</p>
<p class=muted>%(statement)s</p>
<p class=muted>The JSON form of this certificate is at <a href="%(json)s">%(json)s</a>; save it to verify offline. To keep a copy for a dispute, print this page to PDF and keep the JSON and the evidence pack beside it.</p>
</body></html>
""" % {"sample": "SAMPLE (fictional values, for illustration): " if is_sample else "",
       "id": _e(cert.get("certificate_id", "")), "css": CSS, "issued": _when(cert.get("issued_at")),
       "rules": _e(cert.get("rules", "")), "cls": css_class, "verdict": _e(verdict), "sig": sig, "ed": ed_line,
       "claims": claim_rows, "nodes": _e(cert.get("nodes", "?")),
       "images": ", ".join("<code>%s</code>" % _e(i) for i in (cert.get("images") or [])) or "(unrecorded)",
       "checks": _rows(cert.get("checks")), "lists": lists, "pack": _e(cert.get("pack_sha256", "")),
       "statement": _e(cert.get("statement", "")), "json": _e(lookup_url)}


def wants_html(environ):
    query = environ.get("QUERY_STRING", "")
    if "format=html" in query:
        return True
    if "format=json" in query:
        return False
    accept = environ.get("HTTP_ACCEPT", "")
    return "text/html" in accept and accept.find("text/html") < (accept.find("application/json") % (len(accept) + 1))


def sample():
    """A certificate-shaped example for the docs, with no real secret."""
    return {"product": "riggrade_cluster_acceptance", "certificate_id": "rc_sample0000000000",
            "issued_at": 1_793_491_200, "method": "cluster-acceptance-v0-draft",
            "rules": "cluster-acceptance-v0-draft",
            "pack_sha256": "7c2a9e1f0b4d6a8c3e5f1a2b9d8c7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d",
            "claims": {"gpu_model": "NVIDIA H100 80GB HBM3", "gpus_per_node": 8, "nodes": 2,
                       "inter_node_gbps_per_gpu": 400, "storage_read_gbs": 10},
            "images": ["sha256:" + "ab" * 32], "nodes": 2,
            "checks": [
                {"check": "inventory", "status": "accept", "detail": "2 nodes x 8 NVIDIA H100 80GB HBM3, as claimed"},
                {"check": "image", "status": "accept", "detail": "every node ran the same pinned image"},
                {"check": "node_health", "status": "accept", "detail": "preflight passed on every node"},
                {"check": "straggler", "status": "accept", "detail": "slowest node at 98% of the cluster median",
                 "min_ratio": 0.98},
                {"check": "fabric", "status": "exception", "detail": "all-reduce bus bandwidth 31.0 GB/s against 50 GB/s claimed (62%)",
                 "busbw_gbs": 31.0, "claimed_gbs": 50.0, "ratio": 0.62},
                {"check": "storage", "status": "not measured", "detail": "no shared storage path was given, so the 10 GB/s claim was not tested",
                 "claimed_gbs": 10.0}],
            "verdict": "accept with exceptions",
            "exceptions": ["fabric: 62% of the claimed inter-node link",
                           "storage: claimed but not measured"], "rejections": [],
            "statement": "Measured, not promised. What this cluster delivered during the named test, "
                         "against what its provider claimed; no promise about future delivery.",
            "signature": "0" * 64}
