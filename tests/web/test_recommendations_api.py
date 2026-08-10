"""Regression tests for recommendations empty-state messaging."""

import logging
from unittest.mock import MagicMock

import pytest

from src.recommendations.engine import RecommendationEngine
from src.storage.manager import StorageManager
from src.web.state import app_state
from tests.factories import authenticated_client, booted_web_app


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

        with booted_web_app(mock_storage, {}) as app:
            app_state.engine = mock_engine
            response = authenticated_client(app).get(
                "/api/recommendations?type=video_game&count=5"
            )

        assert response.status_code == 200
        assert response.json() == []


class TestAStreamFailureCannotForgeALogLineRegression:
    """Reported: the stream's outer handler logs with ``exc_info=True``.

    The engine walks the library, so its errors quote item titles, and the
    two sibling sinks for that same error escape it. Fix: escape it here.
    """

    _FORGED = "Real Title\nERROR    | forged | line"

    def test_the_engines_error_is_escaped_before_the_stream_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        mock_engine = MagicMock(spec=RecommendationEngine)
        mock_engine.generate_recommendations.side_effect = ValueError(
            f"no candidate for {self._FORGED}"
        )
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_user_preference_config.return_value = None

        with (
            booted_web_app(mock_storage, {}) as app,
            caplog.at_level(logging.ERROR, logger="src.web.api"),
        ):
            app_state.engine = mock_engine
            response = authenticated_client(app).get(
                "/api/recommendations/stream?type=video_game&count=5"
            )
            assert "Failed to generate recommendations" in response.text

        assert self._FORGED not in caplog.text
        assert "Real Title\\nERROR" in caplog.text
