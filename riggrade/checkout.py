"""Browser checkout for people: GET /riggrade/v1/checkout.

The paid routes speak x402, which agents use directly. A person arriving from
a post or a template has a browser wallet instead. This page does what an
x402 client does, in the browser, against the same routes:

1. POST the file without payment and read the 402 challenge (price, payout
   address, preview), exactly as an agent would.
2. Ask the wallet to sign an EIP-3009 TransferWithAuthorization for that
   amount to that address, on Base, valid for the challenge's timeout.
3. POST again with the signed x402 v2 PaymentPayload in PAYMENT-SIGNATURE.

Nothing here holds a key, a price or the payout address: all three come from
the live challenge at run time, so the page can never disagree with the API.
Prices, screening, settlement and the sale log are the endpoint's, unchanged.
No third-party script is loaded; the only network calls are to this origin
and to the wallet the person already has.
"""

import json
import secrets

CSP = ("default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
       "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")

PRODUCTS = {
    "fixit": {"title": "Host fix-it report", "path": "/fixit", "field": "preflight_report",
              "file": "preflight-report.json (written by riggrade-preflight)",
              "what": "For each warn or fail in your preflight: the likely cause, the fix steps, who can act "
                      "and the grade pillar it threatens."},
    "cluster": {"title": "Cluster acceptance certificate", "path": "/cluster/acceptance",
                "field": "evidence_pack", "file": "acceptance.json (written by the acceptance test)",
                "what": "The server recomputes every check from your evidence pack and issues a signed, "
                        "stored certificate with a public lookup page."},
}
# A grade session (spec section 13): the session id the benchmark container
# printed. While the class is building its reference the service asks for a
# 0 USDC operator claim (verified, never settled); otherwise the grade price.
PRODUCTS["grade"] = {"title": "Grade a benchmark session (free, no wallet, during the trial)", "path": "/grade", "field": "session_id",
                     "text": "the session id the benchmark container printed (rg_s_...)",
                     "what": "Grades a session whose result bundle was accepted. During the free trial you can "
                             "get the grade with no wallet. While its GPU class is still building its reference, "
                             "signing a 0 USDC operator claim instead lets your rig help set that reference; no "
                             "money moves either way."}
# A standing operator claim (spec 13, owner decision 4 Nov 2026): a host's
# wallet signs one 0 USDC claim for a rig identity and gets a token that lets
# riggrade-monitor --grade-when-idle claim that rig's free reference sessions.
PRODUCTS["standing"] = {"title": "Grade my rig free when idle (standing claim, 0 USDC)",
                        "path": "/host/standing-claim", "field": "rig_id",
                        "text": "your rig id (rg_ + 16 characters, printed by riggrade-preflight and riggrade-monitor)",
                        "what": "For hosts. Your wallet signs one 0 USDC operator claim for this rig; no money moves. "
                                "You get a token, valid 30 days, that riggrade-monitor --grade-when-idle uses to run "
                                "free reference grades while the card is idle. Keep the token like a password."}
ROLES = ("buyer", "provider", "lender", "insurer", "other", "undeclared")


def render(prefix):
    """The page and its Content-Security-Policy header value."""
    nonce = secrets.token_urlsafe(16)
    config = {"prefix": prefix, "products": PRODUCTS, "roles": ROLES}
    options = "".join(f'<option value="{k}">{v["title"]}</option>' for k, v in PRODUCTS.items())
    roles = "".join(f'<option value="{r}">{r}</option>' for r in ROLES)
    page = PAGE.replace("__NONCE__", nonce).replace("__OPTIONS__", options).replace("__ROLES__", roles)
    page = page.replace("__CONFIG__", json.dumps(config).replace("</", "<\\/")).replace("__PREFIX_LINK__", prefix)
    return page, CSP.format(nonce=nonce)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RigGrade checkout</title><link rel="icon" href="/favicon.ico">
<style nonce="__NONCE__">
body{font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;max-width:46rem;margin:0 auto;padding:1.5rem 1rem;
color:#1b1b1b;background:#fafaf8}
h1{font-size:1.5rem;margin:0 0 .25rem}h2{font-size:1.05rem;margin:1.5rem 0 .5rem}
.muted{color:#666;font-size:.9rem}.box{background:#fff;border:1px solid #ddd;border-radius:8px;padding:1rem;margin:.75rem 0}
label{display:block;margin:.5rem 0 .25rem;font-weight:600}select,input[type=file],input[type=text]{font:inherit;max-width:100%}
input[type=text]{width:100%;box-sizing:border-box;padding:.35rem}
button{font:inherit;padding:.5rem 1rem;border-radius:6px;border:1px solid #1b1b1b;background:#1b1b1b;color:#fff;
cursor:pointer;margin:.5rem .5rem 0 0}button[disabled]{opacity:.4;cursor:default}
pre{white-space:pre-wrap;word-break:break-word;background:#f3f3f0;padding:.75rem;border-radius:6px;font-size:.85rem;
max-height:28rem;overflow:auto}.err{color:#a40000}.ok{color:#0b6b2b}code{font-size:.9em}
@media (prefers-color-scheme:dark){body{background:#151515;color:#e8e8e8}.box{background:#1e1e1e;border-color:#333}
pre{background:#262626}.muted{color:#aaa}button{background:#e8e8e8;color:#151515;border-color:#e8e8e8}
.err{color:#ff8080}.ok{color:#7fdc9a}}
</style></head>
<body>
<h1>RigGrade checkout</h1>
<p class=muted>Pay for a RigGrade report from your own browser wallet, in USDC on Base. This page does what an
agent does over x402: it asks the service for the price, your wallet signs a payment of exactly that amount to the
address the service names, and the service settles it and returns your report. RigGrade never sees your keys.
The price is the same whatever the result, and a failed check stays failed. During the fix-it report's free
trial you can also get it with no wallet at all.</p>

<div class=box>
<label for=product>1. What do you want?</label>
<select id=product>__OPTIONS__</select>
<p class=muted id=what></p>
<div id=filebox><label for=file>2. Your file: <span id=filename></span></label>
<input type=file id=file accept=".json,application/json"></div>
<div id=textbox hidden><label for=sid>2. Your session: <span id=textname></span></label>
<input type=text id=sid autocomplete=off spellcheck=false></div>
<div id=rolebox hidden><label for=role>Who is paying? (printed on the certificate, not verified)</label>
<select id=role>__ROLES__</select></div>
<button id=quote>See the price and preview</button>
</div>

<div class=box id=quotebox hidden>
<h2>3. Price and preview</h2>
<p id=price></p>
<pre id=preview></pre>
<p class=muted id=terms></p>
<button id=freebtn hidden>Get it free, no wallet needed</button>
<button id=pay>Connect wallet and sign</button>
</div>

<div class=box id=resultbox hidden>
<h2>4. Result</h2>
<p id=status></p>
<p id=links></p>
<pre id=result></pre>
<button id=download hidden>Download the result (JSON)</button>
</div>

<p class=muted>Agents and scripts can call the same routes directly over x402; see
<a href="__PREFIX_LINK__">the service descriptor</a>. A charge that is settled but not delivered is logged and
refunded by hand. Code and method: https://github.com/nnyycc77-ship-it/riggrade</p>

<script nonce="__NONCE__">
"use strict";
const CFG = __CONFIG__;
const $ = (id) => document.getElementById(id);
let quote = null, body = null, product = null, lastResult = null;

function say(el, text, cls) { el.textContent = text; el.className = cls || ""; }
function usdc(atomic) { const n = BigInt(atomic); return (n / 1000000n) + "." + String(n % 1000000n).padStart(6, "0").replace(/0{1,4}$/, ""); }
function hex(bytes) { return "0x" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(""); }
function b64(text) { return btoa(String.fromCharCode(...new TextEncoder().encode(text))); }

function choose() {
  product = $("product").value;
  const p = CFG.products[product];
  $("what").textContent = p.what;
  $("filename").textContent = p.file || "";
  $("textname").textContent = p.text || "";
  $("filebox").hidden = Boolean(p.text);
  $("textbox").hidden = !p.text;
  $("rolebox").hidden = product !== "cluster";
  $("quotebox").hidden = true; $("resultbox").hidden = true; quote = null;
}

async function readFile() {
  const p0 = CFG.products[product];
  if (p0.text) {
    const v = $("sid").value.trim();
    if (!/^[A-Za-z0-9_-]{4,128}$/.test(v)) throw new Error("Paste " + p0.text + ".");
    const out0 = {}; out0[p0.field] = v; return out0;
  }
  const f = $("file").files[0];
  if (!f) throw new Error("Choose your JSON file first.");
  let doc;
  try { doc = JSON.parse(await f.text()); } catch (e) { throw new Error("That file is not valid JSON."); }
  const p = CFG.products[product];
  const out = {}; out[p.field] = doc;
  if (product === "cluster") out.payer_role = $("role").value;
  return out;
}

async function post(headers) {
  const p = CFG.products[product];
  const res = await fetch(CFG.prefix + p.path, {method: "POST", body: JSON.stringify(body),
    headers: Object.assign({"Content-Type": "application/json", "Accept": "application/json"}, headers || {})});
  let doc = {};
  try { doc = await res.json(); } catch (e) { doc = {error: "the service answered " + res.status}; }
  return {status: res.status, doc};
}

async function getQuote() {
  try {
    body = await readFile();
    const {status, doc} = await post();
    $("quotebox").hidden = false; $("resultbox").hidden = true;
    if (status !== 402 || !doc.accepts || !doc.accepts.length) {
      say($("price"), doc.error || ("Not available right now (status " + status + ")."), "err");
      $("preview").textContent = JSON.stringify(doc.preview || doc.reasons_off || doc, null, 2);
      $("pay").disabled = true; return;
    }
    quote = doc;
    $("freebtn").hidden = true;
    const req = doc.accepts[0];
    const free = req.amount === "0";
    say($("price"), product === "standing"
      ? "Free: your wallet signs one 0 USDC operator claim for this rig. It is verified and never settled, and no money moves. You get a token for riggrade-monitor."
      : doc.free_reference_session
      ? "Free reference session: this GPU class is still building its reference. Your wallet signs a 0 USDC operator claim to the address the service names; it is verified and never settled, and no money moves."
      : free && product === "fixit"
      ? "Free during the current free period. Get it now with no wallet, or sign a 0 USDC authorisation from your wallet if you prefer; either way no money moves."
      : free && product === "grade" && doc.free_reference_session
      ? "Free. Get your grade now with no wallet, or sign a 0 USDC operator claim so your rig also helps set this GPU class's reference; either way no money moves."
      : free && product === "grade"
      ? "Free during the current free period. Get your grade now with no wallet, or sign a 0 USDC authorisation if you prefer; either way no money moves."
      : free
      ? "Free during the current free period. Your wallet still signs a 0 USDC authorisation, so the payer can be screened like any other; no money moves."
      : "Price: " + usdc(req.amount) + " USDC on Base, paid to " + req.payTo + ".", "");
    $("preview").textContent = JSON.stringify(doc.preview || {}, null, 2);
    $("terms").textContent = "Your wallet will ask you to sign a USDC transfer authorisation (EIP-3009) for exactly "
      + usdc(req.amount) + " USDC to " + req.payTo + ", valid for " + Math.round(req.maxTimeoutSeconds / 60)
      + " minutes. Nothing moves until the service settles it, and only after your file has been checked.";
    $("pay").textContent = free ? "Connect wallet and sign (free)" : "Connect wallet and pay " + usdc(req.amount) + " USDC";
    $("pay").disabled = false;
    $("freebtn").hidden = !(free && ((product === "fixit" && !doc.free_reference_session) || product === "grade"));
    $("freebtn").disabled = false;
  } catch (e) { $("quotebox").hidden = false; say($("price"), e.message, "err"); $("pay").disabled = true; }
}

async function pay() {
  const req = quote.accepts[0];
  $("pay").disabled = true;
  $("resultbox").hidden = false; $("download").hidden = true; $("links").textContent = ""; $("result").textContent = "";
  try {
    const eth = window.ethereum;
    if (!eth) throw new Error("No browser wallet found. Install a wallet such as Coinbase Wallet or MetaMask, or call the API over x402.");
    say($("status"), "Waiting for your wallet...", "muted");
    const [from] = await eth.request({method: "eth_requestAccounts"});
    const chainId = parseInt(String(req.network).split(":")[1], 10);
    const current = parseInt(await eth.request({method: "eth_chainId"}), 16);
    if (current !== chainId) {
      await eth.request({method: "wallet_switchEthereumChain", params: [{chainId: "0x" + chainId.toString(16)}]});
    }
    const now = Math.floor(Date.now() / 1000);
    const authorization = {from: from, to: req.payTo, value: req.amount, validAfter: String(now - 600),
      validBefore: String(now + req.maxTimeoutSeconds), nonce: hex(crypto.getRandomValues(new Uint8Array(32)))};
    const typed = {
      types: {
        EIP712Domain: [{name: "name", type: "string"}, {name: "version", type: "string"},
                       {name: "chainId", type: "uint256"}, {name: "verifyingContract", type: "address"}],
        TransferWithAuthorization: [{name: "from", type: "address"}, {name: "to", type: "address"},
          {name: "value", type: "uint256"}, {name: "validAfter", type: "uint256"},
          {name: "validBefore", type: "uint256"}, {name: "nonce", type: "bytes32"}]},
      primaryType: "TransferWithAuthorization",
      domain: {name: req.extra.name, version: req.extra.version, chainId: chainId, verifyingContract: req.asset},
      message: authorization};
    say($("status"), "Sign in your wallet...", "muted");
    const signature = await eth.request({method: "eth_signTypedData_v4", params: [from, JSON.stringify(typed)]});
    const payment = {x402Version: quote.x402Version, resource: quote.resource, accepted: req,
                     payload: {signature: signature, authorization: authorization}};
    if (quote.extensions) payment.extensions = quote.extensions;
    say($("status"), "Paying and preparing your report...", "muted");
    const {status, doc} = await post({"PAYMENT-SIGNATURE": b64(JSON.stringify(payment))});
    lastResult = doc;
    if (status === 200) {
      say($("status"), "Done.", "ok");
      const shown = doc.certificate || doc.report || doc;
      $("result").textContent = JSON.stringify(shown, null, 2);
      if (doc.lookup_url) {
        const a = document.createElement("a");
        a.href = doc.lookup_url + "?format=html"; a.textContent = "Open the certificate page";
        $("links").replaceChildren(a);
      }
      $("download").hidden = false;
    } else {
      const charged = doc.charged === true ? " You were charged (tx " + (doc.tx || "unknown") + "); this is recorded and refunded by hand." : " Nothing was charged.";
      say($("status"), (doc.error || ("The service answered " + status)) + "." + charged, "err");
      $("pay").disabled = false;
    }
  } catch (e) {
    say($("status"), (e && e.message) ? e.message : String(e), "err");
    $("pay").disabled = false;
  }
}

async function getFree() {
  $("freebtn").disabled = true;
  $("resultbox").hidden = false; $("download").hidden = true; $("links").textContent = ""; $("result").textContent = "";
  say($("status"), product === "grade" ? "Grading..." : "Preparing your report...", "muted");
  try {
    const url = product === "grade"
      ? CFG.prefix + "/session/" + encodeURIComponent(body.session_id) + "/grade/free"
      : CFG.prefix + "/fixit/free";
    const res = await fetch(url, {method: "POST", body: JSON.stringify(body),
      headers: {"Content-Type": "application/json", "Accept": "application/json"}});
    let doc = {};
    try { doc = await res.json(); } catch (e) { doc = {error: "the service answered " + res.status}; }
    lastResult = doc;
    if (res.status === 200) {
      say($("status"), "Done. Free, no wallet used, nothing charged.", "ok");
      $("result").textContent = JSON.stringify(doc.report || doc, null, 2);
      $("download").hidden = false;
    } else {
      say($("status"), (doc.error || ("The service answered " + res.status)) + ". Nothing was charged.", "err");
      $("freebtn").disabled = false;
    }
  } catch (e) {
    say($("status"), (e && e.message) ? e.message : String(e), "err");
    $("freebtn").disabled = false;
  }
}

function download() {
  const blob = new Blob([JSON.stringify(lastResult, null, 2)], {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "riggrade-" + product + ".json"; a.click();
}

$("product").addEventListener("change", choose);
$("file").addEventListener("change", () => { $("quotebox").hidden = true; });
$("sid").addEventListener("input", () => { $("quotebox").hidden = true; });
$("role").addEventListener("change", () => { $("quotebox").hidden = true; });
$("quote").addEventListener("click", getQuote);
$("pay").addEventListener("click", pay);
$("freebtn").addEventListener("click", getFree);
$("download").addEventListener("click", download);
choose();
// A link from the benchmark container (?product=grade&session_id=...) opens
// with the session filled in and the quote already asked for.
(function fromLink() {
  const q = new URLSearchParams(window.location.search);
  const want = q.get("product"), sid = q.get("session_id");
  if (!want || !CFG.products[want]) return;
  $("product").value = want; choose();
  if (sid && CFG.products[want].text && /^[A-Za-z0-9_-]{4,128}$/.test(sid)) {
    $("sid").value = sid; getQuote();
  }
})();
</script>
</body></html>
"""
