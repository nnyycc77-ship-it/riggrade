"""Build the payer screen list from OFAC's SDN.CSV.

    python3 -m riggrade.screenlist SDN.CSV --out blocked-addresses.txt

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
PATTERN = re.compile(r"Digital Currency Address - ([A-Za-z0-9]+)\s+(0x[0-9a-fA-F]{40})\b")


def extract(text):
    """Returns {lowercased address: sorted tickers it is listed under}."""
    found = {}
    for ticker, addr in PATTERN.findall(text):
        found.setdefault(addr.lower(), set()).add(ticker.upper())
    return {a: sorted(t) for a, t in found.items()}


def build(raw_bytes, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    text = raw_bytes.decode("utf-8", errors="replace")
    found = extract(text)
    if not found:
        raise ValueError("no digital currency addresses found: not a full SDN.CSV")
    header = ["# RigGrade payer screen list, built from OFAC SDN.CSV",
              "# source: %s" % SOURCE,
              "# input sha256: %s" % hashlib.sha256(raw_bytes).hexdigest(),
              "# input bytes: %d" % len(raw_bytes),
              "# built: %s" % now.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "# addresses: %d" % len(found)]
    body = ["%s  # %s" % (addr, ",".join(tickers)) for addr, tickers in sorted(found.items())]
    return "\n".join(header + body) + "\n", len(found)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the payer screen list from SDN.CSV")
    ap.add_argument("sdn_csv")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    text, count = build(Path(args.sdn_csv).read_bytes())
    Path(args.out).write_text(text, encoding="utf-8")
    print("wrote %d addresses to %s" % (count, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
