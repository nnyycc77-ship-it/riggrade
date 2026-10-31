"""Fixtures here are synthetic, shaped like the documented formats; they are
not real listings."""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from riggrade import salelog
from pricewatch import aggregate as ag
from pricewatch import record as rec
from pricewatch.sources import SchemaError, VastOffers

NOW = dt.datetime(2026, 11, 1, 0, 30, tzinfo=dt.timezone.utc)


def offer(name="RTX 4090", mem=24.0, n=1, price=0.40, verified=True):
    return {"gpu_name": name, "gpu_memory_gb": mem, "num_gpus": n,
            "total_price_per_hour": price, "verified": verified}


class Fake:
    def __init__(self, name, offers=None, error=None):
        self.name, self._offers, self.error = name, offers or [], error

    def offers(self):
        if self.error:
            raise self.error
        return self._offers


class AggregateTests(unittest.TestCase):
    def test_weighted_percentile(self):
        self.assertEqual(ag.weighted_percentile([(1, 1), (2, 1), (3, 1)], 0.5), 2)
        # an 8-GPU listing at 1.0 outweighs two single-GPU listings at 5.0
        self.assertEqual(ag.weighted_percentile([(1.0, 8), (5.0, 1), (5.0, 1)], 0.5), 1.0)
        self.assertIsNone(ag.weighted_percentile([], 0.5))

    def test_per_gpu_price_classes_and_minimum_sample(self):
        offers = [offer(price=0.30 + 0.01 * i) for i in range(10)]
        offers += [offer("RTX 4090", 24.0, n=2, price=0.80, verified=False)]
        offers += [offer("H100 SXM", 80.0, price=2.0)] * 3
        offers += [offer("Tesla T4", 16.0), offer(price=0, n=1), offer(n=0)]
        summary, _pairs = ag.by_class(offers)
        c = summary["classes"]["rtx4090-24"]
        self.assertEqual((c["status"], c["offers"], c["gpus"]), ("ok", 11, 12))
        self.assertEqual(c["median"], 0.35)  # 6th of 12 GPUs
        self.assertAlmostEqual(c["verified_share"], 10 / 12, places=3)
        self.assertEqual(summary["classes"]["h100-sxm-80"]["status"], "insufficient")
        self.assertNotIn("median", summary["classes"]["h100-sxm-80"])
        self.assertEqual(summary["unmatched_offers"], 3)

    def test_performance_adjusted_waits_for_five_rigs(self):
        self.assertIsNone(ag.performance_adjusted(0.40, [1000.0] * 4))
        # 0.40 $/h over 1000 tok/s = 0.40 / 3.6M tokens -> $0.1111 per million
        self.assertEqual(ag.performance_adjusted(0.40, [900, 1000, 1000, 1100, 1200]), 0.1111)


class VastAdapterTests(unittest.TestCase):
    def test_normalises_and_drops_identifiers(self):
        payload = {"offers": [{"id": 123, "machine_id": 9, "host_id": 7,
                               "gpu_name": "RTX 4090", "num_gpus": 2,
                               "gpu_ram": 24564, "dph_total": 0.9,
                               "verification": "verified"}]}
        (o,) = VastOffers(fetch=lambda: payload).offers()
        self.assertEqual(set(o), {"gpu_name", "gpu_memory_gb", "num_gpus",
                                  "total_price_per_hour", "verified"})
        self.assertAlmostEqual(o["gpu_memory_gb"], 23.99, places=2)
        self.assertTrue(o["verified"])

    def test_format_change_fails_loudly(self):
        with self.assertRaises(SchemaError):
            VastOffers(fetch=lambda: {"results": []}).offers()
        with self.assertRaises(SchemaError):
            VastOffers(fetch=lambda: {"offers": [{"gpu_name": "RTX 4090"}]}).offers()


class RecordTests(unittest.TestCase):
    def test_snapshot_chain_and_no_identifiers(self):
        good = Fake("a", [offer(price=0.3 + 0.01 * i) for i in range(12)])
        bad = Fake("b", error=SchemaError("format changed"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "listed.jsonl"
            snap = rec.record(path, [good, bad], now=NOW)
            self.assertEqual(snap["date"], "2026-11-01")
            self.assertIn("b", snap["failed_sources"])
            self.assertEqual(snap["combined"]["rtx4090-24"]["status"], "ok")
            self.assertEqual(snap["combined"]["h100-sxm-80"]["status"], "missing")
            self.assertEqual(len(salelog.read_chain(path)), 1)
            with self.assertRaises(SystemExit):
                rec.record(path, [good], now=NOW)  # one snapshot per day
            text = path.read_text()
            for word in ("machine_id", "host_id", '"id"'):
                self.assertNotIn(word, text)
            # an edited history breaks the chain
            path.write_text(text.replace("0.3", "0.9", 1))
            with self.assertRaises(ValueError):
                salelog.read_chain(path)

    def test_insufficient_source_is_left_out_of_combined(self):
        thin = Fake("a", [offer()] * 3)
        snap = rec.snapshot([thin], now=NOW)
        self.assertEqual(snap["combined"]["rtx4090-24"]["status"], "missing")


if __name__ == "__main__":
    unittest.main()
