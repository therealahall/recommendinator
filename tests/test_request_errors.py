"""Tests for the request-error scrubbing utility."""

from unittest.mock import Mock

import requests

from src.utils.request_errors import scrub_request_error


class TestScrubRequestError:
    """Tests for scrub_request_error()."""

    def test_http_error_with_response_returns_status_only(self) -> None:
        """An HTTPError with a response surfaces only the status code."""
        response = Mock(spec=requests.Response)
        response.status_code = 401
        error = requests.HTTPError("401 Client Error", response=response)

        assert scrub_request_error(error) == "HTTP 401"

    def test_http_error_without_response_returns_class_name(self) -> None:
        """An HTTPError lacking a response falls back to the class name."""
        error = requests.HTTPError("boom")

        assert scrub_request_error(error) == "HTTPError"

    def test_connection_error_returns_class_name(self) -> None:
        """Transport errors surface only the exception class name."""
        error = requests.ConnectionError("connection refused")

        assert scrub_request_error(error) == "ConnectionError"

    def test_timeout_returns_class_name(self) -> None:
        """Timeouts surface only the exception class name."""
        error = requests.Timeout("timed out")

        assert scrub_request_error(error) == "Timeout"

    def test_never_leaks_secret_from_http_error_message(self) -> None:
        """A secret embedded in the HTTPError's URL never reaches the output."""
        response = Mock(spec=requests.Response)
        response.status_code = 403
        error = requests.HTTPError(
            "403 Client Error for url: https://api.example.com/x?api_key=SECRET123",
            response=response,
        )

        result = scrub_request_error(error)

        assert "SECRET123" not in result
        assert "api_key=" not in result
        assert result == "HTTP 403"

    def test_never_leaks_secret_from_transport_error_message(self) -> None:
        """A secret embedded in a transport error's message never leaks."""
        error = requests.ConnectionError(
            "Failed to connect to https://api.example.com/x?key=SECRET123"
        )

        result = scrub_request_error(error)

        assert "SECRET123" not in result
        assert "key=" not in result
        assert result == "ConnectionError"
