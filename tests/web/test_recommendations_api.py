"""Regression tests for recommendations empty-state messaging."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from src.web.app import app

_APP_JS = Path(__file__).parents[2] / "src" / "web" / "static" / "app.js"


class TestEmptyRecommendationsRegression:
    """Regression tests for the recommendations endpoint empty-results path."""

    def test_empty_recommendations_returns_empty_list_regression(self) -> None:
        """GET /api/recommendations returns HTTP 200 + [] when pipeline empty.

        Bug: the frontend rendered a misleading empty-state message when the
        API returned no results. This test pins the API contract (200 + [])
        that triggers the frontend empty-state rendering path.
        """
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.return_value = []
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_user_preference_config.return_value = None

        with patch("src.web.api.get_engine", return_value=mock_engine):
            with patch("src.web.api.get_storage", return_value=mock_storage):
                with patch("src.web.api.get_config", return_value={}):
                    client = TestClient(app)
                    response = client.get(
                        "/api/recommendations?type=video_game&count=5"
                    )

        assert response.status_code == 200
        assert response.json() == []


class TestEmptyStateMessageRegression:
    """Regression tests for the empty-state message in the web UI."""

    def test_empty_state_message_explains_unconsumed_items_regression(self) -> None:
        """app.js empty-state message explains unconsumed-item semantics.

        Bug: the old message said 'Try adding more content to your library'
        which was misleading — recommendations are based on items the user
        has NOT consumed yet. If everything is marked completed, there is
        nothing to recommend. The fix changed the message to explain this.
        """
        # Read raw JS source — \u2019 is a JS Unicode escape stored as
        # literal ASCII in the file, not decoded by Python's read_text().
        app_js = _APP_JS.read_text()
        assert "haven\\u2019t consumed yet" in app_js
        assert "Try adding more content to your library" not in app_js
