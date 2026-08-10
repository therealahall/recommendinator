"""Tests for GOG OAuth authentication."""

import ast
import logging
import textwrap
import traceback
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner

from src.ingestion.sources.gog import GOG_CLIENT_SECRET
from src.storage.manager import StorageManager
from src.web.gog_auth import (
    GogAuthError,
    exchange_code_for_tokens,
    extract_code_from_input,
    get_gog_auth_url,
    has_gog_token,
    is_gog_enabled,
    save_gog_token,
)
from tests.cli.conftest import _invoke_with_mocks

GOG_LOGGER = "src.web.gog_auth"


class TestGetGogAuthUrl:
    """Tests for get_gog_auth_url function."""

    def test_returns_valid_url(self) -> None:
        """Test that auth URL is properly formatted."""
        url = get_gog_auth_url()

        assert url.startswith("https://auth.gog.com/auth?")
        assert "client_id=46899977096215655" in url
        assert "response_type=code" in url


class TestExtractCodeFromInput:
    """Tests for extract_code_from_input function."""

    def test_extracts_code_from_raw_input(self) -> None:
        """Test extracting a raw authorization code."""
        code = "oF8OSgZVMFb7a8Y3Dolrz4YPqDUnG7TCTsekYKcWnFNcmWWCJH7XJS3RN9d9NB0s"

        result = extract_code_from_input(code)

        assert result == code

    def test_extracts_code_from_url(self) -> None:
        """Test extracting code from a redirect URL."""
        url = (
            "https://embed.gog.com/on_login_success?origin=client"
            "&code=oF8OSgZVMFb7a8Y3Dolrz4YPqDUnG7TCTsekYKcWnFNcmWWCJH7XJS3RN9d9NB0s"
        )

        result = extract_code_from_input(url)

        assert (
            result == "oF8OSgZVMFb7a8Y3Dolrz4YPqDUnG7TCTsekYKcWnFNcmWWCJH7XJS3RN9d9NB0s"
        )

    def test_raises_error_for_url_without_code(self) -> None:
        """Test that URL without code parameter raises error."""
        url = "https://embed.gog.com/on_login_success?origin=client"

        with pytest.raises(GogAuthError) as exc_info:
            extract_code_from_input(url)

        assert "code" in str(exc_info.value)

    def test_raises_error_for_short_input(self) -> None:
        """Test that short input raises error."""
        with pytest.raises(GogAuthError) as exc_info:
            extract_code_from_input("short")

        assert "too short" in str(exc_info.value)

    def test_strips_whitespace(self) -> None:
        """Test that whitespace is stripped."""
        code = "  oF8OSgZVMFb7a8Y3Dolrz4YPqDUnG7TCTsekYKcWnFNcmWWCJH7XJS3RN9d9NB0s  "

        result = extract_code_from_input(code)

        assert not result.startswith(" ")
        assert not result.endswith(" ")


class TestExchangeCodeForTokens:
    """Tests for exchange_code_for_tokens function."""

    @patch("src.web.gog_auth.requests.get")
    def test_successful_exchange(self, mock_get: MagicMock) -> None:
        """Test successful token exchange."""
        mock_response = MagicMock(spec=requests.Response)
        mock_response.ok = True
        mock_response.json.return_value = {
            "access_token": "access123",
            "refresh_token": "refresh456",
            "expires_in": 3600,
        }
        mock_get.return_value = mock_response

        result = exchange_code_for_tokens("test_code")

        assert result["refresh_token"] == "refresh456"
        assert result["access_token"] == "access123"

    @patch("src.web.gog_auth.requests.get")
    def test_exchange_failure(
        self, mock_get: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test token exchange failure."""
        mock_response = MagicMock(spec=requests.Response)
        mock_response.ok = False
        mock_response.status_code = 400
        mock_response.text = "Invalid code"
        mock_response.json.return_value = {"error_description": "Invalid code"}
        mock_get.return_value = mock_response

        with caplog.at_level(logging.ERROR, logger=GOG_LOGGER):
            with pytest.raises(GogAuthError, match="Token exchange failed"):
                exchange_code_for_tokens("bad_code")

        assert "GOG token exchange failed with status 400" in caplog.text

    @patch("src.web.gog_auth.requests.get")
    def test_missing_refresh_token(self, mock_get: MagicMock) -> None:
        """Test response missing refresh_token."""
        mock_response = MagicMock(spec=requests.Response)
        mock_response.ok = True
        mock_response.json.return_value = {"access_token": "access123"}
        mock_get.return_value = mock_response

        with pytest.raises(GogAuthError) as exc_info:
            exchange_code_for_tokens("test_code")

        assert "refresh_token" in str(exc_info.value)

    @patch("src.web.gog_auth.requests.get")
    def test_network_failure_raises_gog_auth_error(self, mock_get: MagicMock) -> None:
        """Network error during token exchange raises GogAuthError."""
        mock_get.side_effect = requests.RequestException("Connection timed out")

        with pytest.raises(GogAuthError, match="Failed to connect to GOG servers"):
            exchange_code_for_tokens("test_code")


class TestSaveGogToken:
    """Tests for save_gog_token — DB persistence replaces config file writes."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_saves_token_to_db(self, storage: StorageManager) -> None:
        """Token is saved to encrypted DB storage."""
        save_gog_token(storage, "new_refresh_token")

        result = storage.get_credential(1, "gog", "refresh_token")
        assert result == "new_refresh_token"

    def test_overwrites_existing_token(self, storage: StorageManager) -> None:
        """Saving a new token overwrites the old one."""
        save_gog_token(storage, "old_token")
        save_gog_token(storage, "new_token")

        assert storage.get_credential(1, "gog", "refresh_token") == "new_token"

    def test_custom_user_id(self, storage: StorageManager) -> None:
        """Token can be saved for a specific user."""
        # Create user 2
        with storage.connection() as conn:
            conn.execute("INSERT INTO users (id, username) VALUES (2, 'user2')")
            conn.commit()

        save_gog_token(storage, "user2_token", user_id=2)

        assert storage.get_credential(2, "gog", "refresh_token") == "user2_token"
        assert storage.get_credential(1, "gog", "refresh_token") is None

    def test_db_failure_raises_gog_auth_error(self, storage: StorageManager) -> None:
        """DB write failure raises GogAuthError, not the underlying exception."""
        with patch.object(storage, "save_credential", side_effect=OSError("disk full")):
            with pytest.raises(GogAuthError, match="Failed to save GOG token"):
                save_gog_token(storage, "some_token")

    def test_db_failure_logs_the_class_not_a_traceback(
        self, storage: StorageManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Regression: this sink logged a traceback while its Epic twin did not.

        The frames name absolute source paths and say nothing the class name
        does not, so the two save functions render a failure the same way.
        """
        with caplog.at_level(logging.ERROR, logger=GOG_LOGGER):
            with patch.object(
                storage, "save_credential", side_effect=OSError("disk full")
            ):
                with pytest.raises(GogAuthError):
                    save_gog_token(storage, "some_token")

        records = [record for record in caplog.records if record.name == GOG_LOGGER]
        assert [record.getMessage() for record in records] == [
            "Failed to save GOG token to database: OSError"
        ]
        assert not any(record.exc_info for record in records)


class TestIsGogEnabled:
    """Tests for is_gog_enabled function."""

    def test_returns_true_when_enabled(self) -> None:
        """Test returns True when GOG is enabled."""
        config = {"inputs": {"gog": {"enabled": True}}}

        assert is_gog_enabled(config) is True

    def test_returns_false_when_disabled(self) -> None:
        """Test returns False when GOG is disabled."""
        config = {"inputs": {"gog": {"enabled": False}}}

        assert is_gog_enabled(config) is False

    def test_returns_false_when_missing(self) -> None:
        """Test returns False when GOG config is missing."""
        config = {"inputs": {}}

        assert is_gog_enabled(config) is False

    def test_returns_false_when_inputs_missing(self) -> None:
        """Test returns False when inputs section is missing."""
        config = {}

        assert is_gog_enabled(config) is False


class TestHasGogToken:
    """Tests for has_gog_token function."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_returns_true_when_token_in_config(self) -> None:
        """Config-only token is detected (backwards compat)."""
        config = {"inputs": {"gog": {"refresh_token": "some_token"}}}

        assert has_gog_token(config) is True

    def test_returns_false_when_token_empty(self) -> None:
        """Test returns False when refresh token is empty."""
        config = {"inputs": {"gog": {"refresh_token": ""}}}

        assert has_gog_token(config) is False

    def test_returns_false_when_token_missing(self) -> None:
        """Test returns False when refresh token is missing."""
        config = {"inputs": {"gog": {}}}

        assert has_gog_token(config) is False

    def test_returns_false_when_whitespace_only(self) -> None:
        """Test returns False when token is whitespace only."""
        config = {"inputs": {"gog": {"refresh_token": "   "}}}

        assert has_gog_token(config) is False

    def test_returns_true_when_token_in_db(self, storage: StorageManager) -> None:
        """DB token detected even when config has no token."""
        config = {"inputs": {"gog": {"refresh_token": ""}}}
        storage.save_credential(1, "gog", "refresh_token", "db_token")

        assert has_gog_token(config, storage=storage) is True

    def test_config_fallback_when_no_storage(self) -> None:
        """Without storage, only config is checked."""
        config = {"inputs": {"gog": {"refresh_token": "config_token"}}}

        assert has_gog_token(config, storage=None) is True


class TestGogAuthCredentialChainRegression:
    """Regression: the authorization code reached the log via ``__cause__``.

    A scrubbed message still left ``raise ... from error``, whose cause renders
    the token URL under ``exc_info=True`` at ``src/cli/commands.py``. Fix:
    ``from None``, so the CLI's traceback carries only the composed message.
    """

    @patch("src.web.gog_auth.requests.get")
    def test_connect_failure_traceback_omits_the_code_and_secret(
        self, mock_get: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Nothing a caller can render off the raised error names the code."""
        code = "gog-auth-code-7d1b3e"
        mock_get.side_effect = requests.ConnectionError(
            "HTTPSConnectionPool(host='auth.gog.com', port=443): Max retries "
            f"exceeded with url: /token?client_secret={GOG_CLIENT_SECRET}&code={code}"
        )

        with caplog.at_level(logging.ERROR, logger=GOG_LOGGER):
            with pytest.raises(GogAuthError) as raised:
                exchange_code_for_tokens(code)

        rendered = "".join(traceback.format_exception(raised.value))
        assert code not in rendered
        assert GOG_CLIENT_SECRET not in rendered
        assert code not in caplog.text
        assert GOG_CLIENT_SECRET not in caplog.text
        assert "GOG token exchange request failed: ConnectionError" in caplog.text

    @patch("src.web.gog_auth.requests.get")
    def test_unparseable_body_is_scrubbed_too(
        self, mock_get: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``JSONDecodeError`` subclasses ``RequestException``, so it lands here."""
        code = "gog-auth-code-b0f38d5e1c"
        response = MagicMock(spec=requests.Response)
        response.ok = True
        response.json.side_effect = requests.exceptions.JSONDecodeError(
            "Expecting value", "", 0
        )
        mock_get.return_value = response

        with caplog.at_level(logging.ERROR, logger=GOG_LOGGER):
            with pytest.raises(GogAuthError) as raised:
                exchange_code_for_tokens(code)

        rendered = "".join(traceback.format_exception(raised.value))
        assert code not in rendered
        assert GOG_CLIENT_SECRET not in rendered
        assert "GOG token exchange request failed: JSONDecodeError" in caplog.text

    @patch("src.cli.commands.is_gog_enabled", return_value=True)
    @patch("src.web.gog_auth.requests.get")
    def test_cli_connect_logs_no_code_with_its_traceback(
        self,
        mock_get: MagicMock,
        _mock_enabled: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The one caller docs/SECURITY.md names logs the whole chain verbatim."""
        code = "gog-auth-code-9c4e1a70b2"
        mock_get.side_effect = requests.ConnectionError(
            "HTTPSConnectionPool(host='auth.gog.com', port=443): Max retries "
            f"exceeded with url: /token?client_secret={GOG_CLIENT_SECRET}&code={code}"
        )

        with caplog.at_level(logging.ERROR):
            result = _invoke_with_mocks(
                CliRunner(),
                ["auth", "connect", "--source", "gog", "--no-browser"],
                MagicMock(spec=StorageManager),
                input_text=f"{code}\n",
            )

        chained = [record for record in caplog.records if record.exc_info]
        assert chained, "the CLI no longer logs a traceback, so this proves nothing"
        rendered = "".join(
            "".join(traceback.format_exception(*record.exc_info))
            for record in chained
            if record.exc_info
        )
        mock_get.assert_called_once()
        assert "GogAuthError: Failed to connect to GOG servers" in rendered
        assert code not in rendered
        assert GOG_CLIENT_SECRET not in rendered
        assert code not in caplog.text
        assert result.exit_code != 0


# parents[2] resolves /tests/web/test_gog_auth.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]

# GOG's token endpoint takes the refresh token, the authorization code and the
# client secret as query parameters, so a ``requests`` exception raised by
# either of these carries a secret in its text.
_CREDENTIAL_URL_FUNCTIONS = (
    ("src/ingestion/sources/gog/gog.py", "refresh_access_token"),
    ("src/web/gog_auth.py", "exchange_code_for_tokens"),
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

_LEAKY_HANDLERS: dict[str, tuple[str, list[str]]] = {
    "exc_info keyword": (
        'logger.error("failed", exc_info=True)',
        ["fetch_token: logs a traceback of the request error"],
    ),
    "logger.exception": (
        'logger.exception("failed")',
        ["fetch_token: logs a traceback of the request error"],
    ),
    "format_exc interpolated": (
        'logger.error("failed: %s", traceback.format_exc())',
        ["fetch_token: logs a traceback of the request error"],
    ),
    "format_exception bound to a local": (
        "rendered = traceback.format_exception(error)\n"
        'logger.error("failed: %s", rendered)',
        ["fetch_token: logs a traceback of the request error"],
    ),
    "print_exc": (
        "traceback.print_exc()",
        ["fetch_token: logs a traceback of the request error"],
    ),
    "print_exception": (
        "traceback.print_exception(error)",
        ["fetch_token: logs a traceback of the request error"],
    ),
    "raw error as a log argument": (
        'logger.error("failed: %s", error)',
        ["fetch_token: logs the request error unscrubbed"],
    ),
    "raw error interpolated into an f-string": (
        'logger.error(f"failed: {error}")',
        ["fetch_token: logs the request error unscrubbed"],
    ),
    "raw error stringified by hand": (
        'logger.error("failed: %s", str(error))',
        ["fetch_token: logs the request error unscrubbed"],
    ),
    "raw error interpolated with %": (
        'logger.error("failed: %s", "%s" % error)',
        ["fetch_token: logs the request error unscrubbed"],
    ),
    "raw error interpolated with .format": (
        'logger.error("failed: %s", "{}".format(error))',
        ["fetch_token: logs the request error unscrubbed"],
    ),
    "cause kept explicitly": (
        'raise Wrapped("failed") from error',
        ["fetch_token: `raise Wrapped('failed') from error` keeps it as the cause"],
    ),
    "cause kept implicitly": (
        'raise Wrapped("failed")',
        ["fetch_token: `raise Wrapped('failed')` keeps it as the cause"],
    ),
    "request error re-raised": (
        "raise error",
        ["fetch_token: `raise error` keeps it as the cause"],
    ),
    "bare re-raise": (
        "raise",
        ["fetch_token: `raise` keeps it as the cause"],
    ),
}


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
        if isinstance(node, ast.FunctionDef) and node.name == function_name
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


def _module_with_handler(
    tmp_path: Path, handler_body: str, catching: str = "requests.RequestException"
) -> Path:
    """Write a module whose one function handles a credential-bearing request."""
    module = tmp_path / "example.py"
    module.write_text(
        "import requests\n"
        "def fetch_token(code):\n"
        "    try:\n"
        "        return requests.get('https://auth.gog.com/token', params=code)\n"
        f"    except {catching} as error:\n"
        + textwrap.indent(handler_body.strip() + "\n", " " * 8),
        encoding="utf-8",
    )
    return module


class TestCredentialUrlHandlersStayOutOfTracebacks:
    """One rule for both halves of the GOG OAuth flow.

    ``exc_info=True`` at ``src/cli/commands.py`` and ``src/web/sync_manager.py``
    renders whatever these handlers leave behind, so none may render a traceback,
    log its exception unscrubbed, or keep it as a ``__cause__``.
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

    @pytest.mark.parametrize(
        ("handler_body", "expected"),
        list(_LEAKY_HANDLERS.values()),
        ids=list(_LEAKY_HANDLERS),
    )
    def test_the_guard_reports_each_leak(
        self, tmp_path: Path, handler_body: str, expected: list[str]
    ) -> None:
        """A guard that cannot fire would stay green through the bug's return."""
        module = _module_with_handler(tmp_path, handler_body)

        assert _leaky_renderings(module, "fetch_token") == expected

    @pytest.mark.parametrize(
        "catching",
        [
            "requests.RequestException",
            "RequestException",
            "requests.exceptions.HTTPError",
            "HTTPError",
            "(ValueError, RequestException)",
            "Exception",
        ],
    )
    def test_every_handler_spelling_is_judged(
        self, tmp_path: Path, catching: str
    ) -> None:
        """Rewriting the ``except`` clause must not smuggle the handler past."""
        module = _module_with_handler(tmp_path, "raise error", catching=catching)

        assert _leaky_renderings(module, "fetch_token") == [
            "fetch_token: `raise error` keeps it as the cause"
        ]

    def test_a_later_handler_is_judged_too(self, tmp_path: Path) -> None:
        """A leak added below a clean handler is the one a first-match scan misses."""
        module = tmp_path / "example.py"
        module.write_text(
            "import requests\n"
            "def fetch_token(code):\n"
            "    try:\n"
            "        return requests.get('https://auth.gog.com/token', params=code)\n"
            "    except requests.HTTPError:\n"
            "        raise Wrapped('failed') from None\n"
            "    except requests.RequestException as error:\n"
            "        raise error\n",
            encoding="utf-8",
        )

        assert _leaky_renderings(module, "fetch_token") == [
            "fetch_token: `raise error` keeps it as the cause"
        ]

    def test_an_unbound_handler_is_judged(self, tmp_path: Path) -> None:
        """Dropping ``as error`` hides the name the unscrubbed-log check matches on."""
        module = tmp_path / "example.py"
        module.write_text(
            "import requests\n"
            "def fetch_token(code):\n"
            "    try:\n"
            "        return requests.get('https://auth.gog.com/token', params=code)\n"
            "    except requests.RequestException:\n"
            "        logger.exception('failed')\n"
            "        raise\n",
            encoding="utf-8",
        )

        assert _leaky_renderings(module, "fetch_token") == [
            "fetch_token: logs a traceback of the request error",
            "fetch_token: `raise` keeps it as the cause",
        ]

    @pytest.mark.parametrize(
        "logged",
        [
            'logger.error("failed: %s", scrub_request_error(error))',
            'logger.error("failed: %s", exception_for_log(error))',
            'logger.error(f"failed: {scrub_request_error(error)}")',
            'logger.error("failed", exc_info=False)',
        ],
    )
    def test_a_scrubbed_handler_is_not_a_leak(
        self, tmp_path: Path, logged: str
    ) -> None:
        """Flagging the fixed shape would leave nobody a way to pass.

        The last two are the false positives a broader predicate buys: a
        scrubbed value inside an f-string, and the default written out.
        """
        module = _module_with_handler(
            tmp_path,
            f"{logged}\n" 'raise Wrapped("Failed to connect to GOG servers") from None',
        )

        assert _leaky_renderings(module, "fetch_token") == []

    def test_a_missing_function_is_an_error(self, tmp_path: Path) -> None:
        """A rename must break the guard rather than silently empty it."""
        module = _module_with_handler(tmp_path, "raise error")

        with pytest.raises(AssertionError, match="no function named renamed"):
            _leaky_renderings(module, "renamed")

    def test_an_unrelated_handler_is_an_error(self, tmp_path: Path) -> None:
        """A clean report has to mean the credential handler was read and passed."""
        module = _module_with_handler(tmp_path, "raise error", catching="ValueError")

        with pytest.raises(AssertionError, match="no longer catches a request error"):
            _leaky_renderings(module, "fetch_token")

    def test_a_request_moved_into_a_helper_is_an_error(self, tmp_path: Path) -> None:
        """Otherwise a refactor that keeps the symbol quietly retires the guard."""
        module = tmp_path / "example.py"
        module.write_text(
            "def fetch_token(code):\n    return _shared_get(code)\n", encoding="utf-8"
        )

        with pytest.raises(AssertionError, match="no longer catches a request error"):
            _leaky_renderings(module, "fetch_token")
