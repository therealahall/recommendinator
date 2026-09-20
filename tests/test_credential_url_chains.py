"""``scrub_request_error`` fixes only the message. ``from error`` leaves the query
string on ``__cause__``, which callers print with ``exc_info=True``."""

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
import requests

from src.enrichment.provider_base import ProviderError
from src.enrichment.providers.rawg.rawg import RAWGProvider
from src.enrichment.providers.tmdb.tmdb import TMDBProvider
from src.ingestion.plugin_base import OAuthError, SourceError
from src.ingestion.sources.calibre_web.calibre_web import CalibreWebPlugin
from src.ingestion.sources.gog.gog import GogAPIError, get_wishlist_product_ids
from src.ingestion.sources.gog.gog import exchange_code_for_tokens as gog_exchange_code
from src.ingestion.sources.gog.gog import get_owned_games as gog_owned_games
from src.ingestion.sources.gog.gog import (
    refresh_access_token as gog_refresh_access_token,
)
from src.ingestion.sources.steam.steam import (
    SteamAPIError,
    get_steam_id_from_vanity_url,
)
from src.ingestion.sources.steam.steam import get_owned_games as steam_owned_games
from src.ingestion.sources.trakt.trakt import (
    TraktAPIError,
    fetch_list,
    fetch_show_season_totals,
    poll_device_token,
    start_device_auth_flow,
)
from src.ingestion.sources.trakt.trakt import (
    refresh_access_token as trakt_refresh_access_token,
)
from src.ingestion.urls import (
    RedirectRefused,
    fixed_endpoint_refusal,
    request_within_origin,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: Whole trees rather than the packages that send today: a listing of those is
#: the hand list this scan exists to retire.
_SCANNED_TREES = (Path("private/plugins"), Path("src"))

_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)

_CREDENTIAL_URL_FUNCTIONS = (
    ("src/enrichment/providers/rawg/rawg.py", "_game_payload"),
    ("src/enrichment/providers/rawg/rawg.py", "_search_game"),
    ("src/enrichment/providers/tmdb/tmdb.py", "_fetch_keywords"),
    ("src/enrichment/providers/tmdb/tmdb.py", "_fetch_movie_details"),
    ("src/enrichment/providers/tmdb/tmdb.py", "_fetch_tv_details"),
    ("src/enrichment/providers/tmdb/tmdb.py", "_request_candidates"),
    ("src/enrichment/providers/tmdb/tmdb.py", "candidate_from_url"),
    ("src/ingestion/sources/gog/gog.py", "exchange_code_for_tokens"),
    ("src/ingestion/sources/gog/gog.py", "refresh_access_token"),
    ("src/ingestion/sources/steam/steam.py", "get_owned_games"),
    ("src/ingestion/sources/steam/steam.py", "get_steam_id_from_vanity_url"),
    ("src/ingestion/sources/tautulli/tautulli.py", "_api_get"),
)

_HTTP_VERBS = frozenset({"delete", "get", "head", "patch", "post", "put", "request"})

#: Exempt from the redirect walk: each reaches a keyless public API, so a hop off
#: the origin replays nothing. A new entry has to argue it sends no credential.
_UNCREDENTIALED_CALLERS = frozenset(
    {
        ("src/enrichment/providers/wikidata/wikidata.py", "_get"),
        ("src/ingestion/sources/goodreads_rss/goodreads_rss.py", "_fetch_page"),
    }
)

_CREDENTIAL_PARAM_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "client_secret",
        "code",
        "key",
        "password",
        "refresh_token",
        "secret",
        "token",
    }
)

_TRACEBACK_RENDERERS = frozenset(
    {"exception", "format_exc", "format_exception", "print_exc", "print_exception"}
)

_LOG_METHODS = frozenset(
    {"critical", "debug", "error", "exception", "info", "log", "warning"}
)

_SCRUBBERS = frozenset({"exception_for_log", "scrub_request_error"})

_REQUEST_ERROR_NAMES = frozenset(
    {
        "BaseException",
        "ConnectionError",
        "Exception",
        "HTTPError",
        "JSONDecodeError",
        "RequestException",
        "Timeout",
    }
)


def _bare_name(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _catches_a_request_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    return any(
        _bare_name(node) in _REQUEST_ERROR_NAMES
        for node in ast.walk(handler.type)
        if isinstance(node, (ast.Name, ast.Attribute))
    )


def _attaches_a_traceback(keyword: ast.keyword) -> bool:
    """``exc_info=False`` is the default written out, so it renders nothing."""
    return keyword.arg == "exc_info" and not (
        isinstance(keyword.value, ast.Constant) and not keyword.value.value
    )


def _renders_a_traceback(handler: ast.ExceptHandler) -> bool:
    for child in ast.walk(handler):
        if not isinstance(child, ast.Call):
            continue
        if any(_attaches_a_traceback(keyword) for keyword in child.keywords):
            return True
        if _bare_name(child.func) in _TRACEBACK_RENDERERS:
            return True
    return False


def _interpolates_a_value(node: ast.expr) -> bool:
    """Reading every ``Call`` as scrubbed waved three of them through."""
    if isinstance(node, (ast.Name, ast.JoinedStr)):
        return True
    if isinstance(node, ast.BinOp):
        return isinstance(node.op, ast.Mod)
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Attribute):
        return node.func.attr == "format"
    return isinstance(node.func, ast.Name) and node.func.id == "str"


def _scrubbed_nodes(argument: ast.expr) -> set[int]:
    """``f"failed: {scrub_request_error(error)}"`` is the fixed shape inside an
    f-string, so the name under it is not the one carrying the URL."""
    return {
        id(node)
        for call in ast.walk(argument)
        if isinstance(call, ast.Call) and _bare_name(call.func) in _SCRUBBERS
        for node in ast.walk(call)
    }


def _names_the_error(argument: ast.expr, bound: str | None) -> bool:
    if not _interpolates_a_value(argument):
        return False
    scrubbed = _scrubbed_nodes(argument)
    return any(
        isinstance(node, ast.Name) and node.id == bound and id(node) not in scrubbed
        for node in ast.walk(argument)
    )


def _logs_the_raw_error(handler: ast.ExceptHandler) -> bool:
    return any(
        _bare_name(child.func) in _LOG_METHODS
        and any(_names_the_error(argument, handler.name) for argument in child.args)
        for child in ast.walk(handler)
        if isinstance(child, ast.Call)
    )


def _keeps_the_cause(raised: ast.Raise) -> bool:
    return not (isinstance(raised.cause, ast.Constant) and raised.cause.value is None)


def _leaky_renderings(module_path: Path, function_name: str) -> list[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, _FUNCTION_NODES) and node.name == function_name
    ]
    assert functions, f"{module_path} has no function named {function_name}"

    handlers = [
        handler
        for function in functions
        for handler in ast.walk(function)
        if isinstance(handler, ast.ExceptHandler) and _catches_a_request_error(handler)
    ]
    assert handlers, (
        f"{module_path} no longer catches a request error in {function_name}, "
        "so an empty report below would mean nothing"
    )

    leaks: list[str] = []
    for handler in handlers:
        if _renders_a_traceback(handler):
            leaks.append(f"{function_name}: logs a traceback of the request error")
        if _logs_the_raw_error(handler):
            leaks.append(f"{function_name}: logs the request error unscrubbed")
        leaks.extend(
            f"{function_name}: `{ast.unparse(raised)}` keeps it as the cause"
            for raised in ast.walk(handler)
            if isinstance(raised, ast.Raise) and _keeps_the_cause(raised)
        )
    return leaks


def _keys_written(function: ast.AST) -> set[str]:
    """Three spellings reach a query string identically: a dict literal, an item
    assignment onto one, and ``dict(api_key=…)``. Reading only the literal was
    the hole."""
    keys: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Dict):
            keys.update(
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            )
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if isinstance(node.slice.value, str):
                keys.add(node.slice.value)
        elif isinstance(node, ast.Call) and _bare_name(node.func) == "dict":
            keys.update(keyword.arg for keyword in node.keywords if keyword.arg)
    return keys


def _names_a_credential(function: ast.AST) -> bool:
    return bool(_keys_written(function) & _CREDENTIAL_PARAM_NAMES)


def _sends_query_params(function: ast.AST) -> bool:
    return any(
        keyword.arg == "params"
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
    )


def _credential_url_functions(root: Path, *subtrees: Path) -> set[tuple[str, str]]:
    """Over-approximate on purpose: the name and the ``params=`` need only share a
    function, not a dict."""
    found: set[tuple[str, str]] = set()
    for subtree in subtrees:
        for module_path in sorted((root / subtree).rglob("*.py")):
            if module_path.name.startswith("test_"):
                continue
            tree = ast.parse(
                module_path.read_text(encoding="utf-8"), filename=str(module_path)
            )
            relative = module_path.relative_to(root).as_posix()
            found.update(
                (relative, node.name)
                for node in ast.walk(tree)
                if isinstance(node, _FUNCTION_NODES)
                and _sends_query_params(node)
                and _names_a_credential(node)
            )
    return found


def _is_a_direct_send(node: ast.AST) -> bool:
    """``request_within_origin(requests.get, …)`` hands the verb over rather than
    calling it, so only a ``Call`` on the attribute counts."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "requests"
        and node.func.attr in _HTTP_VERBS
    )


def _direct_senders(node: ast.AST, enclosing: str) -> set[str]:
    """The nearest enclosing ``def`` owns the call, so a nested helper is named
    rather than the function holding it."""
    found: set[str] = set()
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _FUNCTION_NODES):
            found |= _direct_senders(child, child.name)
            continue
        if _is_a_direct_send(child):
            found.add(enclosing)
        found |= _direct_senders(child, enclosing)
    return found


def _direct_requests_callers(root: Path, *subtrees: Path) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for subtree in subtrees:
        for module_path in sorted((root / subtree).rglob("*.py")):
            if module_path.name.startswith("test_"):
                continue
            tree = ast.parse(
                module_path.read_text(encoding="utf-8"), filename=str(module_path)
            )
            relative = module_path.relative_to(root).as_posix()
            found.update((relative, name) for name in _direct_senders(tree, "<module>"))
    return found


class TestCredentialUrlHandlersStayOutOfTracebacks:
    @pytest.mark.parametrize(("module", "function"), _CREDENTIAL_URL_FUNCTIONS)
    def test_no_request_error_survives_the_handler(
        self, module: str, function: str
    ) -> None:
        leaks = _leaky_renderings(_REPO_ROOT / module, function)

        assert not leaks, (
            f"{module} puts a credential-bearing request URL where a caller's "
            "`exc_info=True` will print it. Scrub the message and raise "
            "`from None`:\n  " + "\n  ".join(leaks)
        )

    @pytest.mark.parametrize(
        "module",
        ["private/plugins/whatever/whatever.py", "src/enrichment/manager.py"],
    )
    def test_a_module_in_any_scanned_tree_is_read_and_a_missing_tree_is_not_an_error(
        self, tmp_path: Path, module: str
    ) -> None:
        """`private/plugins/` is gitignored, so CI's clone has no such directory;
        `src/enrichment/manager.py` already imports `requests` from outside every
        plugin folder, where a scan listing the plugin folders never looks."""
        path = tmp_path / module
        path.parent.mkdir(parents=True)
        path.write_text(
            "import requests\n\n\n"
            "def fetch(url, key):\n"
            "    return requests.get(url, params={'api_key': key})\n",
            encoding="utf-8",
        )
        reaches_requests = {(module, "fetch")}

        assert _direct_requests_callers(tmp_path, *_SCANNED_TREES) == reaches_requests
        assert _credential_url_functions(tmp_path, *_SCANNED_TREES) == reaches_requests

    def test_every_caller_sending_a_credential_param_is_registered(self) -> None:
        scanned = {
            (module, function)
            for module, function in _CREDENTIAL_URL_FUNCTIONS
            if any(
                module.startswith(f"{subtree.as_posix()}/")
                for subtree in _SCANNED_TREES
            )
        }
        assert scanned == set(_CREDENTIAL_URL_FUNCTIONS), (
            "a registered module sits outside every scanned tree, so the "
            "comparison below cannot see it"
        )

        assert _credential_url_functions(_REPO_ROOT, *_SCANNED_TREES) == scanned


_CREDENTIAL = "credential-that-must-not-travel"

_ELSEWHERE = "https://elsewhere.example/taken"

_CALIBRE_CONFIG = {
    "url": "http://localhost:8083",
    "username": "reader",
    "password": _CREDENTIAL,
}


def _item(content_type: ContentType) -> ContentItem:
    return ContentItem(
        id="1",
        title="Prey",
        content_type=content_type,
        status=ConsumptionStatus.UNREAD,
    )


_CREDENTIALED_CALLERS = [
    pytest.param(
        lambda: list(CalibreWebPlugin().fetch(dict(_CALIBRE_CONFIG))),
        SourceError,
        id="calibre-web-opds-feed",
    ),
    pytest.param(
        lambda: get_steam_id_from_vanity_url(_CREDENTIAL, "someone"),
        SteamAPIError,
        id="steam-vanity-url",
    ),
    pytest.param(
        lambda: steam_owned_games(_CREDENTIAL, "76561197960287930"),
        SteamAPIError,
        id="steam-owned-games",
    ),
    pytest.param(
        lambda: gog_refresh_access_token(_CREDENTIAL),
        GogAPIError,
        id="gog-token-refresh",
    ),
    pytest.param(
        lambda: gog_owned_games(_CREDENTIAL, rate_limit_seconds=0),
        GogAPIError,
        id="gog-owned-games",
    ),
    pytest.param(
        lambda: get_wishlist_product_ids(_CREDENTIAL),
        GogAPIError,
        id="gog-wishlist",
    ),
    pytest.param(
        lambda: trakt_refresh_access_token(_CREDENTIAL, "client-id", "client-secret"),
        TraktAPIError,
        id="trakt-token-refresh",
    ),
    pytest.param(
        lambda: fetch_list("/sync/watched/movies", _CREDENTIAL, "client-id"),
        TraktAPIError,
        id="trakt-list",
    ),
    pytest.param(
        lambda: fetch_show_season_totals(1, _CREDENTIAL, "client-id"),
        TraktAPIError,
        id="trakt-season-totals",
    ),
    pytest.param(
        lambda: TMDBProvider().enrich(
            _item(ContentType.MOVIE), {"api_key": _CREDENTIAL}
        ),
        ProviderError,
        id="tmdb-search",
    ),
    pytest.param(
        lambda: RAWGProvider().enrich(
            _item(ContentType.VIDEO_GAME), {"api_key": _CREDENTIAL}
        ),
        ProviderError,
        id="rawg-search",
    ),
    pytest.param(
        lambda: gog_exchange_code(_CREDENTIAL),
        OAuthError,
        id="gog-oauth-code-exchange",
    ),
    pytest.param(
        lambda: start_device_auth_flow(_CREDENTIAL),
        OAuthError,
        id="trakt-device-code",
    ),
    pytest.param(
        lambda: poll_device_token("device-code", "client-id", _CREDENTIAL),
        OAuthError,
        id="trakt-device-token",
    ),
]


class TestNoCredentialFollowsARedirectOffItsOrigin:
    def test_no_caller_reaches_requests_around_the_redirect_walk(self) -> None:
        assert _direct_requests_callers(_REPO_ROOT, *_SCANNED_TREES) == set(
            _UNCREDENTIALED_CALLERS
        ), (
            "a caller reaches `requests` directly, so `requests` replays its "
            "credential onto whatever host a `Location` names. Send it through "
            "`src.ingestion.urls.request_within_origin`, or list it in "
            "_UNCREDENTIALED_CALLERS with the argument that it carries none."
        )

    def test_a_location_no_parser_can_read_is_refused_rather_than_raised(self) -> None:
        """A reverse proxy answering `Location: http://[::1` killed the sync with
        an unhandled ValueError, which no caller catches."""
        unreadable = Mock(
            spec=requests.Response,
            status_code=301,
            headers={"Location": "http://[::1"},
        )

        with pytest.raises(RedirectRefused):
            request_within_origin(
                lambda *_args, **_sent: unreadable,
                "https://openlibrary.org/search.json",
                "Open Library",
                fixed_endpoint_refusal,
            )

    @pytest.mark.parametrize(("invoke", "refusal"), _CREDENTIALED_CALLERS)
    def test_a_hop_off_the_origin_is_refused_rather_than_sent_the_credential(
        self, invoke: Callable[[], Any], refusal: type[Exception]
    ) -> None:
        redirected = Mock(
            spec=requests.Response,
            status_code=301,
            headers={"Location": _ELSEWHERE},
        )

        with (
            patch.object(requests, "get", return_value=redirected),
            patch.object(requests, "post", return_value=redirected),
            pytest.raises(refusal, match="Refused a redirect") as refused,
        ):
            invoke()

        assert _CREDENTIAL not in str(refused.value)
