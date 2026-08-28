"""Autouse isolation for every test in every tree.

This file is at the repository root rather than in ``tests/`` because a conftest
only applies to its own subtree, and tests are collected from three trees.
"""

import logging
import os
import socket
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NoReturn
from unittest.mock import patch

import pytest

from src.ingestion.paths import get_allowed_source_roots, set_allowed_source_roots
from src.utils.dependencies import dependency_drift

_LOOPBACK = {"127.0.0.1", "::1", "localhost", ""}

#: Captured at import, before any fixture can wrap them.
_REAL_SOCKET_CALLS: tuple[tuple[Any, str, Any], ...] = (
    (socket.socket, "connect", socket.socket.connect),
    (socket.socket, "connect_ex", socket.socket.connect_ex),
    (socket, "getaddrinfo", socket.getaddrinfo),
    (socket, "gethostbyname", socket.gethostbyname),
)


class NetworkAccessDenied(RuntimeError):
    pass


def _is_loopback(host: object) -> bool:
    return host is None or (isinstance(host, str) and host in _LOOPBACK)


def _refuse(host: object) -> NoReturn:
    raise NetworkAccessDenied(
        f"This test tried to reach {host!r}. Patch the transport, or request "
        "the `outbound_network` fixture if it truly must dial out."
    )


def _guard_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse a lookup or connection to anywhere but loopback.

    The lookup is guarded too, so a resolve alone cannot leak the host and
    the refusal can name it rather than whatever it resolved to.
    """

    def address_guard(real: Any) -> Any:
        def guarded(
            self: socket.socket, address: Any, *args: Any, **kwargs: Any
        ) -> Any | NoReturn:
            host = address[0] if isinstance(address, tuple) else address
            if _is_loopback(host):
                return real(self, address, *args, **kwargs)
            _refuse(host)

        return guarded

    def host_guard(real: Any) -> Any:
        def guarded(host: Any, *args: Any, **kwargs: Any) -> Any | NoReturn:
            if _is_loopback(host):
                return real(host, *args, **kwargs)
            _refuse(host)

        return guarded

    for target, name, real in _REAL_SOCKET_CALLS:
        wrap = address_guard if name.startswith("connect") else host_guard
        monkeypatch.setattr(target, name, wrap(real))


@pytest.fixture(autouse=True)
def _no_outbound_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two tests called the live Steam API on every run and nothing failed."""
    _guard_socket(monkeypatch)


@pytest.fixture()
def outbound_network(monkeypatch: pytest.MonkeyPatch) -> None:
    for target, name, real in _REAL_SOCKET_CALLS:
        monkeypatch.setattr(target, name, real)


def _remove_production_log_handlers() -> None:
    root = logging.getLogger()
    for handler in root.handlers[:]:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename.endswith(
            "recommendations.log"
        ):
            handler.close()
            root.removeHandler(handler)


@pytest.fixture(autouse=True)
def _isolate_production_log_handlers() -> Iterator[None]:
    """Prevent tests from writing to the production log file.

    Patched at its one definition, the file-opening entry point both interfaces
    reach, so neither imports the name.
    """
    _remove_production_log_handlers()
    with patch("src.utils.logging.configure_logging"):
        yield
    _remove_production_log_handlers()


@pytest.fixture(autouse=True)
def _clear_dependency_drift_cache() -> None:
    """Cached under one test's patched environment, it is the next test's answer."""
    dependency_drift.cache_clear()


@pytest.fixture(autouse=True)
def host_timezone() -> Iterator[Callable[[str], None]]:
    """Pin the process timezone to UTC and let a test choose another zone."""
    previous = os.environ.get("TZ")

    def use(zone: str) -> None:
        os.environ["TZ"] = zone
        time.tzset()

    use("UTC")
    yield use
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


@pytest.fixture(autouse=True)
def allowed_source_roots(tmp_path: Path) -> Iterator[Callable[[Path], None]]:
    """Contain file-based source plugins to this test's ``tmp_path``."""
    saved = get_allowed_source_roots()
    roots = [str(tmp_path)]
    set_allowed_source_roots(roots)

    def allow(root: Path) -> None:
        roots.append(str(root))
        set_allowed_source_roots(roots)

    yield allow
    set_allowed_source_roots(saved)


@pytest.fixture(autouse=True)
def _isolate_credential_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate credential encryption key to a temp dir for each test."""
    monkeypatch.setenv(
        "RECOMMENDINATOR_KEY_PATH",
        str(tmp_path / ".credential_key"),
    )
