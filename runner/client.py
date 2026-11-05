"""Session client for the grading endpoint (spec section 6), stdlib only."""

import json
import urllib.request


class EndpointError(RuntimeError):
    pass


class Client:
    def __init__(self, base_url, opener=urllib.request.urlopen, timeout=60):
        self.base = base_url.rstrip("/") + "/riggrade/v1"
        self.opener = opener
        self.timeout = timeout

    def _post(self, path, body=None):
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            try:
                detail = json.loads(payload)
            except ValueError:
                detail = payload.decode(errors="replace")
            raise EndpointError("%s %s: %s" % (exc.code, path, detail))

    def new_session(self, workloads, declared_runner, host_wallet=None):
        body = {"workload_classes": workloads, "declared_runner": declared_runner}
        if host_wallet:
            body["host_wallet"] = host_wallet
        return self._post("/session", body)

    def start(self, session_id):
        return self._post("/session/%s/start" % session_id)

    def submit(self, session_id, bundle):
        return self._post("/session/%s/bundle" % session_id, bundle)

    def grade_free(self, session_id):
        return self._post(f"/session/{session_id}/grade/free")
