"""The container liveness probe, against a server that answers like the app.

A real loopback server rather than a patched ``urlopen``: the defect is that
``urlopen`` *raises* on 401, which a stub would assert into existence.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any

import pytest

from src.web.healthcheck import HEALTHY_STATUS, probe


class _QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        """Drop the per-request access log, which goes to the suite's stderr."""


@pytest.fixture()
def answering() -> Iterator[Callable[[int], str]]:
    """Start a loopback server answering every GET with a given status."""
    servers: list[HTTPServer] = []

    def start(status: int) -> str:
        class _Handler(_QuietHandler):
            def do_GET(self) -> None:
                self.send_response(status)
                self.end_headers()

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        servers.append(server)
        Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_port}/api/status"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _unused_url() -> str:
    """A loopback URL with nothing listening on it."""
    with socket.socket() as spare:
        spare.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{spare.getsockname()[1]}/api/status"


class TestTheProbeSurvivesTheAuthRequirement:
    """Regression test: both shipped images reported unhealthy forever.

    Bug reported: containers never left `unhealthy`.
    Root cause: the probe read any `urlopen` raise as death; auth made 401,
    which raises, the only answer.
    Fix: it reads the status code.
    """

    def test_a_401_is_healthy(self, answering) -> None:
        assert probe(answering(HEALTHY_STATUS)) == 0

    @pytest.mark.parametrize("status", [200, 404, 500, 503])
    def test_no_other_answer_is(self, answering, status: int) -> None:
        """Including 200: an open `/api/status` means auth left the router."""
        assert probe(answering(status)) == 1

    def test_a_refused_connection_is_not(self) -> None:
        """The case the probe exists for: nothing is listening at all."""
        assert probe(_unused_url()) == 1
