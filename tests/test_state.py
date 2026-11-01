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


class DeployWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()

    def test_manual_only_main_only_keyless(self):
        on_block = self.text.split("on:", 1)[1].split("permissions:", 1)[0]
        self.assertEqual(on_block.split(), ["workflow_dispatch:"])
        self.assertIn("if: github.ref == 'refs/heads/main'", self.text)
        self.assertIn("id-token: write", self.text)
        self.assertIn("workload_identity_provider:", self.text)
        for word in ("credentials_json", "secrets."):
            self.assertNotIn(word, self.text)

    def test_deploy_matches_the_documented_shape(self):
        for flag in ("--max-instances 1", "--add-volume-mount volume=state,mount-path=/state",
                     "mount-options=uid=10001;gid=10001", "deploy/server.Dockerfile"):
            self.assertIn(flag, self.text)
        doc = (ROOT / "docs" / "deploy-cloudrun.md").read_text()
        self.assertIn("assertion.ref=='refs/heads/main'", doc)
        self.assertIn("riggrade-deployer", doc)

    def test_settings_file_holds_no_secret_and_is_guarded(self):
        env = (ROOT / "deploy" / "gcp.env").read_text()
        keys = {line.split("=", 1)[0] for line in env.splitlines()
                if line and not line.startswith("#")}
        self.assertEqual(keys, {"GCP_PROJECT", "GCP_PROJECT_NUMBER", "GCP_REGION"})
        self.assertIn("grep -q REPLACE_ME deploy/gcp.env", self.text)


class PublishBenchWorkflowTests(unittest.TestCase):
    def test_manual_main_only_and_bench_container_only(self):
        text = (ROOT / ".github" / "workflows" / "publish-bench.yml").read_text()
        on_block = text.split("on:", 1)[1].split("permissions:", 1)[0]
        self.assertEqual(on_block.split(), ["workflow_dispatch:"])
        self.assertIn("if: github.ref == 'refs/heads/main'", text)
        self.assertIn("-f container/Dockerfile", text)
        self.assertNotIn("server.Dockerfile", text)
        docker = (ROOT / "container" / "Dockerfile").read_text()
        self.assertIn("COPY riggrade/__init__.py riggrade/__init__.py", docker)
        self.assertNotIn("COPY riggrade/ ", docker)

    def test_runner_needs_only_the_version_from_riggrade(self):
        import re
        for path in (ROOT / "runner").glob("*.py"):
            if path.name == "smoke.py":
                continue  # runs from source, with the server in-process
            for line in path.read_text().splitlines():
                if re.match(r"\s*(from|import) riggrade", line):
                    self.assertEqual(line.strip(), "from riggrade import BENCH_VERSION", path)
class SaleSwitchDeployTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()

    def test_screen_list_is_built_fresh_and_guarded(self):
        self.assertIn("exports/SDN_ADVANCED.XML", self.text)
        self.assertIn('if [ "$COUNT" -lt 100 ]', self.text)
        self.assertLess(self.text.index("Build the payer screen list"),
                        self.text.index("Build and push the server image"))
        docker = (ROOT / "deploy" / "server.Dockerfile").read_text()
        self.assertIn("COPY deploy/screen/blocked-addresses.txt", docker)

    def test_switch_and_adapter_env_but_no_wallet_or_secret_in_repo(self):
        self.assertIn("RIGGRADE_ENABLED=1", self.text)
        self.assertIn("RIGGRADE_FACILITATORS=https://x402.dexter.cash,"
                      "https://facilitator.payai.network", self.text)
        self.assertIn("--update-env-vars", self.text)
        self.assertNotIn("--set-env-vars", self.text)  # would wipe owner-set values
        self.assertNotIn("PAYOUT_WALLET_ADDRESS", self.text)
        self.assertNotIn("RIGGRADE_SECRET", self.text)

    def test_screen_path_matches_the_image(self):
        self.assertIn("RIGGRADE_SCREEN_LIST=/opt/riggrade/screen/blocked-addresses.txt",
                      self.text)


class BenchDigestTests(unittest.TestCase):
    def test_digest_file_is_valid_and_wired_into_the_deploy(self):
        import re
        env = (ROOT / "deploy" / "bench-image.env").read_text()
        digests = re.search(r"^RIGGRADE_IMAGE_DIGESTS=(.+)$", env, re.M).group(1)
        for d in digests.split(","):
            self.assertRegex(d, r"^sha256:[0-9a-f]{64}$")
        image = re.search(r"^BENCH_IMAGE=(.+)$", env, re.M).group(1)
        self.assertTrue(image.startswith("ghcr.io/nnyycc77-ship-it/riggrade-bench@"))
        self.assertIn(image.split("@")[1], digests)
        workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
        self.assertIn("deploy/bench-image.env", workflow)
        self.assertIn("RIGGRADE_IMAGE_DIGESTS=$RIGGRADE_IMAGE_DIGESTS", workflow)
        readme = (ROOT / "README.md").read_text()
        self.assertIn("riggrade-bench", readme)
        self.assertNotIn("riggrade-bench:latest", readme)
