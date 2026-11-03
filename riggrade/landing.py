"""The page a person sees at "/" in a browser. Agents and crawlers that do not
ask for HTML still get the JSON pointer document, unchanged.

Every price and free-period line is passed in from the live configuration at
request time, so the page never states a price the 402 would not.
"""

import html
import secrets

CSP = ("default-src 'none'; style-src 'nonce-{nonce}'; img-src 'self' data:; base-uri 'none'; "
       "form-action 'none'; frame-ancestors 'none'")
REPO = "https://github.com/nnyycc77-ship-it/riggrade"


def render(prefix, offers):
    """offers: [(title, price_line, what, how)] with price_line from live config."""
    nonce = secrets.token_urlsafe(16)
    e = html.escape
    rows = "".join(f"<tr><td><b>{e(t)}</b><br><span class=muted>{e(w)}</span></td><td>{e(p)}</td><td>{e(h)}</td></tr>"
                   for t, p, w, h in offers)
    page = PAGE.format(nonce=nonce, rows=rows, prefix=e(prefix), repo=REPO)
    return page, CSP.format(nonce=nonce)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RigGrade: check the GPU you rented</title><link rel="icon" href="/favicon.ico">
<meta name="description" content="A free one-minute check that a rented GPU box is the box you paid for. Open source.">
<style nonce="{nonce}">
body{{font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;max-width:46rem;margin:0 auto;padding:1.5rem 1rem;
color:#1b1b1b;background:#fafaf8}}
h1{{font-size:1.6rem;margin:.2rem 0}}h2{{font-size:1.1rem;margin:1.6rem 0 .4rem}}
.muted{{color:#666;font-size:.9rem}}pre{{background:#f0f0ec;padding:.75rem 1rem;border-radius:6px;overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:.95rem}}td{{border-top:1px solid #ddd;padding:.5rem .4rem;vertical-align:top}}
a{{color:#1d4ed8}}ul{{padding-left:1.2rem}}
@media (prefers-color-scheme:dark){{body{{background:#151515;color:#e8e8e8}}pre{{background:#262626}}
td{{border-color:#333}}.muted{{color:#aaa}}a{{color:#8ab4ff}}}}
</style></head>
<body>
<p class=muted>RigGrade</p>
<h1>Check the GPU you rented, in one minute</h1>
<p>Renters often get a box that looks fine on the listing and isn't: a card held below its clock by heat, memory
already used by something outside the container, a narrow PCIe link, a driver older than the CUDA build, slow disk
or network. The RigGrade preflight is a free, open-source check you run right after renting:</p>
<pre>pip install riggrade
riggrade-preflight</pre>
<p>It prints pass, warn or fail with the measured numbers and writes <code>preflight-report.json</code>. Nothing is
sent anywhere unless you add <code>--share</code>. Agents can run it as an MCP tool (<code>riggrade-mcp</code>) and
gate a long job on the verdict.</p>

<h2>What it checks</h2>
<ul>
<li>memory or compute already in use by someone else</li>
<li>clocks, slowdown flags and temperature under a 30-second load</li>
<li>PCIe generation and width, driver against CUDA, ECC errors, memory bandwidth</li>
<li>host CPU load, RAM and swap, disk write and download speed</li>
</ul>

<h2>When a check warns or fails, or you need proof</h2>
<table>{rows}</table>
<p>Pay from your own wallet in USDC on Base: in the browser at <a href="{prefix}/checkout">the checkout page</a>, or
by an agent over x402. The price is the same whatever the result, a failed check stays failed, and paying never buys
a pass.</p>

<h2>Who runs it</h2>
<p>RigGrade is a single-operator service with open rules. It is not affiliated with any GPU marketplace and takes no
referral, listing or sponsorship money from marketplaces, hosts or operators. Methods, code and the grading rules
are public (Apache-2.0): <a href="{repo}">{repo}</a>. A warn or fail is a measurement of one box at one time, not a
judgement of a host.</p>
<p class=muted>For agents: <a href="{prefix}">service descriptor</a> &middot; <a href="/llms.txt">llms.txt</a> &middot;
<a href="/openapi.json">OpenAPI</a> &middot; <a href="/.well-known/agent.json">agent card</a></p>
</body></html>
"""
