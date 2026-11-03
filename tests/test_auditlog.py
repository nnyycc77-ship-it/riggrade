"""Server copy of sample-audit lines (owner direction 3 Nov 2026)."""
import json
import tempfile
import unittest
from pathlib import Path

from riggrade import auditlog
from riggrade.endpoint import GradingApp
from runner import sample_audit
from tests.test_endpoint import IMAGE, WALLET, FakeAdapter, call

NOW = 1_800_000_000
LINE = {"pool": 1, "gpu_name": "NVIDIA L40S", "driver": "550.54", "gpus": 1, "memory_total_mb": 46068,
        "verdict": "pass", "statuses": {"gpu0.clocks_under_load": "pass", "gpu0.temperature": "pass"},
        "measures": {"tflops": 226.4, "clock_ratio": 0.98}, "exclusive": True,
        "hostname": "box-17.example", "serial": "1324", "note": "free text"}


class AuditLineTests(unittest.TestCase):
    def app(self, audit=True):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = str(Path(tmp.name) / "audit-lines.jsonl")
        env = {"RIGGRADE_ENABLED": "1", "PAYOUT_WALLET_ADDRESS": WALLET, "RIGGRADE_IMAGE_DIGESTS": IMAGE,
               "RIGGRADE_SALE_LOG": str(Path(tmp.name) / "sales.jsonl"), "RIGGRADE_SECRET": "s"}
        if audit:
            env["RIGGRADE_AUDIT_LOG"] = self.log
        return GradingApp(env, lambda: NOW, adapter=FakeAdapter())

    def test_closed_without_a_log(self):
        status, _h, out = call(self.app(audit=False), "POST", "/riggrade/v1/audit/line",
                               {"run": "ionet-20261103", "box": "b10", "line": LINE})
        self.assertEqual(status, "503 Service Unavailable")
        self.assertFalse(out["accepted"])

    def test_stores_only_the_cleaned_line_and_never_identifiers(self):
        app = self.app()
        status, _h, out = call(app, "POST", "/riggrade/v1/audit/line",
                               {"run": "ionet-20261103", "box": "b10", "line": LINE})
        self.assertEqual(status, "200 OK")
        self.assertEqual((out["accepted"], out["box"], out["copies_of_this_box"]), (True, "b10", 1))
        text = Path(self.log).read_text()
        for word in ("box-17", "hostname", "serial", "1324", "free text"):
            self.assertNotIn(word, text)
        row = json.loads(text)
        self.assertEqual((row["run"], row["box"], row["at"]), ("ionet-20261103", "b10", NOW))
        self.assertEqual(row["line"]["measures"]["tflops"], 226.4)

    def test_bad_input_is_400(self):
        app = self.app()
        for body in ({"run": "x", "box": "b1", "line": LINE}, {"run": "ionet-20261103", "box": "box1", "line": LINE},
                     {"run": "ionet-20261103", "box": "b1", "line": {"verdict": "pass"}}, None):
            status, _h, _o = call(app, "POST", "/riggrade/v1/audit/line", body)
            self.assertEqual(status, "400 Bad Request", body)
        self.assertFalse(Path(self.log).exists())

    def test_never_listed_or_served(self):
        app = self.app()
        call(app, "POST", "/riggrade/v1/audit/line", {"run": "ionet-20261103", "box": "b10", "line": LINE})
        _s, _h, doc = call(app, "GET", "/openapi.json")
        self.assertFalse([p for p in doc["paths"] if "audit" in p])
        status, _h, _o = call(app, "GET", "/riggrade/v1/audit/line")
        self.assertNotEqual(status, "200 OK")

    def test_run_cap(self):
        app = self.app()
        for i in range(auditlog.MAX_LINES_PER_RUN):
            auditlog.append(self.log, {"at": NOW, "run": "ionet-20261103", "box": f"b{i % 999}", "line": {}})
        status, _h, _o = call(app, "POST", "/riggrade/v1/audit/line",
                              {"run": "ionet-20261103", "box": "b10", "line": LINE})
        self.assertEqual(status, "429 Too Many Requests")
        status, _h, _o = call(app, "POST", "/riggrade/v1/audit/line",
                              {"run": "akash-20261110", "box": "b10", "line": LINE})
        self.assertEqual(status, "200 OK")

    def test_lines_feed_the_totals_tool_first_copy_standing(self):
        rows = [auditlog.clean({"run": "ionet-20261103", "box": "b10", "line": LINE}, NOW),
                auditlog.clean({"run": "ionet-20261103", "box": "b10", "line": dict(LINE, verdict="fail")}, NOW + 5),
                auditlog.clean({"run": "other-run-01", "box": "b4", "line": LINE}, NOW)]
        text, extra = auditlog.rgaudit_lines(rows, "ionet-20261103")
        self.assertEqual(extra, {"b10": 1})
        lines = sample_audit.read_lines(text)
        self.assertEqual(list(lines), ["b10"])
        self.assertEqual(lines["b10"]["verdict"], "pass")
        out = sample_audit.total([{"box": "b10", "network": "io.net", "listed_model": "L40S",
                                   "listed_region": "x", "usd_per_hour": "1", "outcome": "ready",
                                   "minutes_billed": "60", "listed_gpus": "1"}], lines)
        self.assertEqual(out["verdicts"], {"pass": 1})
        self.assertEqual(out["model_matches_listing"], {"match": 1})


if __name__ == "__main__":
    unittest.main()
