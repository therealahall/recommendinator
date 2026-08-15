"""Guard: no credential-in-URL request error survives where a caller renders it.

``scrub_request_error`` fixes only the message. ``from error`` leaves the query
string on ``__cause__``, which callers print with ``exc_info=True``.
"""

import ast
from pathlib import Path

import pytest

# parents[1] resolves /tests/test_credential_url_chains.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]

# Every tree a credential could reach a third party from. Discovery
# over-approximates, so a tree holding no such call today still costs nothing
# to scan, and one that leaves the list loses future coverage in silence.
_SCANNED_TREES = (
    Path("src/auth"),
    Path("src/config"),
    Path("src/enrichment/providers"),
    Path("src/ingestion/sources"),
    Path("src/sources"),
    Path("src/utils"),
    Path("src/web"),
)

_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)

# GOG's token endpoint takes the refresh token, the authorization code and the
# client secret as query parameters; Steam's Web API takes ``key``, TMDB
# ``api_key`` and RAWG ``key``.
_CREDENTIAL_URL_FUNCTIONS = (
    ("src/auth/gog.py", "exchange_code_for_tokens"),
    ("src/enrichment/providers/rawg/rawg.py", "_fetch_game_details"),
    ("src/enrichment/providers/rawg/rawg.py", "_fetch_game_series"),
    ("src/enrichment/providers/rawg/rawg.py", "_search_game"),
    ("src/enrichment/providers/tmdb/tmdb.py", "_fetch_keywords"),
    ("src/enrichment/providers/tmdb/tmdb.py", "_fetch_movie_details"),
    ("src/enrichment/providers/tmdb/tmdb.py", "_fetch_tv_details"),
    ("src/enrichment/providers/tmdb/tmdb.py", "_get_movie_position_in_collection"),
    ("src/enrichment/providers/tmdb/tmdb.py", "_search_media"),
    ("src/ingestion/sources/gog/gog.py", "refresh_access_token"),
    ("src/ingestion/sources/steam/steam.py", "get_owned_games"),
    ("src/ingestion/sources/steam/steam.py", "get_steam_id_from_vanity_url"),
)

# Parameter names that carry a secret. Naming one of these where a ``params=``
# call site can reach it is what enrols a plugin in the guard, so a new
# integration joins by being written rather than by being remembered.
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

# The two calls that render a request fault without its URL. Everything else
# reaching a log argument keeps the words the credential is in.
_SCRUBBERS = frozenset({"exception_for_log", "scrub_request_error"})

# Matched on the trailing name, so ``except HTTPError`` is judged like
# ``except requests.exceptions.HTTPError``. The catch-alls are in the set
# because widening the clause is the cheapest way to empty a named sweep.
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
    """Return an expression's trailing name, dropping any module or object prefix."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _catches_a_request_error(handler: ast.ExceptHandler) -> bool:
    """Report whether the request exception can land in ``handler``."""
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
    """Report whether ``handler`` renders a traceback of what it caught."""
    for child in ast.walk(handler):
        if not isinstance(child, ast.Call):
            continue
        if any(_attaches_a_traceback(keyword) for keyword in child.keywords):
            return True
        if _bare_name(child.func) in _TRACEBACK_RENDERERS:
            return True
    return False


def _interpolates_a_value(node: ast.expr) -> bool:
    """The five spellings that render a value into a log line.

    ``%s`` renders the bare name, and ``str``, an f-string, ``%`` and
    ``.format`` get there first. Reading every ``Call`` as scrubbed waved
    three of them through.
    """
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
    """Every node under a scrubber call, by identity.

    ``f"failed: {scrub_request_error(error)}"`` is the fixed shape inside an
    f-string, so the name under it is not the one carrying the URL.
    """
    return {
        id(node)
        for call in ast.walk(argument)
        if isinstance(call, ast.Call) and _bare_name(call.func) in _SCRUBBERS
        for node in ast.walk(call)
    }


def _names_the_error(argument: ast.expr, bound: str | None) -> bool:
    """Report whether ``argument`` is the caught exception itself, unscrubbed."""
    if not _interpolates_a_value(argument):
        return False
    scrubbed = _scrubbed_nodes(argument)
    return any(
        isinstance(node, ast.Name) and node.id == bound and id(node) not in scrubbed
        for node in ast.walk(argument)
    )


def _logs_the_raw_error(handler: ast.ExceptHandler) -> bool:
    """Report whether ``handler`` hands its unscrubbed exception to a log call."""
    return any(
        _bare_name(child.func) in _LOG_METHODS
        and any(_names_the_error(argument, handler.name) for argument in child.args)
        for child in ast.walk(handler)
        if isinstance(child, ast.Call)
    )


def _keeps_the_cause(raised: ast.Raise) -> bool:
    """Report whether ``raised`` leaves the handled exception in the chain."""
    return not (isinstance(raised.cause, ast.Constant) and raised.cause.value is None)


def _leaky_renderings(module_path: Path, function_name: str) -> list[str]:
    """Describe every way ``function_name`` lets a request exception be rendered."""
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
    """Every string this function spells as a key.

    Three spellings reach a query string identically: a dict literal, an item
    assignment onto one, and ``dict(api_key=…)``. Reading only the literal was
    the hole.
    """
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
    """Report whether ``function`` keys anything by a secret's name."""
    return bool(_keys_written(function) & _CREDENTIAL_PARAM_NAMES)


def _sends_query_params(function: ast.AST) -> bool:
    """Report whether ``function`` puts anything in a request query string."""
    return any(
        keyword.arg == "params"
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
    )


def _credential_url_functions(root: Path, *subtrees: Path) -> set[tuple[str, str]]:
    """Find every function under *subtrees* sending a credential as a param.

    Over-approximate on purpose: the name and the ``params=`` need only share
    a function, not a dict. A needless registration costs a line, a missing
    one costs the key.
    """
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


class TestCredentialUrlHandlersStayOutOfTracebacks:
    """One rule for every call that puts a secret in a query string.

    None may render a traceback, log its exception unscrubbed, or keep it as a
    ``__cause__``, because callers print all three.
    """

    @pytest.mark.parametrize(("module", "function"), _CREDENTIAL_URL_FUNCTIONS)
    def test_no_request_error_survives_the_handler(
        self, module: str, function: str
    ) -> None:
        """The credential-bearing calls neither log nor chain their request error."""
        leaks = _leaky_renderings(_REPO_ROOT / module, function)

        assert not leaks, (
            f"{module} puts a credential-bearing request URL where a caller's "
            "`exc_info=True` will print it. Scrub the message and raise "
            "`from None`:\n  " + "\n  ".join(leaks)
        )

    def test_every_caller_sending_a_credential_param_is_registered(self) -> None:
        """A new integration joins the list above, rather than being remembered in."""
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
