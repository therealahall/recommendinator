from unittest.mock import MagicMock

from src.recommendations.engine import RecommendationEngine
from src.web.state import app_state
from tests.factories import authenticated_client, booted_web_app, make_storage_mock


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
