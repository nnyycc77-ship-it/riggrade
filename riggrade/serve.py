"""Run the grading endpoint from the environment.

    python3 -m riggrade.serve [--host 127.0.0.1] [--port 8402]

or point any WSGI host at riggrade.serve:application.

The payment adapter is built only when RIGGRADE_FACILITATORS and
RIGGRADE_SCREEN_LIST are both set (riggrade.payment.from_env). The sale is
on only when that adapter exists AND every switch in riggrade.endpoint is
set; otherwise every grade request answers 503 and charges nothing. The
descriptor at GET /riggrade/v1 lists the reasons the sale is off.

State. With RIGGRADE_STATE_DIR set (on Cloud Run, a mounted Cloud Storage
bucket), sessions and heartbeats go to <dir>/sessions/, class references
to <dir>/references.json, and the sale log defaults to <dir>/sales.jsonl,
so a restart or a new instance loses nothing. Run one instance only: the
files assume a single writer. Without it, state is in memory and a restart
loses open sessions and references.

PORT, when set (Cloud Run sets it), is the default port, and the host then
defaults to 0.0.0.0.
"""

import argparse
import os
import sys
import time
from pathlib import Path

from . import payment
from .endpoint import GradingApp
from .grading import ReferenceBook
from .session import FileStore


def build(env=None, clock=None):
    env = dict(os.environ if env is None else env)
    store = references = None
    state = env.get("RIGGRADE_STATE_DIR", "")
    if state:
        state_dir = Path(state)
        store = FileStore(state_dir / "sessions")
        references = ReferenceBook(state_dir / "references.json")
        env.setdefault("RIGGRADE_SALE_LOG", str(state_dir / "sales.jsonl"))
    return GradingApp(env, clock or (lambda: int(time.time())),
                      adapter=payment.from_env(env), store=store, references=references)


def main(argv=None):
    ap = argparse.ArgumentParser(description="RigGrade grading endpoint")
    port = os.environ.get("PORT")
    ap.add_argument("--host", default="0.0.0.0" if port else "127.0.0.1")
    ap.add_argument("--port", type=int, default=int(port or 8402))
    args = ap.parse_args(argv)
    from wsgiref.simple_server import make_server
    app = build()
    state = "ON SALE" if app.on_sale else "sale off: " + "; ".join(app.config.reasons_off(app.adapter))
    print("RigGrade endpoint on http://%s:%d/riggrade/v1 (%s)" % (args.host, args.port, state))
    make_server(args.host, args.port, app).serve_forever()
    return 0


def application(environ, start_response):
    global _APP
    if _APP is None:
        _APP = build()
    return _APP(environ, start_response)


_APP = None

if __name__ == "__main__":
    sys.exit(main())
