"""Tests for web server entry point utilities."""

from unittest.mock import MagicMock, patch

from src.web.main import get_local_ip_addresses


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
        mock_udp_socket = MagicMock()
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
        mock_udp_socket = MagicMock()
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
        mock_udp_socket = MagicMock()
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
        mock_udp_socket = MagicMock()
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
        mock_udp_socket = MagicMock()
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
        mock_udp_socket = MagicMock()
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
        mock_udp_socket = MagicMock()
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
        mock_udp_socket = MagicMock()
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
        mock_udp_socket = MagicMock()
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
        mock_udp_socket = MagicMock()
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
        mock_udp_socket = MagicMock()
        mock_udp_socket.getsockname.return_value = ("192.168.1.100", 12345)
        mock_socket.socket.return_value = mock_udp_socket

        get_local_ip_addresses()

        mock_udp_socket.close.assert_called_once()
