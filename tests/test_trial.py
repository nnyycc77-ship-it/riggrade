"""Free trial for the fix-it report and the point grade (owner decision 2 Nov
2026, set only once the coordinator records his confirmation): 0 USDC
authorisations, verified and never settled, logged free_trial; trial grades
never add to the reference set; prices return on their own after the date."""

import copy

from riggrade import salelog
from riggrade.endpoint import GradingApp
from tests.test_endpoint import FakeAdapter, Harness, _with_references, call

FIXIT_BODY = {"preflight_report": {"preflight": True, "findings": [
    {"check": "pcie_link", "status": "warn", "detail": "x8 at gen3"}]}}
UNTIL = "2027-01-15"            # the test clock reads 2027-01-15 04:00 UTC
AFTER = 1_800_000_000 + 2 * 86400


class TrialTests(Harness):
    def app(self, trial="fixit,grade", until=UNTIL, products="fixit"):
        env = dict(self.env, RIGGRADE_PRODUCTS=products)
        if trial is not None:
            env["RIGGRADE_TRIAL_PRODUCTS"] = trial
        if until is not None:
            env["RIGGRADE_TRIAL_UNTIL"] = until
        self.adapter = FakeAdapter()
        self.book = _with_references()
        return GradingApp(env, self.clock, adapter=self.adapter, references=self.book)

    def grade(self, app, payment=None):
        sid, status, _b = self.run_session(app)
        self.assertEqual(status, "200 OK")
        return sid, call(app, "POST", f"/riggrade/v1/session/{sid}/grade", payment=payment)

    def test_off_by_default_prices_stand(self):
        app = self.app(trial=None, until=None)
        _s, _h, doc = call(app, "POST", "/riggrade/v1/fixit", FIXIT_BODY)
        self.assertEqual(doc["accepts"][0]["amount"], "2000000")
        _sid, (status, _h, doc) = self.grade(app)
        self.assertEqual((status, doc["accepts"][0]["amount"]), ("402 Payment Required", "3000000"))
        _s, _h, desc = call(app, "GET", "/riggrade/v1")
        self.assertNotIn("point_grade_free_trial", desc)
        self.assertNotIn("trial", desc["products"]["fixit"])

    def test_fixit_free_on_a_verified_zero_authorisation(self):
        app = self.app()
        _s, _h, desc = call(app, "GET", "/riggrade/v1")
        self.assertEqual((desc["products"]["fixit"]["price_usdc"], desc["products"]["fixit"]["free_until"]),
                         ("0", UNTIL))
        self.assertIn("marked free_trial", desc["products"]["fixit"]["trial"])
        status, _h, doc = call(app, "POST", "/riggrade/v1/fixit", FIXIT_BODY)
        self.assertEqual((status, doc["accepts"][0]["amount"]), ("402 Payment Required", "0"))
        self.assertIn("Free trial until 2027-01-15", doc["resource"]["description"])
        status, _h, doc = call(app, "POST", "/riggrade/v1/fixit", FIXIT_BODY, payment="pay")
        self.assertEqual(status, "200 OK", doc)
        self.assertEqual([c[0] for c in self.adapter.calls], ["verify"])       # never settled
        (entry,) = salelog.read_chain(self.log)
        self.assertEqual((entry["amount_atomic"], entry["status"]), ("0", "free_trial"))

    def test_trial_grade_is_free_marked_and_never_feeds_the_reference(self):
        app = self.app()
        before = copy.deepcopy(self.book.__dict__)
        _s, _h, desc = call(app, "GET", "/riggrade/v1")
        self.assertEqual(desc["point_grade_free_trial"]["until"], UNTIL)
        self.assertIn("not added to the reference set", desc["point_grade_free_trial"]["note"])
        _sid, (status, _h, doc) = self.grade(app)
        self.assertEqual((status, doc["accepts"][0]["amount"]), ("402 Payment Required", "0"))
        self.assertIn("never settled", doc["resource"]["description"])
        _sid, (status, _h, doc) = self.grade(app, payment="pay")
        self.assertEqual(status, "200 OK", doc)
        self.assertTrue(doc["evidence_pack"]["free_trial"])
        self.assertEqual({c[0] for c in self.adapter.calls}, {"verify"})       # never settled
        (entry,) = salelog.read_chain(self.log)
        self.assertEqual((entry["amount_atomic"], entry["status"]), ("0", "free_trial"))
        self.assertEqual(self.book.__dict__, before)                          # reference set untouched
        status, _h, doc = call(app, "POST", "/riggrade/v1/grade")
        self.assertEqual(doc["accepts"][0]["amount"], "0")                     # the bare probe says free too

    def test_a_paid_grade_still_feeds_the_reference_outside_the_trial(self):
        app = self.app(trial="fixit")
        before = copy.deepcopy(self.book.__dict__)
        _sid, (status, _h, doc) = self.grade(app, payment="pay")
        self.assertEqual(status, "200 OK", doc)
        self.assertNotIn("free_trial", doc["evidence_pack"])
        self.assertNotEqual(self.book.__dict__, before)

    def test_prices_return_after_the_end_date(self):
        app = self.app()
        self.clock.t = AFTER
        _s, _h, doc = call(app, "POST", "/riggrade/v1/fixit", FIXIT_BODY)
        self.assertEqual(doc["accepts"][0]["amount"], "2000000")
        _sid, (_st, _h, doc) = self.grade(app)
        self.assertEqual(doc["accepts"][0]["amount"], "3000000")
        _s, _h, desc = call(app, "GET", "/riggrade/v1")
        self.assertNotIn("point_grade_free_trial", desc)

    def test_only_eligible_products_and_a_valid_date_open_a_trial(self):
        app = self.app(trial="cluster,ledger", products="fixit,cluster")
        _s, _h, desc = call(app, "GET", "/riggrade/v1")
        self.assertEqual(desc["products"]["cluster"]["price_usdc"], "25")
        self.assertEqual(desc["products"]["fixit"]["price_usdc"], "2")
        app = self.app(until="31/12/2026")
        _s, _h, desc = call(app, "GET", "/riggrade/v1")
        self.assertEqual(desc["products"]["fixit"]["price_usdc"], "2")
        self.assertNotIn("point_grade_free_trial", desc)


class FreeNoWalletTests(Harness):
    """The no-wallet fix-it path for people (owner decision, 4 Nov 2026)."""
    app = TrialTests.app

    def test_free_report_with_no_wallet_during_the_trial(self):
        app = self.app()
        status, _h, doc = call(app, "POST", "/riggrade/v1/fixit/free", FIXIT_BODY)
        self.assertEqual(status, "200 OK", doc)
        self.assertEqual((doc["charged"], doc["report"]["free_trial"], doc["report"]["paying_wallet"]),
                         (False, True, None))
        self.assertEqual(doc["free_until"], UNTIL)
        self.assertEqual(self.adapter.calls, [])                       # no facilitator touched
        (entry,) = salelog.read_chain(self.log)
        self.assertEqual((entry["status"], entry["via"], entry["amount_atomic"], entry["payer"]),
                         ("free_trial", "web_no_wallet", "0", None))

    def test_closed_outside_the_trial_and_when_off(self):
        for app in (self.app(trial=None, until=None), self.app(products="cluster")):
            status, _h, doc = call(app, "POST", "/riggrade/v1/fixit/free", FIXIT_BODY)
            self.assertEqual(status, "403 Forbidden")
            self.assertFalse(doc["charged"])
        app = self.app()
        self.clock.t = AFTER
        status, _h, _doc = call(app, "POST", "/riggrade/v1/fixit/free", FIXIT_BODY)
        self.assertEqual(status, "403 Forbidden")
        self.assertEqual(salelog.read_chain(self.log), [])

    def test_bad_or_clean_reports_are_refused(self):
        app = self.app()
        status, _h, _doc = call(app, "POST", "/riggrade/v1/fixit/free", {"preflight_report": {"findings": []}})
        self.assertEqual(status, "400 Bad Request")
        clean = copy.deepcopy(FIXIT_BODY)
        clean["preflight_report"]["findings"][0]["status"] = "pass"
        status, _h, _doc = call(app, "POST", "/riggrade/v1/fixit/free", clean)
        self.assertEqual(status, "422 Unprocessable Entity")
        self.assertEqual(salelog.read_chain(self.log), [])

    def test_not_listed_for_agents(self):
        _s, _h, doc = call(self.app(), "GET", "/openapi.json")
        self.assertNotIn("/fixit/free", doc["paths"])


class QuoteEventTests(Harness):
    """Real quotes are logged; empty discovery probes are not (owner request, 4 Nov 2026)."""
    app = TrialTests.app

    def test_a_valid_file_logs_a_quote_and_a_probe_does_not(self):
        app = self.app(trial=None, until=None)
        call(app, "GET", "/riggrade/v1/fixit")
        call(app, "POST", "/riggrade/v1/fixit")
        self.assertEqual(app.events, [])
        status, _h, _doc = call(app, "POST", "/riggrade/v1/fixit", FIXIT_BODY)
        self.assertEqual(status, "402 Payment Required")
        (event,) = app.events
        self.assertEqual((event["riggrade_event"], event["product"], event["amount"], event["retry"]),
                         ("quote", "fixit", "2000000", False))
        self.assertNotIn("payer", event)

    def test_a_grade_session_quote_is_logged(self):
        app = self.app(trial=None, until=None)
        _sid, (status, _h, _doc) = TrialTests.grade(self, app)
        self.assertEqual(status, "402 Payment Required")
        self.assertEqual([e["product"] for e in app.events], ["grade"])


class GradeNoWalletTests(Harness):
    """The no-wallet grade for people (owner decision, 5 Nov 2026)."""
    app = TrialTests.app

    def free(self, app, sid):
        return call(app, "POST", f"/riggrade/v1/session/{sid}/grade/free")

    def test_graded_free_with_no_wallet_and_never_feeds_the_reference(self):
        app = self.app()
        before = copy.deepcopy(self.book.__dict__)
        sid, status, _b = self.run_session(app)
        self.assertEqual(status, "200 OK")
        status, _h, doc = self.free(app, sid)
        self.assertEqual(status, "200 OK", doc)
        pack = doc["evidence_pack"]
        self.assertEqual((doc["charged"], pack["free_trial"], pack["no_wallet"], pack["paying_wallet"],
                          pack["operator"]), (False, True, True, None, None))
        self.assertTrue(doc["labels"])
        self.assertEqual(self.adapter.calls, [])                       # no facilitator touched
        self.assertEqual(self.book.__dict__, before)                   # reference set untouched
        (entry,) = salelog.read_chain(self.log)
        self.assertEqual((entry["status"], entry["via"], entry["amount_atomic"], entry["payer"]),
                         ("free_trial", "web_no_wallet", "0", None))
        status, _h, _doc = self.free(app, sid)
        self.assertEqual(status, "409 Conflict")                       # graded once only

    def test_closed_outside_the_trial(self):
        for app in (self.app(trial=None, until=None), self.app(trial="fixit")):
            sid, _s, _b = self.run_session(app)
            status, _h, doc = self.free(app, sid)
            self.assertEqual(status, "403 Forbidden")
            self.assertFalse(doc["charged"])
        app = self.app()
        sid, _s, _b = self.run_session(app)
        self.clock.t = AFTER
        status, _h, _doc = self.free(app, sid)
        self.assertEqual(status, "403 Forbidden")
        self.assertEqual(salelog.read_chain(self.log), [])

    def test_unknown_or_unsubmitted_session(self):
        status, _h, doc = self.free(self.app(), "rg_s_unknown")
        self.assertEqual(status, "404 Not Found")
        self.assertEqual(doc["error"], "unknown_session")
        self.assertFalse(doc["charged"])

    def test_a_building_class_keeps_the_rigs_free_reference_session(self):
        # Empty reference book: the class is still building. A no-wallet grade
        # leaves the rig's first, reference-setting session unused.
        env = dict(self.env, RIGGRADE_TRIAL_PRODUCTS="grade", RIGGRADE_TRIAL_UNTIL=UNTIL)
        app = GradingApp(env, self.clock, adapter=FakeAdapter())
        rig = "rg_" + "7" * 16
        sid, _s, _b = self.run_session(app, rig_id=rig)
        status, _h, doc = self.free(app, sid)
        self.assertEqual(status, "200 OK", doc)
        self.assertEqual(app.references.basis("rtx4090-24", "inference")["rigs"], 0)
        sid, _s, _b = self.run_session(app, rig_id=rig)
        status, _h, doc = call(app, "POST", f"/riggrade/v1/session/{sid}/grade", payment="pay:0xop")
        self.assertEqual(status, "200 OK", doc)
        self.assertTrue(doc["free_reference_session"])

    def test_not_listed_for_agents(self):
        _s, _h, doc = call(self.app(), "GET", "/openapi.json")
        self.assertFalse([p for p in doc["paths"] if p.endswith("/grade/free")])


class GradeLinkTests(Harness):
    """The grade URL the container prints opens a ready checkout in a browser."""
    app = TrialTests.app

    def open(self, app, path, accept):
        got = {}

        def start_response(status, headers):
            got["status"], got["headers"] = status, dict(headers)
        b"".join(app({"REQUEST_METHOD": "GET", "PATH_INFO": path, "HTTP_ACCEPT": accept,
                      "wsgi.input": __import__("io").BytesIO(b"")}, start_response))
        return got["status"], got["headers"]

    def test_browser_lands_on_the_checkout_with_the_session_filled_in(self):
        app = self.app()
        status, headers = self.open(app, "/riggrade/v1/session/rg_s_abc123/grade",
                                    "text/html,application/xhtml+xml,*/*;q=0.8")
        self.assertEqual((status, headers["Location"]),
                         ("302 Found", "/riggrade/v1/checkout?product=grade&session_id=rg_s_abc123"))
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_agents_and_odd_ids_are_not_redirected(self):
        app = self.app()
        status, _h = self.open(app, "/riggrade/v1/session/rg_s_abc123/grade", "application/json")
        self.assertNotEqual(status, "302 Found")
        status, _h = self.open(app, "/riggrade/v1/session/a<b>c/grade", "text/html")
        self.assertNotEqual(status, "302 Found")
