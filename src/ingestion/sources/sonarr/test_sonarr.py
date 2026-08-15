"""Tests for Sonarr TV series import plugin."""

from unittest.mock import Mock, patch

import pytest
import requests

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.sonarr.sonarr import SonarrPlugin
from src.models.content import ConsumptionStatus, ContentType


def _api_response(payload: list[dict]) -> Mock:
    response = Mock(spec=requests.Response)
    response.status_code = 200
    response.headers = {}
    response.json.return_value = payload
    response.raise_for_status = Mock()
    return response


def _redirect_response(location: str) -> Mock:
    response = Mock(spec=requests.Response)
    response.status_code = 301
    response.headers = {"Location": location}
    return response


@pytest.fixture()
def plugin() -> SonarrPlugin:
    """Create a SonarrPlugin instance."""
    return SonarrPlugin()


@pytest.fixture()
def sample_series() -> list[dict]:
    """Create sample Sonarr API response data."""
    return [
        {
            "title": "Breaking Bad",
            "monitored": True,
            "tvdbId": 81189,
            "imdbId": "tt0903747",
            "year": 2008,
            "network": "AMC",
            "overview": "A high school chemistry teacher turned meth manufacturer.",
            "genres": ["Drama", "Crime", "Thriller"],
            "seriesType": "standard",
            "status": "ended",
            "ratings": {"value": 9.5},
            "statistics": {
                "seasonCount": 5,
                "episodeCount": 62,
                "episodeFileCount": 62,
            },
        },
        {
            "title": "The Expanse",
            "monitored": True,
            "tvdbId": 280619,
            "year": 2015,
            "network": "Amazon",
            "genres": ["Sci-Fi"],
            "ratings": {"value": 8.4},
            "statistics": {
                "seasonCount": 6,
                "episodeCount": 62,
                "episodeFileCount": 50,
            },
        },
        {
            "title": "Old Unmonitored Show",
            "monitored": False,
            "tvdbId": 99999,
            "year": 2000,
            "ratings": {"value": 5.0},
        },
    ]


class TestSonarrPluginProperties:
    """Tests for SonarrPlugin metadata properties."""

    def test_is_source_plugin(self, plugin: SonarrPlugin) -> None:
        assert isinstance(plugin, SourcePlugin)

    def test_name(self, plugin: SonarrPlugin) -> None:
        assert plugin.name == "sonarr"

    def test_display_name(self, plugin: SonarrPlugin) -> None:
        assert plugin.display_name == "Sonarr"

    def test_content_types(self, plugin: SonarrPlugin) -> None:
        assert plugin.content_types == [ContentType.TV_SHOW]

    def test_requires_api_key(self, plugin: SonarrPlugin) -> None:
        assert plugin.requires_api_key is True

    def test_requires_network(self, plugin: SonarrPlugin) -> None:
        assert plugin.requires_network is True

    def test_config_schema(self, plugin: SonarrPlugin) -> None:
        schema = plugin.get_config_schema()
        names = [field.name for field in schema]
        assert names == ["url", "api_key", "verify_ssl"]
        # api_key should be sensitive
        api_key_field = next(field for field in schema if field.name == "api_key")
        assert api_key_field.sensitive is True

    def test_verify_ssl_is_optional_and_defaults_on(self, plugin: SonarrPlugin) -> None:
        field = next(
            field for field in plugin.get_config_schema() if field.name == "verify_ssl"
        )
        assert field.field_type is bool
        assert field.required is False
        assert field.default is True
        assert field.credential_bound is False

    def test_get_source_identifier(self, plugin: SonarrPlugin) -> None:
        assert plugin.get_source_identifier() == "sonarr"

    def test_get_info(self, plugin: SonarrPlugin) -> None:
        info = plugin.get_info()
        assert info.name == "sonarr"
        assert info.display_name == "Sonarr"
        assert info.requires_api_key is True
        assert info.requires_network is True


class TestSonarrPluginValidation:
    """Tests for SonarrPlugin config validation."""

    def test_validate_valid_config(self, plugin: SonarrPlugin) -> None:
        errors = plugin.validate_config(
            {"url": "http://localhost:8989", "api_key": "abc123"}
        )
        assert errors == []

    def test_validate_missing_api_key(self, plugin: SonarrPlugin) -> None:
        errors = plugin.validate_config({"url": "http://localhost:8989"})
        assert any("api_key" in error for error in errors)

    def test_validate_empty_api_key(self, plugin: SonarrPlugin) -> None:
        errors = plugin.validate_config({"url": "http://localhost:8989", "api_key": ""})
        assert any("api_key" in error for error in errors)

    def test_validate_missing_url(self, plugin: SonarrPlugin) -> None:
        errors = plugin.validate_config({"url": "", "api_key": "abc123"})
        assert any("url" in error for error in errors)


class TestSonarrPluginFetch:
    """Tests for SonarrPlugin fetch functionality."""

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_all_series(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        """All series should be imported regardless of monitored state."""
        mock_get.return_value = _api_response(sample_series)

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        # All 3 series imported (monitored state is ignored)
        assert len(items) == 3
        assert items[0].title == "Breaking Bad"
        assert items[1].title == "The Expanse"
        assert items[2].title == "Old Unmonitored Show"

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_all_items_are_unread(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        """All imported items should have UNREAD status (Sonarr can't track watching)."""
        mock_get.return_value = _api_response(sample_series)

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        for item in items:
            assert item.status == ConsumptionStatus.UNREAD.value

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_content_type_is_tv_show(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        mock_get.return_value = _api_response(sample_series)

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        for item in items:
            assert item.content_type == ContentType.TV_SHOW.value

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_external_id_format(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        """External ID should be tvdb:{tvdbId} for deduplication."""
        mock_get.return_value = _api_response(sample_series)

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        assert items[0].id == "tvdb:81189"
        assert items[1].id == "tvdb:280619"

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_rating_is_none(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        """Sonarr does not track personal ratings; rating should always be None."""
        mock_get.return_value = _api_response(sample_series)

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        for item in items:
            assert item.rating is None

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_metadata(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        """Metadata should include genres, network, seasons, etc."""
        mock_get.return_value = _api_response(sample_series)

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        metadata = items[0].metadata
        assert metadata["tvdb_id"] == 81189
        assert metadata["imdb_id"] == "tt0903747"
        assert metadata["year"] == 2008
        assert metadata["network"] == "AMC"
        assert metadata["genres"] == ["Drama", "Crime", "Thriller"]
        assert metadata["seasons"] == 5
        assert metadata["episodes"] == 62
        assert metadata["downloaded_episodes"] == 62

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_source_identifier(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        mock_get.return_value = _api_response(sample_series)

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        for item in items:
            assert item.source == "sonarr"

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_api_key_sent_in_header(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        """API key should be sent as X-Api-Key header."""
        mock_get.return_value = _api_response([])

        list(plugin.fetch({"url": "http://localhost:8989", "api_key": "my_secret_key"}))

        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["headers"]["X-Api-Key"] == "my_secret_key"

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_correct_api_endpoint(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        """Should call /api/v3/series endpoint."""
        mock_get.return_value = _api_response([])

        list(plugin.fetch({"url": "http://mysonarr:8989", "api_key": "key"}))

        call_args = mock_get.call_args[0]
        assert call_args[0] == "http://mysonarr:8989/api/v3/series"

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_trailing_slash_handled(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        """Trailing slash in URL should not cause double slash."""
        mock_get.return_value = _api_response([])

        list(plugin.fetch({"url": "http://localhost:8989/", "api_key": "key"}))

        call_args = mock_get.call_args[0]
        assert "//" not in call_args[0].replace("http://", "")

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_empty_library(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        mock_get.return_value = _api_response([])

        items = list(plugin.fetch({"url": "http://localhost:8989", "api_key": "key"}))

        assert len(items) == 0

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_skips_empty_title(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        mock_get.return_value = _api_response(
            [
                {"title": "", "monitored": True, "tvdbId": 123},
                {"title": "Valid Show", "monitored": True, "tvdbId": 456},
            ]
        )

        items = list(plugin.fetch({"url": "http://localhost:8989", "api_key": "key"}))

        assert len(items) == 1
        assert items[0].title == "Valid Show"


class TestSonarrPluginErrors:
    """Tests for error handling."""

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_connection_error_raises_source_error(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        mock_get.side_effect = requests.ConnectionError("Connection refused")

        with pytest.raises(SourceError, match="Failed to connect to Sonarr"):
            list(plugin.fetch({"url": "http://localhost:8989", "api_key": "key"}))

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_tls_failure_is_reported_as_tls_not_as_unreachable(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        mock_get.side_effect = requests.exceptions.SSLError(
            "certificate verify failed: unable to get local issuer certificate"
        )

        with pytest.raises(SourceError, match="TLS verification failed") as raised:
            list(plugin.fetch({"url": "https://sonarr.lan", "api_key": "key"}))

        assert "verify_ssl to false" in str(raised.value)

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_http_error_raises_source_error(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        mock_response = _api_response([])
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            "401 Unauthorized"
        )
        mock_get.return_value = mock_response

        with pytest.raises(SourceError, match="Failed to connect to Sonarr"):
            list(plugin.fetch({"url": "http://localhost:8989", "api_key": "bad_key"}))


class TestSonarrTls:
    """Sonarr behind TLS, with and without a verifiable certificate."""

    @pytest.fixture(autouse=True)
    def _patch_requests(self):
        with patch("src.ingestion.sources.arr_base.requests.get") as mock_get:
            self.mock_get = mock_get
            yield

    def test_an_https_url_is_fetched_with_verification_on(
        self,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        self.mock_get.return_value = _api_response(sample_series)

        items = list(plugin.fetch({"url": "https://sonarr.lan", "api_key": "key"}))

        assert len(items) == 3
        assert self.mock_get.call_args[0][0] == "https://sonarr.lan/api/v3/series"
        assert self.mock_get.call_args[1]["verify"] is True

    def test_verify_ssl_false_reaches_the_request(self, plugin: SonarrPlugin) -> None:
        self.mock_get.return_value = _api_response([])

        list(
            plugin.fetch(
                {
                    "url": "https://sonarr.lan",
                    "api_key": "key",
                    "verify_ssl": False,
                }
            )
        )

        assert self.mock_get.call_args[1]["verify"] is False

    def test_an_http_url_is_not_followed_to_https_regression(
        self,
        plugin: SonarrPlugin,
    ) -> None:
        """Bug: a proxy 301'd the configured http url to https.

        ``requests`` followed it, so the sync died on
        CERTIFICATE_VERIFY_FAILED for a scheme the operator never configured.
        A redirect off the origin is refused now.
        """
        self.mock_get.return_value = _redirect_response(
            "https://sonarr.lan/api/v3/series"
        )

        with pytest.raises(SourceError, match="Refused a redirect") as raised:
            list(plugin.fetch({"url": "http://sonarr.lan", "api_key": "key"}))

        message = str(raised.value)
        assert "http://sonarr.lan/api/v3/series" in message
        assert "https://sonarr.lan/api/v3/series" in message
        # Refused, not followed: the redirect target was never requested.
        self.mock_get.assert_called_once()

    def test_a_redirect_target_cannot_rewrite_the_log(
        self,
        plugin: SonarrPlugin,
    ) -> None:
        """``Location`` is server text and the refusal is logged (CWE-117)."""
        self.mock_get.return_value = _redirect_response(
            "https://elsewhere.example/\x1b[2Kforged"
        )

        with pytest.raises(SourceError) as raised:
            list(plugin.fetch({"url": "http://sonarr.lan", "api_key": "key"}))

        assert "\x1b" not in str(raised.value)

    def test_requests_is_told_not_to_follow_redirects_itself(
        self,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        """Nothing else here fails if ``allow_redirects=False`` is dropped.

        Every test patches ``requests.get``, so only the real one would follow
        the 301 and replay ``X-Api-Key`` at the host the proxy named.
        """
        self.mock_get.return_value = _api_response(sample_series)

        list(plugin.fetch({"url": "https://sonarr.lan", "api_key": "key"}))

        assert self.mock_get.call_args[1]["allow_redirects"] is False

    def test_a_same_origin_redirect_is_still_followed(
        self,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        """A relative ``Location`` is what nginx and Sonarr itself send."""
        self.mock_get.side_effect = [
            _redirect_response("/api/v3/series/"),
            _api_response(sample_series),
        ]

        items = list(plugin.fetch({"url": "https://sonarr.lan", "api_key": "key"}))

        assert len(items) == 3
        assert [call[0][0] for call in self.mock_get.call_args_list] == [
            "https://sonarr.lan/api/v3/series",
            "https://sonarr.lan/api/v3/series/",
        ]

    def test_a_location_whose_port_cannot_be_read_is_refused(
        self,
        plugin: SonarrPlugin,
    ) -> None:
        """A url nobody can read is nobody's origin, so it matches nothing."""
        self.mock_get.return_value = _redirect_response(
            "https://sonarr.lan:99999/api/v3/series"
        )

        with pytest.raises(SourceError, match="Refused a redirect"):
            list(plugin.fetch({"url": "https://sonarr.lan", "api_key": "key"}))

        self.mock_get.assert_called_once()

    def test_a_default_port_in_the_location_is_the_same_origin_regression(
        self,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        """Bug: a proxy naming ``:443`` explicitly had its redirect refused.

        The origin was a raw netloc, so ``sonarr.lan:443`` read as a different
        party than ``sonarr.lan`` — stricter than the check guarding the
        stored API key.
        """
        self.mock_get.side_effect = [
            _redirect_response("https://sonarr.lan:443/api/v3/series/"),
            _api_response(sample_series),
        ]

        items = list(plugin.fetch({"url": "https://sonarr.lan", "api_key": "key"}))

        assert len(items) == 3
        assert [call[0][0] for call in self.mock_get.call_args_list] == [
            "https://sonarr.lan/api/v3/series",
            "https://sonarr.lan:443/api/v3/series/",
        ]
