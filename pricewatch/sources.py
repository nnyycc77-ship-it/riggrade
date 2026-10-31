"""Source adapters. Each turns one source's public listings into normalised
offers (see aggregate.py) and drops every identifier.

An adapter checks the fields it relies on and raises SchemaError when they
are missing, so a format change stops the run loudly instead of producing a
quiet wrong number (method section 6: gaps stay gaps).
"""

import json
import urllib.request


class SchemaError(ValueError):
    pass


def _get_json(url, opener=urllib.request.urlopen, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "riggrade-pricewatch",
                                               "Accept": "application/json"})
    with opener(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class VastOffers:
    """Vast.ai on-demand offers.

    UNVERIFIED: the endpoint and field names below are taken from Vast's
    public CLI and SDK documentation as seen through search results; the
    build environment could not reach vast.ai. The first real run must
    confirm them and the terms before any value is published (method
    section 2). The required-field check enforces that.
    """

    name = "vast"
    URL = ("https://console.vast.ai/api/v0/bundles/?q=" +
           urllib.request.quote(json.dumps({"rentable": {"eq": True},
                                            "rented": {"eq": False},
                                            "type": "on-demand",
                                            "limit": 5000})))
    REQUIRED = ("gpu_name", "num_gpus", "gpu_ram", "dph_total")

    def __init__(self, fetch=None):
        self.fetch = fetch or (lambda: _get_json(self.URL))

    def normalise(self, payload):
        offers = payload.get("offers") if isinstance(payload, dict) else None
        if not isinstance(offers, list):
            raise SchemaError("vast: no 'offers' list in response")
        out = []
        for o in offers:
            missing = [k for k in self.REQUIRED if k not in o]
            if missing:
                raise SchemaError("vast: offer missing fields %s" % missing)
            out.append({"gpu_name": o["gpu_name"],
                        # gpu_ram is per GPU in MB in Vast's listings
                        "gpu_memory_gb": (o["gpu_ram"] or 0) / 1024,
                        "num_gpus": o["num_gpus"],
                        "total_price_per_hour": o["dph_total"],
                        "verified": (o["verification"] == "verified")
                        if "verification" in o else o.get("verified")})
        return out

    def offers(self):
        return self.normalise(self.fetch())


SOURCES = {"vast": VastOffers}
