"""Run the grading endpoint from the environment.

    python3 -m riggrade.serve [--host 127.0.0.1] [--port 8402]

or point any WSGI host at riggrade.serve:application.

The payment adapter is built only when RIGGRADE_FACILITATORS and
RIGGRADE_SCREEN_LIST are both set (riggrade.payment.from_env). The sale is
on only when that adapter exists AND every switch in riggrade.endpoint is
set; otherwise every grade request answers 503 and charges nothing. The
descriptor at GET /riggrade/v1 lists the reasons the sale is off.

Sessions and references are held in memory in this version, so a restart
loses open sessions. The sale log is the durable record.
"""

import argparse
import os
import sys
import time

from . import payment
from .endpoint import GradingApp


def build(env=None, clock=None):
    env = dict(os.environ if env is None else env)
    return GradingApp(env, clock or (lambda: int(time.time())),
                      adapter=payment.from_env(env))


def main(argv=None):
    ap = argparse.ArgumentParser(description="RigGrade grading endpoint")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8402)
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
