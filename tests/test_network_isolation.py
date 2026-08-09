"""The root conftest's refusal to let a test dial a real host."""

from __future__ import annotations

import socket

import pytest

from conftest import NetworkAccessDenied


def test_a_connection_to_a_real_host_is_refused() -> None:
    """Two tests called the live Steam API for months without failing."""
    with pytest.raises(NetworkAccessDenied, match="api.steampowered.com"):
        socket.create_connection(("api.steampowered.com", 443), timeout=1)


def test_the_refusal_names_the_way_out() -> None:
    """A refusal nobody can act on gets worked around rather than fixed."""
    with pytest.raises(NetworkAccessDenied, match="outbound_network"):
        socket.create_connection(("example.com", 80), timeout=1)


def test_loopback_still_connects() -> None:
    """TestClient and the local database clients need it.

    Refusing loopback would make the guard the thing everyone disables.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        with socket.create_connection(listener.getsockname(), timeout=1):
            pass
    finally:
        listener.close()


def test_a_test_that_needs_the_network_can_ask(outbound_network: None) -> None:
    """The hatch must lift the refusal, not reinstall it.

    Asserting against loopback cannot tell those apart. What the resolver
    answers is the network's business, not this test's.
    """
    try:
        socket.getaddrinfo("example.com", 80)
    except NetworkAccessDenied:  # pragma: no cover - the failure this pins
        pytest.fail("outbound_network left the guard installed")
    except OSError:
        pass


def test_connect_ex_is_guarded_too() -> None:
    """It returns an errno rather than raising, so an unguarded one is silent."""
    with pytest.raises(NetworkAccessDenied):
        socket.socket().connect_ex(("93.184.216.34", 80))
