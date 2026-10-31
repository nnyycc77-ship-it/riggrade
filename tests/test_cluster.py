import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from runner import cluster as cl
from tests.test_preflight import FakeProbes

CLAIMS = {"gpu_model": "NVIDIA H100 80GB HBM3", "gpus_per_node": 8, "nodes": 4,
          "inter_node_gbps_per_gpu": 400, "storage_read_gbs": 10}


def node(rank, tflops=700.0, verdict="pass", gpus=8, model="NVIDIA H100 80GB HBM3",
         busbw=45.0, storage=3.0):
    n = {"rank": rank,
         "identity": {"device_name": model, "card_count": gpus, "rig_id": "r%d" % rank},
         "preflight": {"verdict": verdict, "problems": []},
         "tflops_per_gpu": {str(i): tflops for i in range(gpus)},
         "storage_read_gbs": storage}
    if busbw is not None:
        n["fabric"] = {"busbw_gbs": busbw, "world": 32}
    return n


def status(checks):
    return {c["check"]: c["status"] for c in checks}


class EvaluateTests(unittest.TestCase):
    def test_healthy_cluster_is_accepted(self):
        checks, v = cl.evaluate(CLAIMS, [node(r) for r in range(4)])
        self.assertEqual(v["verdict"], "accept")
        fabric = [c for c in checks if c["check"] == "fabric"][0]
        self.assertEqual(fabric["ratio"], 0.9)  # 45 GB/s over 400 Gb/s = 50 GB/s

    def test_missing_node_and_wrong_model_reject(self):
        checks, v = cl.evaluate(CLAIMS, [node(0), node(1), node(2, model="NVIDIA H100 PCIe")])
        self.assertEqual(status(checks)["inventory"], cl.REJECT)
        self.assertEqual(v["verdict"], "reject")

    def test_straggler_bands(self):
        nodes = [node(0), node(1), node(2), node(3, tflops=600.0)]  # 600/700 = 0.857
        checks, v = cl.evaluate(CLAIMS, nodes)
        self.assertEqual(status(checks)["straggler"], cl.EXCEPTION)
        self.assertEqual(v["verdict"], "accept with exceptions")
        self.assertIn("straggler", v["exceptions"])
        nodes[3] = node(3, tflops=500.0)
        self.assertEqual(status(cl.evaluate(CLAIMS, nodes)[0])["straggler"], cl.REJECT)

    def test_fabric_and_storage_against_claims(self):
        nodes = [node(r, busbw=25.0, storage=1.0) for r in range(4)]
        s = status(cl.evaluate(CLAIMS, nodes)[0])
        self.assertEqual(s["fabric"], cl.REJECT)     # 25/50 = 0.5
        self.assertEqual(s["storage"], cl.REJECT)    # 4/10 = 0.4

    def test_unclaimed_is_not_tested_and_claimed_unmeasured_is_an_exception(self):
        claims = {"gpus_per_node": 8, "nodes": 2}
        checks, v = cl.evaluate(claims, [node(0, storage=None), node(1, storage=None)])
        s = status(checks)
        self.assertEqual((s["fabric"], s["storage"]), (cl.NO_CLAIM, cl.NO_CLAIM))
        self.assertEqual(v["verdict"], "accept")
        nodes = [node(r, busbw=None) for r in range(4)]
        checks, v = cl.evaluate(CLAIMS, nodes)
        self.assertEqual(status(checks)["fabric"], cl.NOT_MEASURED)
        self.assertEqual(v["verdict"], "accept with exceptions")
        self.assertIn("fabric", v["exceptions"])

    def test_node_health_and_single_node(self):
        checks, v = cl.evaluate({"nodes": 1}, [node(0, verdict="fail", busbw=None)])
        s = status(checks)
        self.assertEqual(s["node_health"], cl.REJECT)
        self.assertEqual(s["fabric"], cl.NO_CLAIM)
        self.assertEqual(s["straggler"], cl.NOT_MEASURED)


class PackAndNodeTests(unittest.TestCase):
    def test_evidence_pack_records_input_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "claims.json").write_text(json.dumps(CLAIMS))
            paths = []
            for r in range(4):
                p = t / ("node-%d.json" % r)
                p.write_text(json.dumps(node(r)))
                paths.append(str(p))
            pack = cl.evidence_pack(str(t / "claims.json"), paths)
            self.assertEqual(pack["verdict"], "accept")
            self.assertEqual(len(pack["inputs"]), 5)
            self.assertEqual(pack["inputs"][paths[0]], cl.sha256_file(paths[0]))
            out = t / "acceptance.json"
            self.assertEqual(cl.main(["evaluate", "--claims", str(t / "claims.json"),
                                      "--out", str(out)] + paths), 0)
            self.assertEqual(json.loads(out.read_text())["verdict"], "accept")

    def test_run_node_with_fakes_records_fabric_failure(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                def boom(rank, nodes, master):
                    raise RuntimeError("NCCL timeout")
                report = cl.run_node(0, 2, "10.0.0.1:29500", storage=tmp,
                                     preflight_probes=FakeProbes(), fabric_fn=boom,
                                     storage_fn=lambda p: 2.5,
                                     identity_fn=lambda: {"device_name": "X", "card_count": 1})
            finally:
                os.chdir(cwd)
        self.assertEqual(report["preflight"]["verdict"], "pass")
        self.assertIsNone(report["fabric"]["busbw_gbs"])
        self.assertIn("NCCL timeout", report["fabric"]["error"])
        self.assertEqual(report["storage_read_gbs"], 2.5)

    def test_storage_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.bin").write_bytes(b"x" * (3 << 20))
            self.assertGreater(cl.measure_storage(tmp), 0)
            empty = Path(tmp, "empty")
            empty.mkdir()
            self.assertIsNone(cl.measure_storage(empty))

    def test_texts_are_promise_free(self):
        from riggrade.label import lint
        self.assertEqual(lint([cl.__doc__, open("docs/cluster-acceptance.md").read()]), [])


if __name__ == "__main__":
    unittest.main()
