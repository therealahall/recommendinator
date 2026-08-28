"""Container liveness probe, run by the image's ``HEALTHCHECK``."""

from __future__ import annotations

import urllib.error
import urllib.request

# The port is fixed by the image's CMD rather than by config.yaml.
STATUS_URL = "http://localhost:8000/api/status"

# A 200 would mean the auth dependency came off the router, which is a defect
# rather than health; anything else is a server that is not serving.
HEALTHY_STATUS = 401

_TIMEOUT_SECONDS = 5


def probe(url: str = STATUS_URL) -> int:
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as answer:
            code: int = answer.status
    except urllib.error.HTTPError as refusal:
        code = refusal.code
        refusal.close()
    except OSError:
        return 1
    return 0 if code == HEALTHY_STATUS else 1


if __name__ == "__main__":
    raise SystemExit(probe())
