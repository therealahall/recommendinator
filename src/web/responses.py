"""The one encode every response body passes through.

Any JSON blob column can hold an unpaired ``\\ud800``: ``json.loads`` accepts
the escape and ``json.dumps`` stores it as ASCII. Encoding such a body strictly
answered 500 on every later read, forever.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.responses import JSONResponse, Response

#: Only a lone surrogate can reach it, UTF-8 refusing nothing else, and inside
#: a JSON body the escape it writes is a JSON escape — so the caller reads back
#: the value that is stored.
ENCODE_ERRORS = "backslashreplace"


class SurrogateSafeJSONResponse(JSONResponse):
    """``JSONResponse``, minus the strict encode. The app's default."""

    def render(self, content: Any) -> bytes:
        # The json.dumps arguments are JSONResponse's own; only the encode
        # differs. allow_nan stays False: a non-finite float is refused at the
        # request model and dropped on read, so rendering one would hide that.
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode(self.charset, ENCODE_ERRORS)


class SurrogateSafeResponse(Response):
    """The same, for an endpoint handing over a body it serialised itself."""

    def render(self, content: Any) -> bytes | memoryview:
        if isinstance(content, str):
            return content.encode(self.charset, ENCODE_ERRORS)
        return super().render(content)
