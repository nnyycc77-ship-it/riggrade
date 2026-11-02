"""The free-GPU notebook runs end to end against this package.

The Kaggle and Colab runs of 2 Nov 2026 (coordinator Messages 281b and 284)
stopped because the notebook installed riggrade 0.1.0 from PyPI, which wrote
no identity block or measures and knew no share flag. These tests hold the
notebook and the package together: every preflight command line in the
notebook parses and runs, the identity cell runs on the report it writes,
the dry share sends nothing, and the install pin is not ahead of this
package's own version."""

import contextlib
import io
import json
import os
import re
import shlex
import tempfile
import unittest
from pathlib import Path

import tomllib

from runner import preflight as pf
from tests.test_preflight import FakeProbes

ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK = ROOT / "notebooks" / "preflight_free_gpu.ipynb"


class CardProbes(FakeProbes):
    def cards(self):
        return pf.parse_cards_csv("0, GPU-1111, 1650000000001\n1, GPU-2222, [N/A]\n")


def cells():
    nb = json.loads(NOTEBOOK.read_text())
    return ["".join(c["source"]) if isinstance(c["source"], list) else c["source"]
            for c in nb["cells"] if c["cell_type"] == "code"]


def preflight_lines():
    return [line[len("!python -m runner.preflight"):].strip()
            for cell in cells() for line in cell.splitlines()
            if line.startswith("!python -m runner.preflight")]


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        self.saved = pf.Probes, pf.share
        pf.Probes = CardProbes
        self.sent = []
        pf.share = lambda doc, endpoint=None, **kw: self.sent.append(doc) or {"accepted": True}

    def tearDown(self):
        pf.Probes, pf.share = self.saved
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def run_line(self, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = pf.main(shlex.split(args.replace("/tmp", self.tmp.name)))
        return code, out.getvalue()

    def test_every_preflight_line_parses_and_runs(self):
        lines = preflight_lines()
        self.assertGreaterEqual(len(lines), 2)
        for args in lines:
            code, _ = self.run_line(args)
            self.assertEqual(code, 0, args)

    def test_identity_cell_runs_on_the_report_the_run_writes(self):
        self.run_line(preflight_lines()[0])
        identity_cell = next(c for c in cells() if "preflight-report.json" in c)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(compile(identity_cell, "identity-cell", "exec"), {})  # noqa: S102 - our own notebook cell
        text = out.getvalue()
        self.assertRegex(text, r"rig_id\s+rg_[0-9a-f]{16}")
        self.assertIn('"card_id": "gc_', text)
        self.assertIn("measures", text)
        self.assertNotIn("GPU-1111", text)

    def test_dry_share_prints_the_summary_and_sends_nothing(self):
        line = next(a for a in preflight_lines() if "--dry-share" in a)
        _, text = self.run_line(line)
        self.assertIn("Summary to send:", text)
        self.assertIn("Dry run: nothing was sent.", text)
        self.assertEqual(self.sent, [])
        summary = text.split("Summary to send:", 1)[1]
        for identity in ("rig_id", "card_id", "1650000000001", "GPU-1111"):
            self.assertNotIn(identity, summary)

    def test_share_sends_only_the_anonymised_summary(self):
        line = next(a for a in preflight_lines() if "--dry-share" in a).replace("--dry-share", "--share")
        _, text = self.run_line(line)
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("rig_id", self.sent[0])
        self.assertNotIn("cards", self.sent[0])
        self.assertIn("Shared with the RigGrade pool", text)

    def test_install_pin_is_met_by_this_package(self):
        pin = re.search(r"riggrade>=([0-9.]+)", "\n".join(cells()))
        self.assertIsNotNone(pin, "the install cell pins a minimum release")
        version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        as_tuple = lambda v: tuple(int(p) for p in v.split("."))
        self.assertGreaterEqual(as_tuple(version), as_tuple(pin.group(1)))


if __name__ == "__main__":
    unittest.main()
