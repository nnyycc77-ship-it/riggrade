import json
import unittest

from riggrade.engine import grade_session
from riggrade.grading import ReferenceBook
from riggrade.label import lint
from samples import make_samples as ms


class SampleTests(unittest.TestCase):
    def test_committed_samples_match_the_engine(self):
        """The committed sample files must be exactly what the engine
        produces now, so a rule change cannot leave stale samples."""
        samples = ms.build()
        committed = json.loads(ms.JSON_OUT.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(json.dumps(samples, sort_keys=True)), committed)
        self.assertEqual(ms.render(samples), ms.DOC_OUT.read_text(encoding="utf-8"))

    def test_sample_shows_cap_and_watch_and_is_marked(self):
        by_rig = {s["rig"]: s for s in ms.build()}
        self.assertEqual(by_rig["A"]["label"]["grade"], 1)
        self.assertIsNone(by_rig["A"]["label"]["capped_by"])
        self.assertEqual(by_rig["B"]["label"]["capped_by"], "consistency")
        self.assertEqual(by_rig["C"]["label"]["status"], "on_watch")
        self.assertEqual(by_rig["C"]["label"]["grade"], 1)
        doc = ms.DOC_OUT.read_text(encoding="utf-8")
        self.assertIn(ms.SAMPLE_MARK, doc)
        self.assertTrue(all(s["label"]["sample"] == ms.SAMPLE_MARK for s in by_rig.values()))
        self.assertEqual(lint([doc]), [])


class LifecycleTests(unittest.TestCase):
    def session(self, passes):
        return {"session_id": "s", "nonce": "n", "workloads": ["training"],
                "declared_runner": "host", "started_at": 0, "params": {},
                "bundle": {"identity": {"device_name": "NVIDIA A100-SXM4-80GB",
                                        "memory_gb": 79.2, "card_count": 1,
                                        "rig_id": "r"},
                           "gate": {"training": {"results": ms.GATE_OK,
                                                 "free_memory_gb": 70}},
                           "metrics": {"training": {"throughput_passes": passes,
                                                    "decay": 0.0}},
                           "outputs": {}}}

    def test_second_bad_week_demotes(self):
        book = ReferenceBook()
        book.set_controlled("a100-sxm-80", "training", 1000.0)
        labels, _ = grade_session(self.session([910, 909, 911]), book, attested=True,
                                  held={"training": {"grade": 1, "on_watch": True}},
                                  now=10, record_reference=False)
        self.assertEqual(labels[0]["status"], "demoted")
        self.assertEqual(labels[0]["grade"], 2)

    def test_clean_week_affirms(self):
        book = ReferenceBook()
        book.set_controlled("a100-sxm-80", "training", 1000.0)
        labels, _ = grade_session(self.session([990, 991, 989]), book, attested=True,
                                  held={"training": 1}, now=10, record_reference=False)
        self.assertEqual(labels[0]["status"], "affirmed")


if __name__ == "__main__":
    unittest.main()
