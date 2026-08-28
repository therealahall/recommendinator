from __future__ import annotations

import requests


def scrub_request_error(error: requests.RequestException) -> str:
    """Raise ``from None`` when the credential is in the URL or query string;
    keep ``from error`` when it travels in a header, where the URL is
    clean and the chain still aids debugging.
    """
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f"HTTP {error.response.status_code}"
    return type(error).__name__
