"""Shape checks for the URLs config exposes over the network.

``web.allowed_origins`` is settable by one request and must be a bare origin,
so it reads that grammar from here.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# The only schemes a browser puts in an Origin header for a page that could
# reach this app.
_ORIGIN_SCHEMES = frozenset({"http", "https"})

# ``urlsplit`` drops tab, CR and LF before it reads the host, and IDNA splits
# labels on three dots ``str`` does not, so an unnormalised value is checked
# under a different name from the one dialled.
_ONE_SPELLING = str.maketrans(
    {"\t": None, "\r": None, "\n": None, "。": ".", "．": ".", "｡": "."}
)


def normalize_origin(value: str) -> str:
    """Return the one spelling of *value* a check and a transport both read."""
    return value.strip().translate(_ONE_SPELLING).rstrip("/")


def is_bare_origin(value: str) -> bool:
    """Return True when *value* is exactly ``scheme://host[:port]``.

    A path, query, credentials or an unparseable port never reach an Origin
    header.
    """
    try:
        # urlsplit itself raises on a malformed bracketed netloc and on one
        # whose NFKC normalisation introduces a delimiter, so it belongs
        # inside the guard with the port read.
        parsed = urlsplit(value)
        port_is_usable = parsed.port is None or parsed.port > 0
    except ValueError:
        return False
    return bool(
        port_is_usable
        and parsed.scheme in _ORIGIN_SCHEMES
        and parsed.hostname
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )
