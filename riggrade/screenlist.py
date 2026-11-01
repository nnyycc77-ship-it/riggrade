"""Build the payer screen list from OFAC's SDN list.

    python3 -m riggrade.screenlist SDN_ADVANCED.XML [SDN.CSV] --out blocked-addresses.txt

Use SDN_ADVANCED.XML. SDN.CSV truncates long remark fields: on the
28 Oct 2026 publication it carried 94 of the 127 0x-form addresses the
XML lists. Passing both keeps the union and records each input.

OFAC lists sanctioned digital currency addresses in the remarks field of
SDN.CSV, as "Digital Currency Address - <TICKER> <address>". Every
0x-form address is kept whatever its ticker (ETH, USDC, USDT, ARB, BSC and
others share the same EVM address form), so an address listed under any
EVM ticker is blocked on Base. Blocking too much is the safe side.

The output starts with a provenance header: the input file's SHA-256 and
byte count, the build time, and the address count. Source of the input:
https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV
The build refuses an input with no addresses, because a real SDN.CSV has
hundreds; an empty result means a wrong file, not a clean list.
"""

import argparse
import datetime as dt
import hashlib
import re
import sys
from pathlib import Path

SOURCE = ("https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/"
          "exports/SDN.CSV")
SOURCE_XML = ("https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/"
              "exports/SDN_ADVANCED.XML")
PATTERN = re.compile(r"Digital Currency Address - ([A-Za-z0-9]+)\s+(0x[0-9a-fA-F]{40})\b")
XML_TYPE = re.compile(r'<FeatureType ID="(\d+)"[^>]*>Digital Currency Address - ([A-Za-z0-9]+)'
                      r'</FeatureType>')
XML_FEATURE = re.compile(r'<Feature ID="\d+" FeatureTypeID="(\d+)">(.*?)</Feature>', re.S)
XML_VALUE = re.compile(r"<VersionDetail[^>]*>\s*(0x[0-9a-fA-F]{40})\s*</VersionDetail>")


def extract(text):
    """Returns {lowercased address: sorted tickers it is listed under}."""
    found = {}
    for ticker, addr in PATTERN.findall(text):
        found.setdefault(addr.lower(), set()).add(ticker.upper())
    return {a: sorted(t) for a, t in found.items()}


def extract_xml(text):
    """Same shape as extract(), from SDN_ADVANCED.XML: every 0x-form value of
    a feature whose type is a "Digital Currency Address - <TICKER>"."""
    types = {tid: ticker.upper() for tid, ticker in XML_TYPE.findall(text)}
    found = {}
    for tid, body in XML_FEATURE.findall(text):
        if tid in types:
            for addr in XML_VALUE.findall(body):
                found.setdefault(addr.lower(), set()).add(types[tid])
    return {a: sorted(t) for a, t in found.items()}


def _is_xml(raw_bytes):
    return raw_bytes.lstrip()[:5] == b"<?xml" or b"<Sanctions" in raw_bytes[:4096]


def build(raw_bytes, now=None, more=()):
    """Build from one SDN file (XML or CSV), plus any `more` files, keeping
    the union. Each input is recorded in the header with its own count."""
    now = now or dt.datetime.now(dt.timezone.utc)
    found, lines = {}, []
    for raw in (raw_bytes,) + tuple(more):
        text = raw.decode("utf-8", errors="replace")
        xml = _is_xml(raw)
        part = extract_xml(text) if xml else extract(text)
        if not part:
            raise ValueError("no digital currency addresses found: not a full SDN file")
        for addr, tickers in part.items():
            found[addr] = sorted(set(found.get(addr, [])) | set(tickers))
        lines.append("# input: %s sha256 %s bytes %d addresses %d"
                     % (SOURCE_XML if xml else SOURCE, hashlib.sha256(raw).hexdigest(),
                        len(raw), len(part)))
    header = ["# RigGrade payer screen list, built from the OFAC SDN list"] + lines + [
              "# built: %s" % now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "# addresses: %d" % len(found)]
    body = ["%s  # %s" % (addr, ",".join(tickers)) for addr, tickers in sorted(found.items())]
    return "\n".join(header + body) + "\n", len(found)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the payer screen list from SDN.CSV")
    ap.add_argument("sdn_file", help="SDN_ADVANCED.XML (preferred) or SDN.CSV")
    ap.add_argument("more", nargs="*", help="further SDN files to union, for example SDN.CSV")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    text, count = build(Path(args.sdn_file).read_bytes(),
                        more=[Path(p).read_bytes() for p in args.more])
    Path(args.out).write_text(text, encoding="utf-8")
    print("wrote %d addresses to %s" % (count, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
