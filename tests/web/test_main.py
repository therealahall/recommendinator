import logging
import os
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.web.app
from src.config.service import BOOTSTRAP_WEB_HOST, BOOTSTRAP_WEB_PORT
from src.web.main import get_local_ip_addresses, main


class TestGetLocalIpAddresses:
    @patch("src.web.main.socket")
    def test_udp_socket_adds_unique_ip(self, mock_socket: MagicMock) -> None:
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


@pytest.fixture(autouse=True)
def _no_real_network(request: pytest.FixtureRequest):
    if request.cls is TestGetLocalIpAddresses:
        yield None
        return
    with patch(
        "src.web.main.get_local_ip_addresses", return_value=["192.168.1.10"]
    ) as mock_ips:
        yield mock_ips


class TestMainBindResolution:
    @patch("src.web.main.uvicorn.run")
    @patch("src.web.main.create_app")
    @patch("src.web.main.load_config")
    def test_missing_web_section_binds_loopback(
        self,
        mock_load_config: MagicMock,
        mock_create_app: MagicMock,
        mock_uvicorn_run: MagicMock,
    ) -> None:
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
        mock_load_config.return_value = {"web": {"host": wildcard}}
        with (
            patch("sys.argv", ["src.web"]),
            caplog.at_level(logging.INFO, logger="src.web.main"),
        ):
            main()

        mock_local_ips.assert_called_once()
        assert "http://192.168.1.10:18473" in caplog.text
        assert "/docs" not in caplog.text

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
        mock_load_config.return_value = {"web": {"host": "192.168.1.50", "debug": True}}
        with (
            patch("sys.argv", ["src.web"]),
            caplog.at_level(logging.INFO, logger="src.web.main"),
        ):
            main()

        mock_local_ips.assert_not_called()
        assert "  - http://192.168.1.50:18473" in caplog.text
        assert "http://192.168.1.50:18473/docs" in caplog.text
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
        mock_load_config.return_value = {"web": {"host": "127.0.0.1", "port": 18473}}
        with patch("sys.argv", ["src.web", "--host", "0.0.0.0", "--port", "8000"]):
            main()

        _, kwargs = mock_uvicorn_run.call_args
        assert kwargs["host"] == "0.0.0.0"
        assert kwargs["port"] == 8000


class TestMainReloadBehavior:
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
        reload_dirs = kwargs["reload_dirs"]
        assert reload_dirs
        assert all(Path(d).is_absolute() for d in reload_dirs)

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
        assert kwargs["reload_dirs"]

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

    @patch("src.web.app.create_app")
    def test_the_import_string_builds_from_the_config_path_main_handed_over(
        self,
        mock_create_app: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config_file = tmp_path / "myconfig.yaml"
        monkeypatch.setenv("CONFIG_PATH", str(config_file))

        built = src.web.app.app

        assert built is mock_create_app.return_value
        assert mock_create_app.call_args.args[0] == config_file
