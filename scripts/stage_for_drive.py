"""List the files the free GPU notebook needs, with their checksums, for
staging in Google Drive (notebooks/smoke_test_drive.ipynb).

    python3 scripts/stage_for_drive.py > MANIFEST.json

Drive keeps no folders inside one upload, so each file gets a flat
`drive_name` (path with "/" replaced by "__"). The checksum is SHA-256 of
the text with trailing newlines normalised to exactly one, so a copy that
gained or lost a final newline in transit still verifies, and any other
change does not.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "riggrade/__init__.py", "riggrade/classes.py", "riggrade/endpoint.py",
    "riggrade/engine.py", "riggrade/grading.py", "riggrade/heartbeat.py",
    "riggrade/label.py", "riggrade/salelog.py", "riggrade/session.py",
    "runner/__init__.py", "runner/bundle.py", "runner/gpu.py", "runner/identity.py",
    "runner/local.py", "runner/preflight.py", "runner/rulers.py", "runner/smoke.py",
    "runner/stats.py", "spec/param-distribution.json",
]


def normalised_sha256(text):
    return hashlib.sha256((text.rstrip("\n") + "\n").encode("utf-8")).hexdigest()


def manifest():
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    return {"commit": commit,
            "files": [{"path": f, "drive_name": f.replace("/", "__"),
                       "sha256": normalised_sha256((ROOT / f).read_text(encoding="utf-8"))}
                      for f in FILES]}


if __name__ == "__main__":
    json.dump(manifest(), sys.stdout, indent=1)
    sys.stdout.write("\n")
