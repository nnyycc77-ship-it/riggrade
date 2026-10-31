"""Drives a grading endpoint in-process, with the same calls runner.client
makes over HTTP. Used by the smoke test and the CPU tests, so a full
session can run with no network and no deployed endpoint."""

import io
import json


class InProcessClient:
    def __init__(self, app, base="http://local/riggrade/v1"):
        self.app = app
        self.base = base

    def _post(self, path, body=None):
        data = json.dumps(body or {}).encode()
        env = {"REQUEST_METHOD": "POST", "PATH_INFO": "/riggrade/v1" + path,
               "CONTENT_LENGTH": str(len(data)), "wsgi.input": io.BytesIO(data)}
        got = {}

        def start_response(status, headers):
            got["status"] = status
        out = json.loads(b"".join(self.app(env, start_response)))
        out["_status"] = got["status"]
        return out

    def new_session(self, workloads, declared_runner, host_wallet=None):
        body = {"workload_classes": workloads, "declared_runner": declared_runner}
        if host_wallet:
            body["host_wallet"] = host_wallet
        return self._post("/session", body)

    def start(self, session_id):
        return self._post("/session/%s/start" % session_id)

    def submit(self, session_id, bundle):
        return self._post("/session/%s/bundle" % session_id, bundle)
