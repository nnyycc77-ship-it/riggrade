"""Durable state for the Cloud Run deploy: sessions, references and the
sale log must survive a server restart when RIGGRADE_STATE_DIR is set."""

import io
import json
import tempfile
import unittest
from pathlib import Path

from riggrade import serve
from riggrade.grading import ReferenceBook
from riggrade.session import FileStore

ROOT = Path(__file__).resolve().parents[1]


def call(app, method, path, body=None):
    raw = json.dumps(body or {}).encode()
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path,
               "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw)}
    status = []
    out = b"".join(app(environ, lambda s, h: status.append(s)))
    return status[0], json.loads(out)


class FileStoreTests(unittest.TestCase):
    def test_round_trip_and_items_across_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            FileStore(tmp).put("hb:rig/1", {"a": [1, 2]})
            again = FileStore(tmp)
            self.assertEqual(again.get("hb:rig/1"), {"a": [1, 2]})
            self.assertIsNone(again.get("missing"))
            self.assertEqual(again.items(), [("hb:rig/1", {"a": [1, 2]})])
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_returns_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(tmp)
            store.put("k", {"n": 1})
            store.get("k")["n"] = 2
            self.assertEqual(store.get("k"), {"n": 1})


class ReferenceBookPersistenceTests(unittest.TestCase):
    def test_reload_keeps_samples_first_session_rule_and_controlled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "references.json"
            book = ReferenceBook(path)
            for n in range(5):
                book.add("rtx4090", "inference", "rig%d" % n, 100.0 + n, attested=n == 0)
            book.add("rtx4090", "inference", "rig0", 999.0)  # repeat: ignored
            book.set_controlled("h100", "training", 42.0)
            again = ReferenceBook(path)
            self.assertEqual(again.reference("rtx4090", "inference"), (102.0, True))
            self.assertTrue(again.has_rig("rtx4090", "inference", "rig0"))
            self.assertEqual(again.basis("rtx4090", "inference")["attested_rigs"], 1)
            self.assertEqual(again.reference("h100", "training"), (42.0, False))

    def test_memory_book_writes_nothing(self):
        self.assertIsNone(ReferenceBook().path)


class ServeStateTests(unittest.TestCase):
    def test_session_survives_restart_and_sale_log_defaults_into_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"RIGGRADE_STATE_DIR": tmp}
            app = serve.build(env, clock=lambda: 1000)
            self.assertEqual(app.config.sale_log, str(Path(tmp) / "sales.jsonl"))
            status, session = call(app, "POST", "/riggrade/v1/session",
                                   {"workload_classes": ["inference"], "declared_runner": "buyer"})
            self.assertTrue(status.startswith("20"), session)
            restarted = serve.build(env, clock=lambda: 1001)
            status, started = call(restarted, "POST",
                                   "/riggrade/v1/session/%s/start" % session["session_id"])
            self.assertTrue(status.startswith("20"), started)

    def test_explicit_sale_log_wins_and_dark_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = serve.build({"RIGGRADE_STATE_DIR": tmp, "RIGGRADE_SALE_LOG": "/x/s.jsonl"})
            self.assertEqual(app.config.sale_log, "/x/s.jsonl")
            self.assertFalse(app.on_sale)

    def test_no_state_dir_stays_in_memory(self):
        app = serve.build({})
        self.assertIsNone(app.references.path)
        self.assertEqual(app.config.sale_log, "")


class DeployFilesTests(unittest.TestCase):
    def test_server_image_copies_what_the_server_imports(self):
        text = (ROOT / "deploy" / "server.Dockerfile").read_text()
        self.assertIn("COPY riggrade/ riggrade/", text)
        self.assertIn("COPY spec/ spec/", text)
        self.assertIn("RIGGRADE_STATE_DIR=/state", text)
        self.assertNotIn("runner/", text)
        self.assertIn("deploy/server.Dockerfile",
                      (ROOT / "deploy" / "cloudbuild.yaml").read_text())
