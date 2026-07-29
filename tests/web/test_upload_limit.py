"""Tests for the request-body size limit middleware.

The middleware is what keeps an oversized upload off the host disk: by the
time an endpoint runs, Starlette's multipart parser has already spooled the
whole body to a temp file. These tests drive it over a probe app so they can
assert on what the wrapped application saw, which is the property that matters
— not merely that the client got a 413.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pytest import LogCaptureFixture
from starlette.types import Message, Receive, Scope, Send

from src.web.api import router as api_router
from src.web.upload_limit import (
    IMPORT_PATH,
    MAX_CONCURRENT_IMPORTS,
    MAX_REQUEST_BODY_BYTES,
    MAX_UPLOAD_BYTES,
    MULTIPART_OVERHEAD_BYTES,
    TOO_MANY_IMPORTS_DETAIL,
    RequestBodySizeLimitMiddleware,
    too_large_detail,
)

_CAP = 64

# Long enough that a genuine deadlock fails the test instead of hanging the
# suite, short enough that it does not stall CI when it does.
_WAIT_SECONDS = 10.0

# The middleware refuses a request on one of two paths, and both end in an
# identical 413 with an unread body. Only the log line distinguishes them.
_MIDDLEWARE_LOGGER = "src.web.upload_limit"
_DECLARED_LOG = "Refused a request declaring"
_MID_STREAM_LOG = "passed 64 bytes mid-stream"


@pytest.fixture()
def received_bodies() -> list[bytes]:
    """Bodies the wrapped application managed to read."""
    return []


@pytest.fixture()
def client(received_bodies: list[bytes]) -> TestClient:
    """A client over a probe app that records every body it reads."""
    app = FastAPI()
    app.add_middleware(RequestBodySizeLimitMiddleware, max_bytes=_CAP)

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        body = await request.body()
        received_bodies.append(body)
        return {"read": len(body)}

    return TestClient(app)


class TestDeclaredContentLength:
    """A declared content-length over the cap is refused before the app runs."""

    def test_oversized_body_never_reaches_the_application(
        self,
        client: TestClient,
        received_bodies: list[bytes],
        caplog: LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=_MIDDLEWARE_LOGGER):
            response = client.post("/echo", content=b"x" * (_CAP + 1))

        assert response.status_code == 413
        assert response.json()["detail"] == too_large_detail(MAX_UPLOAD_BYTES)
        # The point of the middleware: the body was never read, so it was
        # never spooled to the host's temp directory either.
        assert received_bodies == []
        assert _DECLARED_LOG in caplog.text
        assert _MID_STREAM_LOG not in caplog.text

    def test_body_at_the_cap_is_passed_through(
        self, client: TestClient, received_bodies: list[bytes]
    ) -> None:
        """Only *over* the cap is refused, so an exactly-at-the-cap body runs."""
        response = client.post("/echo", content=b"x" * _CAP)

        assert response.status_code == 200
        assert response.json() == {"read": _CAP}
        assert received_bodies == [b"x" * _CAP]

    def test_malformed_content_length_falls_through_to_counting(
        self,
        client: TestClient,
        received_bodies: list[bytes],
        caplog: LogCaptureFixture,
    ) -> None:
        """An unparseable header is not trusted either way; the counter decides.

        Both branches end in 413 with an unread body, so the status code cannot
        tell them apart — the log line is what says which one ran, and this test
        exists precisely to prove the malformed header was NOT trusted as a
        declared length.
        """
        with caplog.at_level(logging.WARNING, logger=_MIDDLEWARE_LOGGER):
            response = client.post(
                "/echo",
                content=b"x" * (_CAP + 1),
                headers={"content-length": "not-a-number"},
            )

        assert response.status_code == 413
        assert received_bodies == []
        assert _MID_STREAM_LOG in caplog.text
        assert _DECLARED_LOG not in caplog.text


class TestStreamedBody:
    """A chunked body with no declared length is bounded as it arrives."""

    def test_oversized_stream_is_refused(
        self,
        client: TestClient,
        received_bodies: list[bytes],
        caplog: LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=_MIDDLEWARE_LOGGER):
            response = client.post("/echo", content=iter([b"x" * 32] * 8))

        assert response.status_code == 413
        assert response.json()["detail"] == too_large_detail(MAX_UPLOAD_BYTES)
        assert received_bodies == []
        assert _MID_STREAM_LOG in caplog.text

    def test_stream_under_the_cap_is_passed_through(
        self, client: TestClient, received_bodies: list[bytes]
    ) -> None:
        response = client.post("/echo", content=iter([b"x" * 16, b"y" * 16]))

        assert response.status_code == 200
        assert received_bodies == [b"x" * 16 + b"y" * 16]


class TestOwnFallback:
    """The middleware answers the overrun itself when nothing else will.

    ``_BodyTooLarge`` derives from ``HTTPException``, so in the real stack
    Starlette's ``ExceptionMiddleware`` converts it into the 413 first and the
    middleware's own ``except _BodyTooLarge`` arm never runs. That arm is the
    last line of the security boundary, so it is driven here directly.
    """

    def test_a_bare_asgi_stack_still_gets_the_413(
        self, caplog: LogCaptureFixture
    ) -> None:
        """No exception middleware in the stack, and the client still sees 413.

        The wrapped app here is a plain ASGI callable: the signal propagates all
        the way back out of it, so only the middleware's own arm can answer.
        """

        async def bare_app(scope: Scope, receive: Receive, send: Send) -> None:
            while (await receive()).get("more_body", False):
                pass
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        client = TestClient(RequestBodySizeLimitMiddleware(bare_app, max_bytes=_CAP))
        with caplog.at_level(logging.WARNING, logger=_MIDDLEWARE_LOGGER):
            response = client.post("/echo", content=iter([b"x" * 32] * 4))

        assert response.status_code == 413
        assert response.json()["detail"] == too_large_detail(MAX_UPLOAD_BYTES)
        assert _MID_STREAM_LOG in caplog.text

    def test_a_started_response_is_never_started_twice(self) -> None:
        """An app that streams before reading gets no second response start.

        An ASGI server treats a second ``http.response.start`` as a protocol
        error, so without the ``response_started`` guard the 413 would replace a
        clean truncated response with a crash. The connection simply ends with
        the body unread, which is the outcome the cap exists for.
        """
        sent: list[Message] = []

        async def streaming_app(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send(
                {"type": "http.response.body", "body": b"partial", "more_body": True}
            )
            while (await receive()).get("more_body", False):
                pass
            await send({"type": "http.response.body", "body": b""})

        async def receive() -> Message:
            return {
                "type": "http.request",
                "body": b"x" * (_CAP + 1),
                "more_body": True,
            }

        async def send(message: Message) -> None:
            sent.append(message)

        middleware = RequestBodySizeLimitMiddleware(streaming_app, max_bytes=_CAP)
        scope: Scope = {
            "type": "http",
            "method": "POST",
            "path": "/echo",
            "headers": [],
        }
        asyncio.run(middleware(scope, receive, send))

        starts = [
            message for message in sent if message["type"] == "http.response.start"
        ]
        assert len(starts) == 1
        assert starts[0]["status"] == 200


class TestNonHttpScopes:
    """Anything that is not an HTTP request passes straight through."""

    def test_lifespan_is_not_intercepted(self) -> None:
        """A lifespan scope has no body to bound; TestClient startup proves it."""
        app = FastAPI()
        app.add_middleware(RequestBodySizeLimitMiddleware, max_bytes=_CAP)

        @app.get("/ping")
        async def ping() -> dict[str, bool]:
            return {"ok": True}

        with TestClient(app) as started:
            assert started.get("/ping").json() == {"ok": True}


class TestCapValues:
    """The caps the rest of the app is documented against."""

    def test_request_cap_is_the_upload_cap_plus_multipart_slack(self) -> None:
        """The request cap must exceed the file cap, or a 50 MB file cannot fit.

        A multipart body carries boundaries, per-part headers and the option
        fields on top of the file itself, so a request cap equal to the file
        cap would reject exactly the upload the file cap allows.
        """
        assert MAX_REQUEST_BODY_BYTES == MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES
        assert MULTIPART_OVERHEAD_BYTES > 0

    def test_import_path_matches_the_real_route(self) -> None:
        """The middleware matches the upload endpoint by literal path.

        It runs above routing, so it has no router to ask — the path is spelled
        out in ``upload_limit``. Renaming the route without updating it would
        silently drop the in-flight bound, with nothing else failing.
        """
        assert IMPORT_PATH in {
            getattr(route, "path", None) for route in api_router.routes
        }

    def test_concurrent_import_bound_is_small_and_positive(self) -> None:
        """A bound of zero would refuse every import; a large one bounds nothing."""
        assert 1 <= MAX_CONCURRENT_IMPORTS <= 4


@pytest.fixture()
def import_gate() -> Iterator[tuple[TestClient, threading.Event, threading.Event]]:
    """A client whose ``/api/import`` handler blocks until released.

    Yields ``(client, entered, release)``: the handler sets *entered* on the way
    in and waits on *release*, so a test can hold an import slot open while it
    sends the next request. The route is ``def`` rather than ``async def`` so
    Starlette runs it in a worker thread and the event loop stays free to serve
    the second request.
    """
    entered = threading.Event()
    release = threading.Event()

    app = FastAPI()
    app.add_middleware(RequestBodySizeLimitMiddleware, max_concurrent_imports=1)

    @app.post(IMPORT_PATH)
    def slow_import() -> dict[str, bool]:
        entered.set()
        release.wait(_WAIT_SECONDS)
        return {"imported": True}

    with TestClient(app) as client:
        try:
            yield client, entered, release
        finally:
            release.set()


class TestConcurrentImportLimit:
    """The number of imports in flight is bounded above the multipart parser."""

    def test_import_past_the_bound_is_refused_with_429(
        self,
        import_gate: tuple[TestClient, threading.Event, threading.Event],
        caplog: LogCaptureFixture,
    ) -> None:
        """A second import while one is in flight is refused, not queued.

        The bound has to live in the middleware: a counter inside the handler
        is only reached after Starlette's multipart parser has already spooled
        the whole body to the host's temp directory, which is the cost being
        bounded.
        """
        client, entered, release = import_gate
        first: list[int] = []

        def run_first() -> None:
            first.append(client.post(IMPORT_PATH).status_code)

        holder = threading.Thread(target=run_first)
        holder.start()
        try:
            assert entered.wait(_WAIT_SECONDS), "the first import never started"
            with caplog.at_level(logging.WARNING, logger=_MIDDLEWARE_LOGGER):
                refused = client.post(IMPORT_PATH)
        finally:
            release.set()
            holder.join(_WAIT_SECONDS)

        assert refused.status_code == 429
        assert refused.json()["detail"] == TOO_MANY_IMPORTS_DETAIL
        assert "already in flight" in caplog.text
        # The held request is unaffected — the bound refuses the newcomer.
        assert first == [200]

    def test_the_slot_is_released_once_the_import_finishes(
        self, import_gate: tuple[TestClient, threading.Event, threading.Event]
    ) -> None:
        """Back-to-back imports both succeed, so the counter is not leaked."""
        client, _entered, release = import_gate
        release.set()

        assert client.post(IMPORT_PATH).status_code == 200
        assert client.post(IMPORT_PATH).status_code == 200

    def test_a_failing_import_still_releases_its_slot(self) -> None:
        """A handler that raises must not permanently consume a slot.

        The release lives in a ``finally``; without it a single 500 would lock
        the endpoint out for the lifetime of the process.
        """
        app = FastAPI()
        app.add_middleware(RequestBodySizeLimitMiddleware, max_concurrent_imports=1)
        calls: list[int] = []

        @app.post(IMPORT_PATH)
        async def flaky_import() -> dict[str, bool]:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return {"imported": True}

        client = TestClient(app, raise_server_exceptions=False)
        assert client.post(IMPORT_PATH).status_code == 500
        assert client.post(IMPORT_PATH).status_code == 200

    def test_other_paths_are_not_bounded(self) -> None:
        """Only the upload endpoint takes a slot; nothing else is throttled.

        With no slots at all, every import is refused — and every other request
        still goes through, which is what pins the bound to ``IMPORT_PATH``
        rather than to traffic in general.
        """
        app = FastAPI()
        app.add_middleware(RequestBodySizeLimitMiddleware, max_concurrent_imports=0)

        @app.post(IMPORT_PATH)
        async def unreachable_import() -> dict[str, bool]:
            raise AssertionError("the handler must never run")

        @app.get("/api/status")
        async def status() -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app)
        assert client.post(IMPORT_PATH).status_code == 429
        assert client.get("/api/status").json() == {"ok": True}
