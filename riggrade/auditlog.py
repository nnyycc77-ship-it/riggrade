"""Server copy of sample-audit lines (docs/sample-audit-method.md, owner
direction 3 Nov 2026).

Each audited box posts the same anonymised summary it prints as its RGAUDIT
line, tagged with the run id and the box label from the run sheet. The
summary passes through the preflight pool's cleaner (riggrade.pool.clean),
so only the listed fields are kept: no host name, address, card id, serial or
free text. The log is append-only, never served by the service, and read only
by the operators (riggrade-ops audit-read workflow). It is closed unless
RIGGRADE_AUDIT_LOG names the log.
"""

import json
import re

from riggrade import pool

RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{5,47}$")
BOX = re.compile(r"^b\d{1,3}$")
MAX_LINES_PER_RUN = 200


def clean(body, now):
    """The stored entry for one box, or ValueError."""
    if not isinstance(body, dict):
        raise ValueError("body must be an object with run, box and line")  # noqa: TRY004 - the route answers 400
    run, box = body.get("run"), body.get("box")
    if not isinstance(run, str) or not RUN_ID.match(run):
        raise ValueError("run must be the run id: 6 to 48 of a-z, 0-9 and '-'")
    if not isinstance(box, str) or not BOX.match(box):
        raise ValueError("box must be the run sheet label, e.g. b10")
    line = pool.clean(body.get("line"), int(now) // 3600 * 3600)
    line.pop("at_hour", None)
    return {"at": int(now), "run": run, "box": box, "line": line}


def append(path, entry):
    """Append unless the run is at its cap. Returns (stored, copies of this
    box in the run after storing)."""
    rows = [r for r in pool.read(path) if r.get("run") == entry["run"]]
    if len(rows) >= MAX_LINES_PER_RUN:
        return False, sum(1 for r in rows if r.get("box") == entry["box"])
    pool.append(path, entry)
    return True, 1 + sum(1 for r in rows if r.get("box") == entry["box"])


def rgaudit_lines(rows, run):
    """The run's lines in the form runner.sample_audit reads. Where a box
    posted more than once, the first copy stands and the count is returned."""
    first, extra = {}, {}
    for r in sorted((r for r in rows if r.get("run") == run), key=lambda r: r.get("at", 0)):
        if r["box"] in first:
            extra[r["box"]] = extra.get(r["box"], 0) + 1
            continue
        first[r["box"]] = r
    text = "".join(f"RGAUDIT {box} {json.dumps(r['line'], sort_keys=True)}\n" for box, r in first.items())
    return text, extra
