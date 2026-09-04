from collections.abc import Callable
from datetime import date
from typing import Any
from unittest.mock import Mock, patch

import pytest
import requests

from src.ingestion.plugin_base import SourceError
from src.ingestion.sources.tautulli.tautulli import TautulliPlugin
from src.models.content import ConsumptionStatus, ContentType

_USER = "aaron"

_API_KEY = "secret-key"

_CONFIG = {
    "url": "http://localhost:8181",
    "api_key": _API_KEY,
    "username": _USER,
}

# 2024-02-20T12:00:00Z and 2024-03-02T02:30:00Z, the second of which falls on
# the previous day in every zone west of UTC.
_EARLIER_PLAY = 1708430400
_LATER_PLAY = 1709346600

_MAX_PAGES = 4


def _response(payload: dict[str, Any]) -> Mock:
    response = Mock(spec=requests.Response)
    response.status_code = 200
    response.headers = {}
    response.json.return_value = payload
    response.raise_for_status = Mock()
    return response


def _movie_row(title: str, **overrides: Any) -> dict[str, Any]:
    return {
        "date": _EARLIER_PLAY,
        "watched_status": 1,
        "media_type": "movie",
        "title": title,
        "full_title": title,
        "year": 2019,
        "rating_key": 0,
        "user": _USER,
        **overrides,
    }


def _episode_row(show: str, **overrides: Any) -> dict[str, Any]:
    season = overrides.pop("season", 1)
    episode = overrides.pop("episode", 1)
    return {
        "date": _EARLIER_PLAY,
        "watched_status": 1,
        "media_type": "episode",
        "title": f"Episode {episode}",
        "full_title": f"{show} - Episode {episode}",
        "grandparent_title": show,
        "parent_media_index": season,
        "media_index": episode,
        "year": 2022,
        "rating_key": 0,
        "grandparent_rating_key": 0,
        "user": _USER,
        **overrides,
    }


class _FakeTautulli:
    """Answers off the query string, so pagination and the per-command routing
    are exercised rather than assumed by a fixed list of responses.
    """

    def __init__(
        self,
        movies: list[dict[str, Any]] | None = None,
        episodes: list[dict[str, Any]] | None = None,
        children: dict[int, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.movies = movies or []
        self.episodes = episodes or []
        self.children = children or {}
        self.queries: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> Mock:
        params = kwargs["params"]
        self.queries.append(params)

        if params["cmd"] == "get_history":
            rows = self.movies if params["media_type"] == "movie" else self.episodes
            start = params["start"]
            # A cursor that stopped advancing would page forever, which as a
            # hang is a test nobody can read.
            assert len(self.history_calls(params["media_type"])) <= _MAX_PAGES
            page = rows[start : start + params["length"]]
            return _response(
                {
                    "response": {
                        "result": "success",
                        "data": {
                            "recordsFiltered": len(rows),
                            "recordsTotal": len(rows),
                            "data": page,
                        },
                    }
                }
            )

        assert params["cmd"] == "get_children_metadata"
        children = self.children.get(int(params["rating_key"]))
        if children is None:
            return _response(
                {"response": {"result": "error", "message": "no metadata"}}
            )
        return _response(
            {
                "response": {
                    "result": "success",
                    "data": {"children_list": children},
                }
            }
        )

    def history_calls(self, media_type: str) -> list[dict[str, Any]]:
        return [
            query
            for query in self.queries
            if query["cmd"] == "get_history" and query["media_type"] == media_type
        ]


@pytest.fixture()
def plugin() -> TautulliPlugin:
    return TautulliPlugin()


def _fetch(server: _FakeTautulli, plugin: TautulliPlugin, **config: Any) -> list[Any]:
    with patch("src.ingestion.sources.tautulli.tautulli.requests.get", server):
        return list(plugin.fetch({**_CONFIG, **config}))


class TestMovies:
    def test_a_watched_film_imports_as_completed_and_unrated(
        self, plugin: TautulliPlugin
    ) -> None:
        """Tautulli records plays, so a rating carried here would be invented."""
        server = _FakeTautulli(movies=[_movie_row("Arrival", year=2016)])

        items = _fetch(server, plugin)

        assert [(item.title, item.content_type) for item in items] == [
            ("Arrival", ContentType.MOVIE)
        ]
        assert items[0].status == ConsumptionStatus.COMPLETED
        assert items[0].rating is None
        assert items[0].metadata["year"] == 2016
        assert items[0].id == "tautulli:movie:arrival:2016"

    def test_the_completion_date_is_the_local_day_of_the_latest_play(
        self, plugin: TautulliPlugin, host_timezone: Callable[[str], None]
    ) -> None:
        """The later play is 02:30 UTC, which is still the previous evening in
        New York, so reading the stamp as UTC dates the film a day late.
        """
        host_timezone("America/New_York")
        server = _FakeTautulli(
            movies=[
                _movie_row("Arrival", date=_EARLIER_PLAY),
                _movie_row("Arrival", date=_LATER_PLAY),
            ]
        )

        items = _fetch(server, plugin)

        assert len(items) == 1
        assert items[0].date_completed == date(2024, 3, 1)


class TestShows:
    def test_an_episode_watched_twice_counts_once_in_its_season(
        self, plugin: TautulliPlugin
    ) -> None:
        server = _FakeTautulli(
            episodes=[
                _episode_row("The Bear", episode=1),
                _episode_row("The Bear", episode=1, date=_LATER_PLAY),
                _episode_row("The Bear", episode=2),
            ]
        )

        items = _fetch(server, plugin)

        assert [(item.title, item.content_type) for item in items] == [
            ("The Bear", ContentType.TV_SHOW)
        ]
        assert items[0].status == ConsumptionStatus.CURRENTLY_CONSUMING
        assert items[0].metadata["episodes_watched_by_season"] == {1: 2}

    def test_each_season_carries_the_timestamp_of_its_latest_play(
        self, plugin: TautulliPlugin
    ) -> None:
        """The variety ladder reads these dates, so an earlier rewatch winning
        would move a show back down it.
        """
        server = _FakeTautulli(
            episodes=[
                _episode_row("The Bear", season=1, episode=1, date=_LATER_PLAY),
                _episode_row("The Bear", season=1, episode=2, date=_EARLIER_PLAY),
                _episode_row("The Bear", season=2, episode=1, date=_EARLIER_PLAY),
            ]
        )

        items = _fetch(server, plugin)

        assert items[0].metadata["seasons_watched_dates"] == {
            "1": "2024-03-02T02:30:00+00:00",
            "2": "2024-02-20T12:00:00+00:00",
        }

    def test_a_show_plex_still_holds_carries_its_season_episode_counts(
        self, plugin: TautulliPlugin
    ) -> None:
        server = _FakeTautulli(
            episodes=[_episode_row("The Bear", grandparent_rating_key=55)],
            children={
                55: [
                    {"media_index": 1, "children_count": 8},
                    {"media_index": 2, "children_count": 10},
                ]
            },
        )

        items = _fetch(server, plugin)

        assert items[0].metadata["plex_season_episode_counts"] == {1: 8, 2: 10}

    def test_a_show_with_no_live_rating_key_still_imports(
        self, plugin: TautulliPlugin
    ) -> None:
        """Plex strips the key when the file goes, which is most of a long
        history: asking about key 0 would fail and skipping the row would lose
        everything the operator watched.
        """
        server = _FakeTautulli(episodes=[_episode_row("Severance")])

        items = _fetch(server, plugin)

        assert items[0].title == "Severance"
        assert items[0].metadata["episodes_watched_by_season"] == {1: 1}
        assert items[0].metadata["plex_season_episode_counts"] == {}
        assert [query["cmd"] for query in server.queries] == [
            "get_history",
            "get_history",
        ]

    def test_a_show_key_that_has_gone_stale_does_not_end_the_sync(
        self, plugin: TautulliPlugin
    ) -> None:
        """A key live in the history can be dead by the time it is asked about,
        and one stale show must not cost the operator the whole sync.
        """
        server = _FakeTautulli(
            episodes=[_episode_row("The Bear", grandparent_rating_key=55)],
            children={},
        )

        items = _fetch(server, plugin)

        assert items[0].title == "The Bear"
        assert items[0].metadata["plex_season_episode_counts"] == {}

    def test_season_zero_never_reaches_any_mapping(
        self, plugin: TautulliPlugin
    ) -> None:
        """A special is not part of a show's run, so counting one would make a
        finished show look unfinished forever.
        """
        server = _FakeTautulli(
            episodes=[
                _episode_row(
                    "The Bear", season=0, episode=1, grandparent_rating_key=55
                ),
                _episode_row(
                    "The Bear", season=1, episode=1, grandparent_rating_key=55
                ),
            ],
            children={
                55: [
                    {"media_index": 0, "children_count": 3},
                    {"media_index": 1, "children_count": 8},
                ]
            },
        )

        items = _fetch(server, plugin)

        metadata = items[0].metadata
        assert metadata["episodes_watched_by_season"] == {1: 1}
        assert metadata["seasons_watched_dates"] == {"1": "2024-02-20T12:00:00+00:00"}
        assert metadata["plex_season_episode_counts"] == {1: 8}


class TestWhatIsIgnored:
    def test_a_partially_watched_row_is_not_imported(
        self, plugin: TautulliPlugin
    ) -> None:
        """Tautulli already applied the operator's own watched threshold."""
        server = _FakeTautulli(
            movies=[_movie_row("Arrival", watched_status=0.5)],
            episodes=[_episode_row("The Bear", watched_status=0)],
        )

        assert _fetch(server, plugin) == []

    def test_another_plex_users_history_is_not_imported(
        self, plugin: TautulliPlugin
    ) -> None:
        """The server-side ``user`` filter is one query parameter away from
        being dropped, and the import it would produce is someone else's taste.
        """
        server = _FakeTautulli(
            movies=[_movie_row("Arrival", user="hayden")],
            episodes=[_episode_row("The Bear", user="hayden")],
        )

        assert _fetch(server, plugin) == []
        assert server.history_calls("movie")[0]["user"] == _USER

    def test_the_operators_own_history_survives_a_difference_of_case(
        self, plugin: TautulliPlugin
    ) -> None:
        """Plex keeps the case the account was made with and the operator types
        the config by hand: an exact match imports nothing and says nothing.
        """
        server = _FakeTautulli(movies=[_movie_row("Arrival", user="Aaron")])

        assert [item.title for item in _fetch(server, plugin)] == ["Arrival"]


class TestPagination:
    def test_every_page_of_history_is_consumed(self, plugin: TautulliPlugin) -> None:
        """Stopping after the first page silently imported the oldest 1000 plays
        and nothing since.
        """
        server = _FakeTautulli(
            episodes=[
                _episode_row("The Bear", episode=number) for number in range(1500)
            ]
        )

        items = _fetch(server, plugin)

        assert items[0].metadata["episodes_watched_by_season"] == {1: 1500}
        assert [query["start"] for query in server.history_calls("episode")] == [
            0,
            1000,
        ]

    def test_a_page_this_plugin_ignores_entirely_still_advances_the_cursor(
        self, plugin: TautulliPlugin
    ) -> None:
        """A cursor advanced by rows kept rather than rows returned re-requests
        a page of part-watched plays forever, and the sync never ends.
        """
        server = _FakeTautulli(
            episodes=[
                _episode_row("The Bear", episode=number, watched_status=0)
                for number in range(1000)
            ]
            + [_episode_row("The Bear", episode=1000 + number) for number in range(5)]
        )

        items = _fetch(server, plugin)

        assert items[0].metadata["episodes_watched_by_season"] == {1: 5}


class TestErrorsKeepTheApiKeyOut:
    def test_a_refused_command_raises_without_quoting_the_key(
        self, plugin: TautulliPlugin
    ) -> None:
        response = _response(
            {"response": {"result": "error", "message": "Invalid apikey"}}
        )

        with patch(
            "src.ingestion.sources.tautulli.tautulli.requests.get",
            return_value=response,
        ):
            with pytest.raises(SourceError, match="Invalid apikey") as raised:
                list(plugin.fetch(dict(_CONFIG)))

        assert _API_KEY not in str(raised.value)

    def test_a_failed_request_does_not_carry_its_own_url(
        self, plugin: TautulliPlugin
    ) -> None:
        """``requests`` puts the full URL in the exception text, and this key
        travels in the query string.
        """
        with patch(
            "src.ingestion.sources.tautulli.tautulli.requests.get",
            side_effect=requests.ConnectionError(
                f"failed: http://localhost:8181/api/v2?apikey={_API_KEY}"
            ),
        ):
            with pytest.raises(SourceError, match="ConnectionError") as raised:
                list(plugin.fetch(dict(_CONFIG)))

        assert _API_KEY not in str(raised.value)

    def test_a_cross_origin_redirect_is_refused_rather_than_followed(
        self, plugin: TautulliPlugin
    ) -> None:
        """Following it would replay the key, query string and all, at whatever
        host ``Location`` named.
        """
        redirect = Mock(spec=requests.Response)
        redirect.status_code = 301
        redirect.headers = {"Location": "https://elsewhere.example/api/v2"}

        with patch(
            "src.ingestion.sources.tautulli.tautulli.requests.get",
            return_value=redirect,
        ) as mock_get:
            with pytest.raises(SourceError, match="Refused a redirect"):
                list(plugin.fetch(dict(_CONFIG)))

        mock_get.assert_called_once()


class TestValidateConfig:
    def test_every_missing_field_is_reported_at_once(
        self, plugin: TautulliPlugin
    ) -> None:
        """One error per field: the caller is shown the field name and nothing
        else, so a single "invalid config" leaves it guessing.
        """
        errors = plugin.validate_config({})

        assert [error.split(" ")[0] for error in errors] == [
            "'api_key'",
            "'username'",
            "'url'",
        ]

    def test_a_url_that_names_no_host_is_refused(self, plugin: TautulliPlugin) -> None:
        """The scheduler syncs without validating, so ``fetch`` refuses it too."""
        config = {**_CONFIG, "url": "file:///etc/passwd"}

        assert plugin.validate_config(config) == [
            "'url' must start with http:// or https://"
        ]
        with pytest.raises(SourceError, match="http:// or https://"):
            list(plugin.fetch(config))
