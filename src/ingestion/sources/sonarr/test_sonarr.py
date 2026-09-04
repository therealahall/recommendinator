from unittest.mock import Mock, patch

import pytest
import requests

from src.ingestion.plugin_base import SourceError
from src.ingestion.sources.sonarr.sonarr import SonarrPlugin


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
    return SonarrPlugin()


@pytest.fixture()
def sample_series() -> list[dict]:
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


class TestSonarrPluginFetch:
    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_all_series(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        mock_get.return_value = _api_response(sample_series)

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        assert len(items) == 3
        assert items[0].title == "Breaking Bad"
        assert items[1].title == "The Expanse"
        assert items[2].title == "Old Unmonitored Show"

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_external_id_format(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
        mock_get.return_value = _api_response(sample_series)

        items = list(
            plugin.fetch({"url": "http://localhost:8989", "api_key": "test_key"})
        )

        assert items[0].id == "tvdb:81189"
        assert items[1].id == "tvdb:280619"

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_fetch_metadata(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
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


class TestSonarrSeasonEpisodeCounts:
    """Without a count per season every season looks the same size, so nothing
    downstream can tell a finished season from a merely caught-up one."""

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_each_season_keeps_its_aired_count_without_specials_or_unaired(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        mock_get.return_value = _api_response(
            [
                {
                    "title": "Severance",
                    "tvdbId": 371980,
                    "seasons": [
                        {"seasonNumber": 0, "statistics": {"episodeCount": 3}},
                        {
                            "seasonNumber": 1,
                            "statistics": {"episodeCount": 9, "totalEpisodeCount": 9},
                        },
                        {
                            "seasonNumber": 2,
                            "statistics": {"episodeCount": 8, "totalEpisodeCount": 10},
                        },
                        {"seasonNumber": 3, "monitored": True},
                    ],
                }
            ]
        )

        items = list(plugin.fetch({"url": "http://localhost:8989", "api_key": "key"}))

        assert items[0].metadata["season_episode_counts"] == {"1": 9, "2": 8}

    @patch("src.ingestion.sources.arr_base.requests.get")
    def test_a_series_with_no_countable_season_writes_no_counts_key(
        self,
        mock_get: Mock,
        plugin: SonarrPlugin,
    ) -> None:
        mock_get.return_value = _api_response(
            [
                {
                    "title": "Specials Only",
                    "tvdbId": 1,
                    "seasons": [{"seasonNumber": 0, "statistics": {"episodeCount": 3}}],
                },
                {"title": "No Seasons Yet", "tvdbId": 2},
            ]
        )

        items = list(plugin.fetch({"url": "http://localhost:8989", "api_key": "key"}))

        assert "season_episode_counts" not in items[0].metadata
        assert "season_episode_counts" not in items[1].metadata


class TestSonarrPluginErrors:
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


class TestSonarrTls:
    @pytest.fixture(autouse=True)
    def _patch_requests(self):
        with patch("src.ingestion.sources.arr_base.requests.get") as mock_get:
            self.mock_get = mock_get
            yield

    def test_an_http_url_is_not_followed_to_https_regression(
        self,
        plugin: SonarrPlugin,
    ) -> None:
        self.mock_get.return_value = _redirect_response(
            "https://sonarr.lan/api/v3/series"
        )

        with pytest.raises(SourceError, match="Refused a redirect") as raised:
            list(plugin.fetch({"url": "http://sonarr.lan", "api_key": "key"}))

        message = str(raised.value)
        assert "http://sonarr.lan/api/v3/series" in message
        assert "https://sonarr.lan/api/v3/series" in message
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

    def test_a_default_port_in_the_location_is_the_same_origin_regression(
        self,
        plugin: SonarrPlugin,
        sample_series: list[dict],
    ) -> None:
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
