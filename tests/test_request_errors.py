from unittest.mock import Mock

import requests

from src.utils.request_errors import scrub_request_error


class TestScrubRequestError:
    def test_http_error_with_response_returns_status_only(self) -> None:
        response = Mock(spec=requests.Response)
        response.status_code = 401
        error = requests.HTTPError("401 Client Error", response=response)

        assert scrub_request_error(error) == "HTTP 401"

    def test_connection_error_returns_class_name(self) -> None:
        error = requests.ConnectionError("connection refused")

        assert scrub_request_error(error) == "ConnectionError"

    def test_never_leaks_secret_from_http_error_message(self) -> None:
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
