"""Write docs/sample-certificate.html and .json from the example certificate
(no real secret; the signature shown is a placeholder)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from riggrade import certificate_page  # noqa: E402


def main():
    docs = Path(__file__).resolve().parents[1] / "docs"
    cert = certificate_page.sample()
    (docs / "sample-certificate.json").write_text(json.dumps(cert, indent=1, sort_keys=True) + "\n")
    (docs / "sample-certificate.html").write_text(certificate_page.render(
        cert, True, "https://riggrade-674098696181.us-central1.run.app/riggrade/v1/cluster/acceptance/rc_sample0000000000?format=json"))
    print("wrote docs/sample-certificate.{html,json}")


if __name__ == "__main__":
    main()
