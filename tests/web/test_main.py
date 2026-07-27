"""Tests for web server entry point utilities."""

import logging
import os
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.cli.config import BOOTSTRAP_WEB_HOST, BOOTSTRAP_WEB_PORT
from src.web.main import get_local_ip_addresses, main


class TestGetLocalIpAddresses:
    """Tests for get_local_ip_addresses function."""

    @patch("src.web.main.socket")
    def test_returns_valid_ips_from_gethostbyname_ex(
        self, mock_socket: MagicMock
    ) -> None:
        """Test that valid non-Docker, non-localhost IPs are returned."""
        mock_socket.gethostname.return_value = "myhost"
        mock_socket.gethostbyname_ex.return_value = (
            "myhost",
            [],
            ["192.168.1.100"],
        )
        mock_socket.AF_INET = 2
        mock_socket.SOCK_DGRAM = 2
        mock_udp_socket = MagicMock(spec=socket.socket)
        mock_udp_socket.getsockname.return_value = ("192.168.1.100", 12345)
        mock_socket.socket.return_value = mock_udp_socket

        result = get_local_ip_addresses()

        assert "192.168.1.100" in result

    @patch("src.web.main.socket")
    def test_filters_docker_network_addresses(self, mock_socket: MagicMock) -> None:
        """Test that Docker network IPs (172.x) are excluded."""
        mock_socket.gethostname.return_value = "myhost"
        mock_socket.gethostbyname_ex.return_value = (
            "myhost",
            [],
            ["172.17.0.1", "192.168.1.100"],
        )
        mock_socket.AF_INET = 2
        mock_socket.SOCK_DGRAM = 2
        mock_udp_socket = MagicMock(spec=socket.socket)
        mock_udp_socket.getsockname.return_value = ("192.168.1.100", 12345)
        mock_socket.socket.return_value = mock_udp_socket

        result = get_local_ip_addresses()

        assert "172.17.0.1" not in result
        assert "192.168.1.100" in result

    @patch("src.web.main.socket")
    def test_filters_localhost_addresses(self, mock_socket: MagicMock) -> None:
        """Test that localhost IPs (127.x) are excluded."""
        mock_socket.gethostname.return_value = "myhost"
        mock_socket.gethostbyname_ex.return_value = (
            "myhost",
            [],
            ["127.0.0.1", "10.0.0.5"],
        )
        mock_socket.AF_INET = 2
        mock_socket.SOCK_DGRAM = 2
        mock_udp_socket = MagicMock(spec=socket.socket)
        mock_udp_socket.getsockname.return_value = ("10.0.0.5", 12345)
        mock_socket.socket.return_value = mock_udp_socket

        result = get_local_ip_addresses()

        assert "127.0.0.1" not in result
        assert "10.0.0.5" in result

    @patch("src.web.main.socket")
    def test_gethostbyname_ex_exception_handled_silently(
        self, mock_socket: MagicMock
    ) -> None:
        """Test that gethostbyname_ex exceptions are silently caught."""
        mock_socket.gethostname.return_value = "myhost"
        mock_socket.gethostbyname_ex.side_effect = OSError("DNS lookup failed")
        mock_socket.AF_INET = 2
        mock_socket.SOCK_DGRAM = 2
        mock_udp_socket = MagicMock(spec=socket.socket)
        mock_udp_socket.getsockname.return_value = ("192.168.1.100", 12345)
        mock_socket.socket.return_value = mock_udp_socket

        result = get_local_ip_addresses()

        assert result == ["192.168.1.100"]

    @patch("src.web.main.socket")
    def test_udp_socket_connect_exception_handled_silently(
        self, mock_socket: MagicMock
    ) -> None:
        """Test that UDP socket connect exceptions are silently caught."""
        mock_socket.gethostname.return_value = "myhost"
        mock_socket.gethostbyname_ex.return_value = (
            "myhost",
            [],
            ["192.168.1.100"],
        )
        mock_socket.AF_INET = 2
        mock_socket.SOCK_DGRAM = 2
        mock_udp_socket = MagicMock(spec=socket.socket)
        mock_udp_socket.connect.side_effect = OSError("Network unreachable")
        mock_socket.socket.return_value = mock_udp_socket

        result = get_local_ip_addresses()

        assert result == ["192.168.1.100"]

    @patch("src.web.main.socket")
    def test_both_methods_fail_returns_empty_list(self, mock_socket: MagicMock) -> None:
        """Test that empty list is returned when both methods fail."""
        mock_socket.gethostname.return_value = "myhost"
        mock_socket.gethostbyname_ex.side_effect = OSError("DNS lookup failed")
        mock_socket.AF_INET = 2
        mock_socket.SOCK_DGRAM = 2
        mock_udp_socket = MagicMock(spec=socket.socket)
        mock_udp_socket.connect.side_effect = OSError("Network unreachable")
        mock_socket.socket.return_value = mock_udp_socket

        result = get_local_ip_addresses()

        assert result == []

    @patch("src.web.main.socket")
    def test_deduplicates_ips_from_both_methods(self, mock_socket: MagicMock) -> None:
        """Test that duplicate IPs found by both methods appear only once."""
        mock_socket.gethostname.return_value = "myhost"
        mock_socket.gethostbyname_ex.return_value = (
            "myhost",
            [],
            ["192.168.1.100"],
        )
        mock_socket.AF_INET = 2
        mock_socket.SOCK_DGRAM = 2
        mock_udp_socket = MagicMock(spec=socket.socket)
        mock_udp_socket.getsockname.return_value = ("192.168.1.100", 12345)
        mock_socket.socket.return_value = mock_udp_socket

        result = get_local_ip_addresses()

        assert result == ["192.168.1.100"]
        assert result.count("192.168.1.100") == 1

    @patch("src.web.main.socket")
    def test_udp_socket_adds_unique_ip(self, mock_socket: MagicMock) -> None:
        """Test that UDP socket method adds an IP not found by gethostbyname_ex."""
        mock_socket.gethostname.return_value = "myhost"
        mock_socket.gethostbyname_ex.return_value = (
            "myhost",
            [],
            ["192.168.1.100"],
        )
        mock_socket.AF_INET = 2
        mock_socket.SOCK_DGRAM = 2
        mock_udp_socket = MagicMock(spec=socket.socket)
        mock_udp_socket.getsockname.return_value = ("10.0.0.5", 12345)
        mock_socket.socket.return_value = mock_udp_socket

        result = get_local_ip_addresses()

        assert "192.168.1.100" in result
        assert "10.0.0.5" in result
        assert len(result) == 2

    @patch("src.web.main.socket")
    def test_filters_all_docker_and_localhost_keeps_valid(
        self, mock_socket: MagicMock
    ) -> None:
        """Test filtering with mixed Docker, localhost, and valid IPs."""
        mock_socket.gethostname.return_value = "myhost"
        mock_socket.gethostbyname_ex.return_value = (
            "myhost",
            [],
            ["127.0.0.1", "172.17.0.1", "172.18.0.1", "10.0.0.5", "192.168.1.50"],
        )
        mock_socket.AF_INET = 2
        mock_socket.SOCK_DGRAM = 2
        mock_udp_socket = MagicMock(spec=socket.socket)
        mock_udp_socket.getsockname.return_value = ("10.0.0.5", 12345)
        mock_socket.socket.return_value = mock_udp_socket

        result = get_local_ip_addresses()

        assert "127.0.0.1" not in result
        assert "172.17.0.1" not in result
        assert "172.18.0.1" not in result
        assert "10.0.0.5" in result
        assert "192.168.1.50" in result
        assert len(result) == 2

    @patch("src.web.main.socket")
    def test_gethostbyname_ex_returns_only_filtered_ips(
        self, mock_socket: MagicMock
    ) -> None:
        """Test that empty result from gethostbyname_ex when all IPs are filtered."""
        mock_socket.gethostname.return_value = "myhost"
        mock_socket.gethostbyname_ex.return_value = (
            "myhost",
            [],
            ["127.0.0.1", "172.17.0.1"],
        )
        mock_socket.AF_INET = 2
        mock_socket.SOCK_DGRAM = 2
        mock_udp_socket = MagicMock(spec=socket.socket)
        mock_udp_socket.connect.side_effect = OSError("Network unreachable")
        mock_socket.socket.return_value = mock_udp_socket

        result = get_local_ip_addresses()

        assert result == []

    @patch("src.web.main.socket")
    def test_udp_socket_is_closed_after_use(self, mock_socket: MagicMock) -> None:
        """Test that the UDP socket is properly closed after getting the IP."""
        mock_socket.gethostname.return_value = "myhost"
        mock_socket.gethostbyname_ex.return_value = ("myhost", [], [])
        mock_socket.AF_INET = 2
        mock_socket.SOCK_DGRAM = 2
        mock_udp_socket = MagicMock(spec=socket.socket)
        mock_udp_socket.getsockname.return_value = ("192.168.1.100", 12345)
        mock_socket.socket.return_value = mock_udp_socket

        get_local_ip_addresses()

        mock_udp_socket.close.assert_called_once()


@pytest.fixture(autouse=True)
def _no_real_network(request: pytest.FixtureRequest):
    """Keep every main() test off the real network, structurally.

    ``get_local_ip_addresses`` does a real ``gethostbyname_ex`` and opens a real
    UDP socket to 8.8.8.8, both wrapped in bare ``except Exception: pass``. Tests
    that call main() avoid it today only because the bind default happens to be
    loopback — so a change to BOOTSTRAP_WEB_HOST, or ``127.0.0.1`` joining
    _WILDCARD_HOSTS, would start doing DNS and an outbound UDP association on
    every CI run with the suite still green. Patch it for everyone; the tests
    that care assert on the mock explicitly.
    """
    # TestGetLocalIpAddresses exercises the real function and patches socket
    # itself, so it must see the genuine implementation. Compared by identity,
    # not by name: a name comparison silently stops matching if the class is
    # renamed, and the opt-out fails open.
    if request.cls is TestGetLocalIpAddresses:
        yield None
        return
    with patch(
        "src.web.main.get_local_ip_addresses", return_value=["192.168.1.10"]
    ) as mock_ips:
        yield mock_ips


class TestMainBindResolution:
    """main() must never bind more broadly than the operator asked for.

    The app ships no authentication, so the bind address is a security control:
    a wildcard bind exposes the whole library, read/write, to the local network.
    ``web.host``/``web.port`` are deliberately NOT settings-registry leaves —
    the launcher resolves them before any database is open — so this is the only
    place the resulting value is decided, and the only place it can be pinned.
    """

    @patch("src.web.main.uvicorn.run")
    @patch("src.web.main.create_app")
    @patch("src.web.main.load_config")
    def test_missing_web_section_binds_loopback(
        self,
        mock_load_config: MagicMock,
        mock_create_app: MagicMock,
        mock_uvicorn_run: MagicMock,
    ) -> None:
        """No ``web`` section at all falls back to loopback, not a wildcard.

        Regression: the fallbacks were ``0.0.0.0``/``8000``, so a config without
        a ``web`` section silently published an unauthenticated instance to the
        network.
        """
        mock_load_config.return_value = {}
        with patch("sys.argv", ["src.web"]):
            main()

        _, kwargs = mock_uvicorn_run.call_args
        assert kwargs["host"] == BOOTSTRAP_WEB_HOST
        assert kwargs["port"] == BOOTSTRAP_WEB_PORT

    @pytest.mark.parametrize("blank", [None, ""])
    @patch("src.web.main.uvicorn.run")
    @patch("src.web.main.create_app")
    @patch("src.web.main.load_config")
    def test_blank_yaml_host_falls_back_to_loopback(
        self,
        mock_load_config: MagicMock,
        mock_create_app: MagicMock,
        mock_uvicorn_run: MagicMock,
        blank: str | None,
    ) -> None:
        """A present-but-blank ``host:`` must not become a wildcard bind.

        Regression: the fallback was a ``dict.get`` default, which only fires
        when the key is ABSENT. ``host:`` with no value parses to None and
        ``host: ""`` to the empty string; both are INADDR_ANY at the socket
        layer, so blanking the value — the obvious edit for someone who wants
        the default back — silently published the instance to the network.
        """
        mock_load_config.return_value = {"web": {"host": blank}}
        with patch("sys.argv", ["src.web"]):
            main()

        _, kwargs = mock_uvicorn_run.call_args
        assert kwargs["host"] == BOOTSTRAP_WEB_HOST

    @pytest.mark.parametrize("wildcard", ["0.0.0.0", "::"])
    @patch("src.web.main.get_local_ip_addresses", return_value=["192.168.1.10"])
    @patch("src.web.main.uvicorn.run")
    @patch("src.web.main.create_app")
    @patch("src.web.main.load_config")
    def test_wildcard_bind_is_reported_as_network_reachable(
        self,
        mock_load_config: MagicMock,
        mock_create_app: MagicMock,
        mock_uvicorn_run: MagicMock,
        mock_local_ips: MagicMock,
        wildcard: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A wildcard bind must be announced with its reachable addresses.

        The banner is a safety control: an operator must never be told an exposed
        instance is localhost-only. Printing the host literally would emit
        "http://:::18473" for the IPv6 wildcard and skip the LAN enumeration
        entirely. Without this, removing "::" from _WILDCARD_HOSTS — or emptying
        the set — breaks nothing in the suite.
        """
        mock_load_config.return_value = {"web": {"host": wildcard}}
        with (
            patch("sys.argv", ["src.web"]),
            caplog.at_level(logging.INFO, logger="src.web.main"),
        ):
            main()

        mock_local_ips.assert_called_once()
        assert "http://192.168.1.10:18473" in caplog.text
        # Debug is off by default, and create_app leaves docs_url/redoc_url/
        # openapi_url unset in that case — so the banner must not advertise a
        # URL that 404s.
        assert "/docs" not in caplog.text

    @patch("src.web.main.uvicorn.run")
    @patch("src.web.main.create_app")
    @patch("src.web.main.load_config")
    def test_debug_banner_advertises_docs_at_localhost_not_the_wildcard(
        self,
        mock_load_config: MagicMock,
        mock_create_app: MagicMock,
        mock_uvicorn_run: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """With debug on, the docs line appears and uses a reachable host.

        Printing the wildcard literally would emit "http://0.0.0.0:18473/docs",
        which is not a URL anyone can open.
        """
        mock_load_config.return_value = {"web": {"host": "0.0.0.0", "debug": True}}
        with (
            patch("sys.argv", ["src.web"]),
            caplog.at_level(logging.INFO, logger="src.web.main"),
        ):
            main()

        assert "http://localhost:18473/docs" in caplog.text

    @patch("src.web.main.get_local_ip_addresses")
    @patch("src.web.main.uvicorn.run")
    @patch("src.web.main.create_app")
    @patch("src.web.main.load_config")
    def test_specific_host_is_reported_literally_and_skips_lan_enumeration(
        self,
        mock_load_config: MagicMock,
        mock_create_app: MagicMock,
        mock_uvicorn_run: MagicMock,
        mock_local_ips: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A non-wildcard bind is announced at its own address, not localhost.

        A socket bound to 192.168.1.50 alone is NOT reachable at localhost, so
        substituting it in either banner line hands the operator a dead URL.
        Without this the whole non-wildcard branch is unasserted: deleting the
        ``else`` arm, or hardcoding the docs host to "localhost", both pass.
        """
        mock_load_config.return_value = {"web": {"host": "192.168.1.50", "debug": True}}
        with (
            patch("sys.argv", ["src.web"]),
            caplog.at_level(logging.INFO, logger="src.web.main"),
        ):
            main()

        mock_local_ips.assert_not_called()
        assert "  - http://192.168.1.50:18473" in caplog.text
        assert "http://192.168.1.50:18473/docs" in caplog.text
        # Neither banner line may offer localhost: the socket is not bound
        # there, so both would be dead URLs.
        assert "localhost" not in caplog.text

    @patch("src.web.main.uvicorn.run")
    @patch("src.web.main.create_app")
    @patch("src.web.main.load_config")
    def test_yaml_web_section_overrides_bootstrap_defaults(
        self,
        mock_load_config: MagicMock,
        mock_create_app: MagicMock,
        mock_uvicorn_run: MagicMock,
    ) -> None:
        """An explicit YAML value still wins — opting in to a wildcard works.

        A wildcard host sends the startup banner down the LAN-enumeration
        branch, which would otherwise do real DNS resolution and open a real UDP
        socket — the ``_no_real_network`` autouse fixture holds that off.
        """
        mock_load_config.return_value = {"web": {"host": "0.0.0.0", "port": 9000}}
        with patch("sys.argv", ["src.web"]):
            main()

        _, kwargs = mock_uvicorn_run.call_args
        assert kwargs["host"] == "0.0.0.0"
        assert kwargs["port"] == 9000

    @patch("src.web.main.uvicorn.run")
    @patch("src.web.main.create_app")
    @patch("src.web.main.load_config")
    def test_explicit_port_zero_requests_an_ephemeral_port(
        self,
        mock_load_config: MagicMock,
        mock_create_app: MagicMock,
        mock_uvicorn_run: MagicMock,
    ) -> None:
        """``--port 0`` is a real request for an ephemeral port, not a blank value.

        Regression: an ``or`` chain treated 0 as falsy and silently substituted
        the bootstrap default, so a caller asking the OS to pick a free port
        (test harnesses, side-by-side instances) silently got 18473 instead.
        """
        mock_load_config.return_value = {"web": {"port": 18473}}
        with patch("sys.argv", ["src.web", "--port", "0"]):
            main()

        _, kwargs = mock_uvicorn_run.call_args
        assert kwargs["port"] == 0

    @patch("src.web.main.uvicorn.run")
    @patch("src.web.main.create_app")
    @patch("src.web.main.load_config")
    def test_cli_flags_beat_yaml_bind_settings(
        self,
        mock_load_config: MagicMock,
        mock_create_app: MagicMock,
        mock_uvicorn_run: MagicMock,
    ) -> None:
        """``--host``/``--port`` outrank config.yaml — Docker depends on this.

        The image's CMD is ``python -m src.web --host 0.0.0.0 --port 8000`` while
        the entrypoint seeds config.yaml from example.yaml, which now carries
        ``host: 127.0.0.1``. If this precedence ever inverted, uvicorn would bind
        loopback inside the container and the published port mapping would go
        dead for every Docker install.
        """
        mock_load_config.return_value = {"web": {"host": "127.0.0.1", "port": 18473}}
        with patch("sys.argv", ["src.web", "--host", "0.0.0.0", "--port", "8000"]):
            main()

        _, kwargs = mock_uvicorn_run.call_args
        assert kwargs["host"] == "0.0.0.0"
        assert kwargs["port"] == 8000


class TestMainReloadBehavior:
    """Verify how main() decides between reload mode and production mode.

    Two inputs activate reload: the --reload CLI flag (used by the dev compose
    override) or web.debug=true in config (legacy behavior). The branches call
    uvicorn.run with structurally different first arguments — an import string
    for reload, the app object for production — so a regression in this logic
    silently breaks either dev hot-reload or production startup.
    """

    @patch("src.web.main.uvicorn.run")
    @patch("src.web.main.create_app")
    @patch("src.web.main.load_config")
    def test_reload_flag_uses_import_string_and_dirs(
        self,
        mock_load_config: MagicMock,
        mock_create_app: MagicMock,
        mock_uvicorn_run: MagicMock,
    ) -> None:
        mock_load_config.return_value = {}
        with patch("sys.argv", ["src.web", "--reload"]):
            main()

        mock_uvicorn_run.assert_called_once()
        args, kwargs = mock_uvicorn_run.call_args
        assert args[0] == "src.web.app:app"
        assert kwargs["reload"] is True
        # reload_dirs must be absolute paths to the project's src and templates
        # so --reload works regardless of cwd.
        reload_dirs = kwargs["reload_dirs"]
        assert len(reload_dirs) == 2
        assert all(Path(d).is_absolute() for d in reload_dirs)
        assert reload_dirs[0].endswith("/src")
        assert reload_dirs[1].endswith("/templates")

    @patch("src.web.main.uvicorn.run")
    @patch("src.web.main.create_app")
    @patch("src.web.main.load_config")
    def test_web_debug_config_activates_reload_without_flag(
        self,
        mock_load_config: MagicMock,
        mock_create_app: MagicMock,
        mock_uvicorn_run: MagicMock,
    ) -> None:
        mock_load_config.return_value = {"web": {"debug": True}}
        with patch("sys.argv", ["src.web"]):
            main()

        args, kwargs = mock_uvicorn_run.call_args
        assert args[0] == "src.web.app:app"
        assert kwargs["reload"] is True
        # reload_dirs must apply on every reload-enabled path, not just --reload
        assert len(kwargs["reload_dirs"]) == 2

    @patch("src.web.main.uvicorn.run")
    @patch("src.web.main.create_app")
    @patch("src.web.main.load_config")
    def test_no_flag_no_debug_uses_app_object(
        self,
        mock_load_config: MagicMock,
        mock_create_app: MagicMock,
        mock_uvicorn_run: MagicMock,
    ) -> None:
        mock_load_config.return_value = {}
        sentinel_app = MagicMock(name="created_app")
        mock_create_app.return_value = sentinel_app
        with patch("sys.argv", ["src.web"]):
            main()

        args, kwargs = mock_uvicorn_run.call_args
        # First positional arg must be the app object, not an import string
        assert args[0] is sentinel_app
        assert kwargs["reload"] is False
        assert "reload_dirs" not in kwargs

    @patch.dict(os.environ, {}, clear=True)
    @patch("src.web.main.uvicorn.run")
    @patch("src.web.main.create_app")
    @patch("src.web.main.load_config")
    def test_reload_with_config_path_sets_env_var(
        self,
        mock_load_config: MagicMock,
        mock_create_app: MagicMock,
        mock_uvicorn_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        config_file = tmp_path / "myconfig.yaml"
        config_file.write_text("web:\n  debug: false\n")
        mock_load_config.return_value = {}
        with patch("sys.argv", ["src.web", "--reload", "--config", str(config_file)]):
            main()

        assert os.environ["CONFIG_PATH"] == str(config_file.resolve())

    @patch.dict(os.environ, {}, clear=True)
    @patch("src.web.main.uvicorn.run")
    @patch("src.web.main.create_app")
    @patch("src.web.main.load_config")
    def test_reload_without_config_path_leaves_env_unchanged(
        self,
        mock_load_config: MagicMock,
        mock_create_app: MagicMock,
        mock_uvicorn_run: MagicMock,
    ) -> None:
        mock_load_config.return_value = {}
        with patch("sys.argv", ["src.web", "--reload"]):
            main()

        # No --config supplied: CONFIG_PATH must not be injected into env.
        assert "CONFIG_PATH" not in os.environ
