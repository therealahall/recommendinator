"""Invariants that hold only while two surfaces of one capability agree.

Every test here spans a boundary that no single surface's suite can see
across: the CLI against the web API, or the Python bound the API enforces
against the TypeScript one the UI submits under. A test living on one side of
such a boundary keeps passing while the other side drifts away from it.
"""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from src.models.content import (
    MAX_REVIEW_LENGTH,
    ConsumptionStatus,
    ContentItem,
    ContentType,
)
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.engine import RecommendationEngine
from src.recommendations.record import Recommendation
from src.recommendations.scorers import SCORER_NAME_MAP
from src.storage.manager import StorageManager
from src.utils.sorting import MAX_SEARCH_LENGTH
from src.web.api import (
    CompletionRequest,
    ItemEditRequest,
    UserPreferenceResponse,
)
from src.web.app import app as web_app
from tests.cli.conftest import _invoke_with_mocks

# parents[1] resolves /tests/test_interface_parity.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_SEARCH_BOUND = "src/utils/sorting.py"
FRONTEND_CONSTANTS = "resources/js/constants/library.ts"
FRONTEND_PREFERENCES = "resources/js/stores/preferences.ts"

# `export const MAX_SEARCH_LENGTH = 200` — the only form that file uses.
_TS_SEARCH_LENGTH = re.compile(
    r"^export const MAX_SEARCH_LENGTH = (?P<value>\d+)\s*$", re.MULTILINE
)

# The quoted entries of `export const SCORER_KEYS = [ ... ] as const`.
_TS_SCORER_KEYS = re.compile(
    r"^export const SCORER_KEYS = \[(?P<entries>[^\]]*)\] as const\s*$",
    re.MULTILINE | re.DOTALL,
)

# The two spellings of an empty review. Every review-writing surface is
# checked against this one list, because refusing different sets is itself the
# drift. Chat imports it from here, and the storage door imports it as the base
# of a superset that adds the spelling only a direct caller can reach it with.
BLANK_REVIEWS = ["", "   "]

# The two request models carrying a review, each with the other fields it needs
# to be constructible at all.
_REVIEW_MODELS = [
    pytest.param(ItemEditRequest, {"status": "completed"}, id="edit"),
    pytest.param(
        CompletionRequest, {"content_type": "book", "title": "Dune"}, id="complete"
    ),
]


def _reviewed_book(tmp_path: Path, name: str) -> tuple[StorageManager, int]:
    """A real temp-DB storage holding one completed, reviewed book."""
    storage = StorageManager(sqlite_path=tmp_path / name)
    db_id = storage.save_content_item(
        ContentItem(
            id="book-1",
            title="Dune",
            author="Frank Herbert",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            review="Loved it",
        )
    )
    return storage, db_id


def _blank_review_message(model: type[BaseModel], **fields: object) -> str:
    """The single validation message a blank ``review`` produces."""
    with pytest.raises(ValidationError) as caught:
        model(review="   ", **fields)

    errors = caught.value.errors()
    assert [error["loc"] for error in errors] == [("review",)]
    return str(errors[0]["msg"])


class TestSearchLengthBoundMatchesTheFrontend:
    """The UI must refuse exactly the search terms the API refuses.

    The API answers an over-long term with a 422, so a UI bound above the
    Python one puts that 422 in front of a user for a term the input let them
    type, and a UI bound below it silently shortens what they can search for.
    The two constants are written in different languages, so nothing else
    notices when one of them moves.
    """

    def test_typescript_constant_equals_the_python_one(self) -> None:
        """The exported TypeScript literal is the Python bound, exactly."""
        source = (_REPO_ROOT / FRONTEND_CONSTANTS).read_text()
        match = _TS_SEARCH_LENGTH.search(source)

        assert match is not None, (
            f"{FRONTEND_CONSTANTS} no longer exports MAX_SEARCH_LENGTH as a"
            f" plain integer literal, so it can no longer be checked against"
            f" MAX_SEARCH_LENGTH in {PYTHON_SEARCH_BOUND}."
        )
        assert int(match.group("value")) == MAX_SEARCH_LENGTH, (
            f"MAX_SEARCH_LENGTH is {match.group('value')} in"
            f" {FRONTEND_CONSTANTS} and {MAX_SEARCH_LENGTH} in"
            f" {PYTHON_SEARCH_BOUND}; the UI and the API disagree about which"
            f" search terms are accepted."
        )


class TestReviewLengthBoundIsTheSameOnBothApiSurfaces:
    """The two endpoints that take a review accept exactly the same lengths.

    ``PATCH /api/items/{id}`` and ``POST /api/complete`` bound the same free
    text on its way to the same column, and ``library edit`` bounds it against
    the same number in Python. (CLI ``complete`` and chat bound no length at
    all — they check only that a review is not blank.) Spelled as separate
    literals, one can move without the others: a review is then accepted on
    one surface and answered with a 422 on another, and text one interface
    stored is text the other refuses to edit. No suite on a single surface can
    see that, and the completion endpoint's half had no test of its own at all.
    """

    @pytest.mark.parametrize("model, fields", _REVIEW_MODELS)
    def test_a_review_of_exactly_the_bound_is_accepted(
        self, model: type[BaseModel], fields: dict[str, object]
    ) -> None:
        """The longest allowed review is allowed — the bound is not off by one."""
        longest = "x" * MAX_REVIEW_LENGTH

        accepted = model(review=longest, **fields)

        assert accepted.model_dump()["review"] == longest

    @pytest.mark.parametrize("model, fields", _REVIEW_MODELS)
    def test_one_character_over_the_bound_is_refused(
        self, model: type[BaseModel], fields: dict[str, object]
    ) -> None:
        """One character more is a 422, on both surfaces alike."""
        with pytest.raises(ValidationError) as caught:
            model(review="x" * (MAX_REVIEW_LENGTH + 1), **fields)

        errors = caught.value.errors()
        assert [error["loc"] for error in errors] == [("review",)]
        assert errors[0]["type"] == "string_too_long"


class TestBlankReviewRefusedByBothCompletionSurfaces:
    """`complete` and POST /api/complete refuse a blank review alike.

    Bug: both surfaces accepted ``--review ""`` and ``{"review": ""}`` and
    sent them to ``complete_content_item``, which overwrites — so an empty
    string replaced a review the user had written. ``tests/test_sqlite_db.py``
    covers the loss at the door; this covers the two doorbells.
    Root cause: the blank guard existed on the edit surfaces only
    (``library edit --review``, ``ItemEditRequest``), and the completion
    surfaces had never needed one while they still wrote through the
    fill-only sync door.
    Fix: the completion request model shares the edit model's review type, the
    ``complete`` command shares the blank check with ``library edit``, and the
    door itself treats blank as "supplied none". A surface that accepted the
    input and let the door drop it would report a review recorded while
    quietly keeping the old one, so refusing up front is the visible half —
    and both surfaces must refuse the same input.
    """

    @pytest.mark.parametrize("blank_review", BLANK_REVIEWS)
    def test_web_completion_request_rejects_a_blank_review_regression(
        self, blank_review: str
    ) -> None:
        """The request model refuses it, which is the endpoint's 422."""
        with pytest.raises(ValidationError) as caught:
            CompletionRequest(content_type="book", title="Dune", review=blank_review)

        assert [error["loc"] for error in caught.value.errors()] == [("review",)]

    @pytest.mark.parametrize("blank_review", BLANK_REVIEWS)
    def test_complete_command_refuses_a_blank_review_regression(
        self, tmp_path: Path, blank_review: str
    ) -> None:
        """The CLI aborts and the stored review is still there afterwards."""
        storage, db_id = _reviewed_book(tmp_path, "complete.db")

        result = _invoke_with_mocks(
            CliRunner(),
            [
                "complete",
                "--type",
                "book",
                "--title",
                "Dune",
                "--review",
                blank_review,
            ],
            storage,
        )

        assert result.exit_code != 0
        assert "--review cannot be empty" in result.output
        stored = storage.get_content_item(db_id)
        assert stored is not None
        assert stored.review == "Loved it"


class TestBlankReviewRemedyNamesTheSurfacesOwn:
    """Each surface names the remedy it has, and a completion has none.

    Bug: ``CompletionRequest`` and ``ItemEditRequest`` shared one review type,
    so ``POST /api/complete`` answered a blank review with the edit endpoint's
    remedy — "send null to clear it". On ``PATCH /api/items/{id}`` that is
    true. On ``POST /api/complete`` a null is indistinguishable from omitting
    the field: the door skips a blank review either way and leaves the stored
    review in place, so a caller who followed the instruction got a 200 and
    believed they had cleared something they had not.
    Root cause: the shared type carried the remedy as well as the constraint,
    and only the constraint is common to the two models. The suite could not
    see it because the web assertions checked the error's location while only
    the CLI's message text was pinned.
    Fix: the constraint stays shared and the message belongs to the model, the
    way the CLI already splits it — ``library edit`` names ``--clear-review``
    and ``complete`` names nothing, because a fresh completion has nothing to
    clear.
    """

    def test_both_edit_surfaces_name_the_clear(self, tmp_path: Path) -> None:
        """The edit endpoint and ``library edit`` both point at clearing."""
        message = _blank_review_message(ItemEditRequest, status="completed")
        storage, db_id = _reviewed_book(tmp_path, "edit.db")

        result = _invoke_with_mocks(
            CliRunner(),
            ["library", "edit", "--id", str(db_id), "--review", "   "],
            storage,
        )

        assert message == "Value error, review cannot be blank; send null to clear it"
        assert result.exit_code != 0
        assert "--review cannot be empty. Use --clear-review to remove one." in (
            result.output
        )
        stored = storage.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert stored.review == "Loved it"

    def test_neither_completion_surface_names_a_clear_regression(
        self, tmp_path: Path
    ) -> None:
        """Neither offers a remedy, because a completion has nothing to clear."""
        message = _blank_review_message(
            CompletionRequest, content_type="book", title="Dune"
        )
        storage, db_id = _reviewed_book(tmp_path, "complete-remedy.db")

        result = _invoke_with_mocks(
            CliRunner(),
            ["complete", "--type", "book", "--title", "Dune", "--review", "   "],
            storage,
        )

        assert message == "Value error, review cannot be blank"
        assert result.exit_code != 0
        assert "--review cannot be empty." in result.output
        assert "clear" not in result.output
        stored = storage.get_content_item(db_id, user_id=1)
        assert stored is not None
        assert stored.review == "Loved it"


class TestPreferenceJsonKeysAgree:
    """``preferences get --format json`` and the web GET return one key set.

    The CLI serialises ``UserPreferenceConfig.to_dict()`` straight out, while
    the web feeds the same dict into ``UserPreferenceResponse``, whose Pydantic
    default silently drops anything the model does not declare. A field on the
    dataclass but not the response model is therefore a divergence neither
    side's suite can see.
    """

    def test_the_cli_dict_and_the_response_model_declare_the_same_fields(
        self,
    ) -> None:
        """Neither surface carries a preference field the other omits."""
        assert set(UserPreferenceConfig().to_dict()) == set(
            UserPreferenceResponse.model_fields
        )


class TestScorerKeysMatchTheFrontendList:
    """The Preferences page must offer a slider for every scorer, and no more.

    ``SCORER_NAME_MAP`` is what ``preferences set-weight`` accepts and what the
    engine resolves overrides against. A scorer missing from the TypeScript
    list has a weight the CLI can set and the web cannot, and a key only in the
    TypeScript list renders a slider that resolves against nothing.
    """

    def test_typescript_scorer_keys_equal_the_python_ones(self) -> None:
        """The exported TypeScript list is the scorer registry, exactly."""
        source = (_REPO_ROOT / FRONTEND_PREFERENCES).read_text()
        match = _TS_SCORER_KEYS.search(source)

        assert match is not None, (
            f"{FRONTEND_PREFERENCES} no longer exports SCORER_KEYS as a plain"
            f" array of string literals, so it can no longer be checked against"
            f" SCORER_NAME_MAP."
        )
        assert set(re.findall(r"'([^']+)'", match.group("entries"))) == set(
            SCORER_NAME_MAP
        ), (
            f"{FRONTEND_PREFERENCES} and SCORER_NAME_MAP disagree about which"
            f" scorers exist, so the Preferences page and the CLI offer"
            f" different weights."
        )


class TestRecommendationJsonIsByteIdenticalOnBothSurfaces:
    """``recommend --format json`` and ``GET /api/recommendations`` agree.

    Both serialise one :class:`Recommendation` through ``to_payload``, so the
    field set, the values and the key order are one decision made in one place.
    The expected text below is what both surfaces emitted while each built its
    own dict by hand, which is what makes this a pin rather than a snapshot:
    a change to the payload shows up here as changed bytes on both sides.
    """

    # One recommendation carrying a distinguishable value in every field the
    # payload names, so a dropped, renamed or reordered field is visible.
    RECOMMENDATION = Recommendation(
        item=ContentItem(
            id="ol-1",
            db_id=42,
            title="Hyperion",
            author="Dan Simmons",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        ),
        score=0.875,
        reasoning="Recommended because you liked the book Dune",
        score_breakdown={"genre_match": 0.9, "creator_match": 0.5},
        variety_penalty=0.25,
        # Reference lists stay off the wire: neither surface has ever sent them.
        contributing_items=[
            ContentItem(
                id="ol-2",
                db_id=7,
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
            )
        ],
        llm_reasoning="Big ideas, bigger prose.",
    )

    CLI_JSON = """[
  {
    "db_id": 42,
    "title": "Hyperion",
    "author": "Dan Simmons",
    "score": 0.875,
    "reasoning": "Recommended because you liked the book Dune",
    "llm_reasoning": "Big ideas, bigger prose.",
    "score_breakdown": {
      "genre_match": 0.9,
      "creator_match": 0.5
    },
    "variety_penalty": 0.25
  }
]"""

    WEB_JSON = (
        '[{"db_id":42,"title":"Hyperion","author":"Dan Simmons","score":0.875,'
        '"reasoning":"Recommended because you liked the book Dune",'
        '"llm_reasoning":"Big ideas, bigger prose.",'
        '"score_breakdown":{"genre_match":0.9,"creator_match":0.5},'
        '"variety_penalty":0.25}]'
    )

    def _engine(self) -> MagicMock:
        """An engine that recommends exactly the record above."""
        engine = MagicMock(spec=RecommendationEngine)
        engine.generate_recommendations.return_value = [self.RECOMMENDATION]
        return engine

    def test_the_cli_emits_the_pinned_json(self) -> None:
        """``recommend --type book --format json`` prints it, byte for byte."""
        storage = MagicMock(spec=StorageManager)
        storage.get_user_preference_config.return_value = None

        result = _invoke_with_mocks(
            CliRunner(),
            ["recommend", "--type", "book", "--format", "json"],
            storage,
            engine=self._engine(),
        )

        assert result.exit_code == 0
        # The command prints a progress line before the document.
        assert result.output.endswith(self.CLI_JSON + "\n")

    def test_the_web_endpoint_emits_the_pinned_json(self) -> None:
        """``GET /api/recommendations`` returns the same document, compacted."""
        storage = MagicMock(spec=StorageManager)
        storage.get_user_preference_config.return_value = None

        with (
            patch("src.web.api.get_engine", return_value=self._engine()),
            patch("src.web.api.get_storage", return_value=storage),
            patch("src.web.api.get_config", return_value={}),
        ):
            response = TestClient(web_app).get("/api/recommendations?type=book&count=1")

        assert response.status_code == 200
        assert response.text == self.WEB_JSON

    def test_the_two_documents_carry_the_same_data(self) -> None:
        """Neither surface adds, drops or renames a field the other does not.

        The pins above are per-surface; this is the parity claim itself, and it
        survives a reformat of either document.
        """
        assert json.loads(self.CLI_JSON) == json.loads(self.WEB_JSON)
