"""The free GPU price index as the service serves it: the reconstructed
weekly series shipped inside the package (riggrade/index/*.json, written by
pricewatch.reconstructed in the weekly workflow), refreshed from the
repository's published copy when RIGGRADE_INDEX_URL is set, so the live
answer does not wait for a deploy. Information, not an offer or advice."""

import json
import time
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "index"
RECONSTRUCTED = DATA_DIR / "reconstructed-getdeploying.json"
DEFAULT_URL = ("https://raw.githubusercontent.com/nnyycc77-ship-it/riggrade/main/"
               "riggrade/index/reconstructed-getdeploying.json")
REFRESH_SECONDS = 3600
FETCH_TIMEOUT = 3.0


def load_packaged(path=RECONSTRUCTED):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


class Index:
    """Holds the packaged series and, when a URL is given, a refreshed copy
    with a short timeout; a failed refresh keeps the last good copy."""

    def __init__(self, url=None, fetch=None, clock=time.time, packaged=None):
        self.url = url
        self.fetch = fetch or self._fetch
        self.clock = clock
        self.doc = packaged if packaged is not None else load_packaged()
        self.refreshed_at = None
        self.last_error = None

    @staticmethod
    def _fetch(url):
        req = urllib.request.Request(url, headers={"User-Agent": "riggrade-index"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def current(self):
        if self.url and (self.refreshed_at is None or self.clock() - self.refreshed_at >= REFRESH_SECONDS):
            self.refreshed_at = self.clock()
            try:
                doc = self.fetch(self.url)
                if isinstance(doc, dict) and isinstance(doc.get("classes"), dict):
                    self.doc = doc
                    self.last_error = None
                else:
                    self.last_error = "refresh returned an unexpected shape"
            except Exception as exc:  # noqa: BLE001 - the packaged copy stands in
                self.last_error = "%s: %s" % (type(exc).__name__, exc)
        return self.doc


def latest(doc):
    """One row per class: the latest week the source states, with attribution."""
    if not doc:
        return {"series": "reconstructed", "classes": {}, "note": "no index data packaged"}
    classes = {}
    for cid, c in sorted((doc.get("classes") or {}).items()):
        week = c.get("latest_week")
        if not week:
            classes[cid] = {"latest_week": None, "note": "no on-demand weeks mapped"}
            continue
        v = c["weeks"][week]
        classes[cid] = {"latest_week": week, "source_name": c.get("source_name"),
                        "usd_per_gpu_hour_median": v["median_price"],
                        "usd_per_gpu_hour_provider_median": v["provider_median_price"],
                        "usd_per_gpu_hour_min": v["min_price"], "usd_per_gpu_hour_max": v["max_price"],
                        "providers": v["provider_count"], "offerings": v["offering_count"],
                        "weeks_available": len(c["weeks"])}
    families = {}
    for fid, c in sorted((doc.get("chip_families") or {}).items()):
        week = c.get("latest_week")
        if not week:
            continue
        v = c["weeks"][week]
        families[fid] = {"label": c.get("label"), "mixes": c.get("mixes"), "rule": c.get("rule"),
                         "latest_week": week, "source_name": c.get("source_name"),
                         "usd_per_gpu_hour_median": v["median_price"],
                         "usd_per_gpu_hour_provider_median": v["provider_median_price"],
                         "usd_per_gpu_hour_min": v["min_price"], "usd_per_gpu_hour_max": v["max_price"],
                         "providers": v["provider_count"], "offerings": v["offering_count"],
                         "weeks_available": len(c["weeks"])}
    imports = doc.get("imports") or []
    return {"series": doc.get("series"), "method": doc.get("method"), "method_url": doc.get("method_url"),
            # Amendment A1: chip families live beside the classes, never inside them.
            "chip_families": families,
            "billing": doc.get("billing"), "unit": "USD per GPU per hour, on-demand, listed not paid",
            "source": {k: doc["source"].get(k) for k in ("title", "home", "licence", "licence_url", "attribution")},
            "last_import": imports[-1] if imports else None,
            "ambiguous_dropped": doc.get("ambiguous_dropped"),
            "corrections": len(doc.get("corrections") or []),
            "classes": classes, "note": doc.get("note")}
