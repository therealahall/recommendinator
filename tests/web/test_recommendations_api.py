from unittest.mock import MagicMock

from src.models.content import ContentType
from src.recommendations.engine import RecommendationEngine
from src.recommendations.record import Recommendation
from src.web.state import app_state
from tests.factories import (
    authenticated_client,
    booted_web_app,
    make_item,
    make_storage_mock,
)


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
        # Empty, never null: the SPA iterates both lists without a guard.
        assert payload["contributing_items"] == []
