"""Shape and locality checks for the URLs config exposes over the network.

``web.allowed_origins`` and ``ollama.base_url`` are each settable by one
request and each must be a bare origin, so both read that grammar from here.
"""

from __future__ import annotations

import socket
from ipaddress import IPv4Address, IPv4Network, IPv6Address, ip_address
from urllib.parse import urlsplit

# The only schemes a browser puts in an Origin header for a page that could
# reach this app, and the only two an Ollama server speaks.
_ORIGIN_SCHEMES = frozenset({"http", "https"})

# Suffixes no public registrar hands out: ``.local`` is mDNS and ICANN
# reserved ``.internal`` for private use.
_LOCAL_SUFFIXES = (".local", ".internal")

# Shared address space: never globally routable, and what Tailscale addresses
# a machine on. ``ipaddress`` does not count it private.
_SHARED_ADDRESS_SPACE = IPv4Network("100.64.0.0/10")

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
    header, and an Ollama base URL carrying one addresses something that is
    not an Ollama server.
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


def _embedded_ipv4(address: IPv6Address) -> IPv4Address | None:
    """Return the IPv4 destination *address* encodes, if any.

    ``is_private`` counts all of 6to4 and Teredo private, so ``2002:808:808::``
    — 8.8.8.8 — reads as local. Judging the embedded address also keeps the
    IPv4-mapped answer off the interpreter's patch level (CVE-2024-4032).
    """
    teredo = address.teredo
    return address.ipv4_mapped or address.sixtofour or (teredo[1] if teredo else None)


def _numeric_host(host: str) -> IPv4Address | IPv6Address | None:
    """Return the address *host* literally is, in any spelling.

    ``ip_address`` reads dotted-quad alone, so the integer, hex and octal
    forms of 8.8.8.8 would otherwise reach the single-label branch as names.
    ``inet_aton`` is what the resolver reads them with.
    """
    try:
        address = ip_address(host)
    except ValueError:
        pass
    else:
        if isinstance(address, IPv6Address):
            return _embedded_ipv4(address) or address
        return address
    try:
        return IPv4Address(socket.inet_aton(host))
    except OSError:
        return None


def is_local_url(value: str) -> bool:
    """Return True when *value*'s host is this machine or its own network.

    Text alone, never a lookup: resolving is a network call inside a config
    write, and its answer can change before the request that trusted it.
    """
    # Normalised here too, so a caller holding a raw config.yaml value reads
    # the same host as one holding a validated setting. Guarded because
    # src/llm/client.py reaches here without is_bare_origin having run.
    try:
        host = urlsplit(normalize_origin(value)).hostname
    except ValueError:
        return False
    if not host:
        return False
    address = _numeric_host(host)
    if address is None:
        # A single-label name is served by compose, /etc/hosts or the local
        # resolver, so it is as local as an RFC 1918 literal.
        return "." not in host or host.endswith(_LOCAL_SUFFIXES)
    if isinstance(address, IPv4Address) and address in _SHARED_ADDRESS_SPACE:
        return True
    return address.is_loopback or address.is_private or address.is_link_local
