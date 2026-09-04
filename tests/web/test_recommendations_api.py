import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.models.content import ContentType
from src.recommendations.engine import RecommendationEngine
from src.recommendations.record import Recommendation, RecommendationPayload
from src.web.api._recommendations import RecommendationResponse
from src.web.state import app_state
from tests.factories import (
    authenticated_client,
    booted_web_app,
    make_item,
    make_storage_mock,
)

_MIRROR = Path("resources/js/types/api.ts")


def _mirrored_fields(interface: str) -> set[str]:
    body = re.search(
        rf"export interface {interface} \{{(.*?)^\}}",
        _MIRROR.read_text(encoding="utf-8"),
        re.MULTILINE | re.DOTALL,
    )
    assert body is not None, f"{interface} is declared nowhere in {_MIRROR}"
    return set(re.findall(r"^  (\w+)\??:", body.group(1), re.MULTILINE))


class TestRecommendationFieldParity:
    def test_a_field_reaches_the_cli_the_api_and_the_spa_or_none_of_them(self) -> None:
        fields = set(RecommendationPayload.__annotations__)

        assert fields == set(RecommendationResponse.model_fields)
        assert fields == _mirrored_fields("RecommendationResponse")


class TestEmptyRecommendationsRegression:
    def test_empty_recommendations_returns_empty_list_regression(self) -> None:
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = []
        mock_storage = make_storage_mock()
        mock_storage.get_user_preference_config.return_value = None

        with booted_web_app(mock_storage, {}) as app:
            app_state.engine = mock_engine
            response = authenticated_client(app).get(
                "/api/recommendations?type=video_game&count=5"
            )

        assert response.status_code == 200
        assert response.json() == []


class TestCrossTypeRequest:
    def test_omitting_the_type_ranks_all_four_rather_than_refusing(self) -> None:
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = [
            Recommendation(
                item=make_item(title="Pentiment", content_type=ContentType.VIDEO_GAME),
                score=0.8,
                reasoning="Because",
            )
        ]
        mock_storage = make_storage_mock()
        mock_storage.get_user_preference_config.return_value = None

        with booted_web_app(mock_storage, {}) as app:
            app_state.engine = mock_engine
            response = authenticated_client(app).get("/api/recommendations?count=5")

        assert response.status_code == 200
        assert [rec["content_type"] for rec in response.json()] == ["video_game"]
        call = mock_engine.generate_recommendations.call_args
        assert call.kwargs["content_type"] is None


class TestDefaultCount:
    @pytest.mark.parametrize(
        ("settings", "expected"),
        [
            ({"default_count": 7}, 7),
            ({"default_count": 30, "max_count": 10}, 10),
        ],
    )
    def test_no_count_param_takes_the_setting_capped_by_the_maximum(
        self, settings: dict, expected: int
    ) -> None:
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = []
        mock_storage = make_storage_mock()
        mock_storage.get_user_preference_config.return_value = None

        with booted_web_app(mock_storage, {"recommendations": settings}) as app:
            app_state.engine = mock_engine
            response = authenticated_client(app).get("/api/recommendations")

        assert response.status_code == 200
        assert (
            mock_engine.generate_recommendations.call_args.kwargs["count"] == expected
        )


class TestRecommendationEvidence:
    def test_the_items_behind_a_pick_reach_the_response(self) -> None:
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = [
            Recommendation(
                item=make_item(
                    title="Blade Runner",
                    content_type=ContentType.MOVIE,
                    db_id=5,
                    cover_url="https://1.2.3.4/br.jpg",
                ),
                score=0.8,
                reasoning="Because",
                adaptations=[make_item(title="Do Androids Dream", db_id=6)],
            )
        ]
        mock_storage = make_storage_mock()
        mock_storage.get_user_preference_config.return_value = None

        with booted_web_app(mock_storage, {}) as app:
            app_state.engine = mock_engine
            response = authenticated_client(app).get(
                "/api/recommendations?type=movie&count=1"
            )

        assert response.status_code == 200
        payload = response.json()[0]
        assert payload["content_type"] == "movie"
        assert payload["cover_url"] == "/api/covers/5"
        assert payload["adaptations"] == [
            {
                "db_id": 6,
                "title": "Do Androids Dream",
                "author": None,
                "content_type": "book",
                "cover_url": None,
            }
        ]
        assert payload["contributing_items"] == []
