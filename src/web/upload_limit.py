"""Request-body size limiting for the app.

The upload endpoint (``POST /api/import``) declares ``file: UploadFile``, and
FastAPI resolves that dependency *before* the handler body runs: Starlette's
multipart parser has already drained the whole request stream into a
``SpooledTemporaryFile`` (spilling to the system temp directory past 1 MB) by
the time any application code can look at it. The handler's chunked copy loop
therefore bounds only the second copy.

This middleware is the layer that bounds the first one. It runs before the
parser, refuses a declared ``content-length`` over the cap outright, and counts
the bytes that actually arrive so a chunked request with no declared length is
bounded too. It also bounds how many imports may be in flight at once, because
the per-request cap says nothing about the aggregate: every accepted request
costs the spooled body plus the handler's own temp copy plus an ingestion run.
The app has no authentication and can be bound to ``0.0.0.0``, so without both
bounds an unauthenticated caller fills the host temp filesystem before the
endpoint is even entered.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Cap on the imported file itself, enforced again inside the upload handler.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# Slack above the file cap for multipart framing, so the request limit sized
# for a 50 MB file does not reject a legitimate 50 MB file. A multipart body
# carries a boundary line and Content-Disposition/Content-Type headers per
# part, plus the ``source`` field and one field per import option — a few
# kilobytes in practice. 1 MB is deliberately far above that: the allowance
# must never be the thing that rejects a valid upload, and an extra megabyte
# does not meaningfully change what a single request can push at the parser.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024

MAX_REQUEST_BODY_BYTES = MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES

# Path of the one endpoint whose in-flight count is bounded. Pinned against the
# real route by ``tests/web/test_upload_limit.py``.
IMPORT_PATH = "/api/import"

# How many imports may be in flight at once. Each one costs up to the request
# cap spooled by the multipart parser, plus the handler's own up-to-50 MB temp
# copy, plus an ingestion run — so the aggregate, not the per-request cap, is
# what bounds disk and CPU here. Two rather than one because the handler's own
# duplicate guard is per plugin (a Goodreads import and a CSV import may
# legitimately overlap); collapsing that to a global lock would be a behaviour
# change dressed up as a limit. Two rather than more because this is a
# single-user app: nobody is running a third import on purpose.
MAX_CONCURRENT_IMPORTS = 2

TOO_MANY_IMPORTS_DETAIL = (
    "Too many imports are already running. Wait for one to finish, then retry."
)


def too_large_detail(max_upload_bytes: int) -> str:
    """Render the client-facing message for an upload over *max_upload_bytes*.

    Shared so the middleware and the handler's own backstop loop word the
    rejection identically.
    """
    return f"Upload exceeds the {max_upload_bytes // (1024 * 1024)} MB limit."


# Quotes the file cap rather than the request cap: the slack above it is an
# implementation detail the user should not have to reason about.
TOO_LARGE_DETAIL = too_large_detail(MAX_UPLOAD_BYTES)


class _BodyTooLarge(HTTPException):
    """Internal signal raised from the wrapped ``receive`` past the cap.

    Derives from Starlette's ``HTTPException`` so it survives the one filter
    that stands between the cap and the endpoint it exists for. On
    ``/api/import`` the overrun is raised inside ``await request.form()``, and
    FastAPI wraps that call in ``except Exception`` -> HTTP 400 "There was an
    error parsing the body" (``fastapi/routing.py``). The arm just above it is
    ``except HTTPException: raise``, so an oversized upload reaches the client
    as the 413 it is instead of a misleading 400.
    """

    def __init__(self) -> None:
        """Carry the same status and detail the middleware sends itself."""
        super().__init__(status_code=413, detail=TOO_LARGE_DETAIL)


class RequestBodySizeLimitMiddleware:
    """Refuse an oversized request body (413) or an excess import (429).

    Written as pure ASGI rather than ``BaseHTTPMiddleware`` because the whole
    point is to wrap ``receive`` — the body has to be bounded on its way *in*,
    which ``BaseHTTPMiddleware`` gives no access to. The in-flight import count
    lives here for the same reason: a counter inside the handler would only be
    reached after the multipart parser had already spooled the body to disk.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int = MAX_REQUEST_BODY_BYTES,
        max_concurrent_imports: int = MAX_CONCURRENT_IMPORTS,
    ) -> None:
        """Store the wrapped app and the two bounds.

        Args:
            app: The next ASGI application in the stack.
            max_bytes: Largest request body accepted, in bytes.
            max_concurrent_imports: Most imports allowed in flight at once.
        """
        self.app = app
        self.max_bytes = max_bytes
        self.max_concurrent_imports = max_concurrent_imports
        self._imports_in_flight = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Bound the body of an HTTP request, passing everything else through."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._declared_length_over_cap(scope):
            logger.warning(
                "Refused a request declaring %s bytes of body (cap %d)",
                Headers(scope=scope).get("content-length"),
                self.max_bytes,
            )
            await _send_error(scope, receive, send, 413, TOO_LARGE_DETAIL)
            return

        is_import = _is_import_request(scope)
        if is_import:
            if self._imports_in_flight >= self.max_concurrent_imports:
                logger.warning(
                    "Refused an import: %d already in flight",
                    self._imports_in_flight,
                )
                await _send_error(scope, receive, send, 429, TOO_MANY_IMPORTS_DETAIL)
                return
            # Nothing is awaited between the check and the increment, so the
            # event loop cannot slip another request in between them.
            self._imports_in_flight += 1

        try:
            await self._call_with_bounded_body(scope, receive, send)
        finally:
            if is_import:
                self._imports_in_flight -= 1

    async def _call_with_bounded_body(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        """Run the wrapped app with a ``receive`` that counts what arrives."""
        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # Logged here rather than where the signal is caught,
                    # because it may be caught by the app's own exception
                    # handling instead of by the fallback below. At the raise
                    # site the overrun is recorded exactly once, either way.
                    logger.warning(
                        "Refused a request whose body passed %d bytes mid-stream",
                        self.max_bytes,
                    )
                    raise _BodyTooLarge
            return message

        async def watched_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, watched_send)
        except _BodyTooLarge:
            # Only reached when nothing inside turned the signal into a
            # response — a stack without Starlette's ``ExceptionMiddleware``,
            # or a body read outside any route.
            if response_started:
                # A response is already on the wire, so the 413 cannot replace
                # it; dropping out here closes the connection with the body
                # unread, which is the outcome the cap exists for anyway.
                return
            await _send_error(scope, receive, send, 413, TOO_LARGE_DETAIL)

    def _declared_length_over_cap(self, scope: Scope) -> bool:
        """True when a parseable ``content-length`` header exceeds the cap.

        An absent or malformed header is not treated as a rejection: the
        streaming counter bounds those requests instead.
        """
        raw_length = Headers(scope=scope).get("content-length")
        if raw_length is None:
            return False
        try:
            declared = int(raw_length)
        except ValueError:
            return False
        return declared > self.max_bytes


def _is_import_request(scope: Scope) -> bool:
    """True for the upload endpoint, whose in-flight count is bounded."""
    return bool(scope["method"] == "POST" and scope["path"] == IMPORT_PATH)


async def _send_error(
    scope: Scope, receive: Receive, send: Send, status_code: int, detail: str
) -> None:
    """Answer the request directly, without ever entering the wrapped app."""
    response = JSONResponse({"detail": detail}, status_code=status_code)
    await response(scope, receive, send)
