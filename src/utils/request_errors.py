"""Helpers for rendering ``requests`` exceptions without leaking credentials."""

from __future__ import annotations

import requests


def scrub_request_error(error: requests.RequestException) -> str:
    """Render a ``requests`` exception without leaking the request URL.

    The default ``str()`` of a ``requests.HTTPError`` (and other request
    exceptions) embeds the full request URL. When a source or enrichment
    provider passes its API key as a query parameter (``?api_key=<secret>``
    / ``?key=<secret>``), that key ends up in the exception message — and
    from there in sync/enrichment error fields the web API and CLI surface
    to users and logs.

    This deliberately omits the URL and the raw exception message. It
    returns only the HTTP status code for HTTP errors and the bare
    exception class name for transport errors (connection, timeout, etc.),
    neither of which can carry the credential.

    Args:
        error: The ``requests`` exception to render.

    Returns:
        ``"HTTP <status>"`` for an HTTP error with a response, otherwise the
        exception class name (e.g. ``"ConnectionError"``).
    """
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f"HTTP {error.response.status_code}"
    return type(error).__name__
