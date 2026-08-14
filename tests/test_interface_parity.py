"""Invariants that hold only while two surfaces of one capability agree.

Every test here spans a boundary that no single surface's suite can see
across: the CLI against the web API, or the Python bound the API enforces
against the TypeScript one the UI submits under. A test living on one side of
such a boundary keeps passing while the other side drifts away from it.
"""

import json
import re
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner
from pydantic import BaseModel, ValidationError

from src.cli.commands._preferences import preferences_set_length
from src.models.content import (
    MAX_DESCRIPTION_LENGTH,
    MAX_GENRE_TAG_LENGTH,
    MAX_GENRES,
    MAX_REVIEW_LENGTH,
    MAX_TAGS,
    ConsumptionStatus,
    ContentItem,
    ContentType,
)
from src.models.user_preferences import UserPreferenceConfig
from src.recommendations.content_length import LengthPreference
from src.recommendations.engine import RecommendationEngine
from src.recommendations.record import Recommendation, RecommendationPayload
from src.recommendations.scorers import SCORER_NAME_MAP
from src.storage.accounts import (
    MAX_ACCOUNT_NAME_LENGTH,
    MIN_PASSWORD_LENGTH,
    AccountRecord,
)
from src.storage.manager import StorageManager
from src.utils.sorting import MAX_SEARCH_LENGTH
from src.web.api import (
    CompletionRequest,
    ItemEditRequest,
    RecommendationResponse,
    UserPreferenceResponse,
    UserResponse,
)
from src.web.auth_api import SessionResponse
from src.web.state import app_state
from tests.cli.conftest import _invoke_with_mocks
from tests.factories import authenticated_client, booted_web_app

# parents[1] resolves /tests/test_interface_parity.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_SEARCH_BOUND = "src/utils/sorting.py"
PYTHON_PASSWORD_BOUND = "src/storage/accounts.py"
FRONTEND_CONSTANTS = "resources/js/constants/library.ts"
FRONTEND_AUTH_CONSTANTS = "resources/js/constants/auth.ts"
FRONTEND_PREFERENCES = "resources/js/stores/preferences.ts"
FRONTEND_TYPES = "resources/js/types/api.ts"

# `export const MAX_SEARCH_LENGTH = 200` — the only form that file uses.
_TS_SEARCH_LENGTH = re.compile(
    r"^export const MAX_SEARCH_LENGTH = (?P<value>\d+)\s*$", re.MULTILINE
)

# `export const PASSWORD_MIN_LENGTH = 12` — the only form that file uses.
_TS_PASSWORD_LENGTH = re.compile(
    r"^export const PASSWORD_MIN_LENGTH = (?P<value>\d+)\s*$", re.MULTILINE
)

# `export const NAME_MAX_LENGTH = 100` — same form.
_TS_NAME_LENGTH = re.compile(
    r"^export const NAME_MAX_LENGTH = (?P<value>\d+)\s*$", re.MULTILINE
)

# The body of `export interface RecommendationResponse { ... }`. No field of it
# is an object literal, so the first closing brace is the interface's own.
_TS_RECOMMENDATION_FIELDS = re.compile(
    r"^export interface RecommendationResponse \{(?P<body>[^}]*)\}", re.MULTILINE
)

# The quoted entries of `export const SCORER_KEYS = [ ... ] as const`.
_TS_SCORER_KEYS = re.compile(
    r"^export const SCORER_KEYS = \[(?P<entries>[^\]]*)\] as const\s*$",
    re.MULTILINE | re.DOTALL,
)

# The two spellings of an empty review. Every review-writing surface is
# checked against this one list, because refusing different sets is itself the
# drift. The storage door imports it as the base of a superset that adds the
# spelling only a direct caller can reach it with.
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


def _claimed(tmp_path: Path, name: str) -> StorageManager:
    """A real temp-DB storage whose one account is claimed by ``owner``."""
    storage = StorageManager(sqlite_path=tmp_path / name)
    storage.claim_account("owner", "The Owner", "correct horse battery")
    return storage


def _username(storage: StorageManager) -> str:
    record = storage.describe_account(1)
    assert record is not None
    return record["username"]


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


class TestPasswordLengthBoundMatchesTheFrontend:
    """The auth forms must refuse exactly the passwords the API refuses.

    The session call carries the running server's floor, so this pins the
    default the bundle ships with, which nothing at runtime can correct.
    """

    def test_typescript_constant_equals_the_python_one(self) -> None:
        """The exported TypeScript literal is the Python bound, exactly."""
        source = (_REPO_ROOT / FRONTEND_AUTH_CONSTANTS).read_text()
        match = _TS_PASSWORD_LENGTH.search(source)

        assert match is not None, (
            f"{FRONTEND_AUTH_CONSTANTS} no longer exports PASSWORD_MIN_LENGTH"
            f" as a plain integer literal, so it can no longer be checked"
            f" against MIN_PASSWORD_LENGTH in {PYTHON_PASSWORD_BOUND}."
        )
        assert int(match.group("value")) == MIN_PASSWORD_LENGTH, (
            f"The password minimum is {match.group('value')} in"
            f" {FRONTEND_AUTH_CONSTANTS} and {MIN_PASSWORD_LENGTH} in"
            f" {PYTHON_PASSWORD_BOUND}; the forms refuse a password the API"
            f" accepts and `account set-password` still sets."
        )

    def test_the_hint_and_the_refusal_name_the_bound_they_are_given(self) -> None:
        """Both strings the user reads are built from the caller's figure."""
        source = (_REPO_ROOT / FRONTEND_AUTH_CONSTANTS).read_text()

        for name in ("passwordHint", "passwordTooShort"):
            declaration = re.search(
                rf"^export function {name}\(minLength: number\): string \{{"
                rf"(?P<body>.*?)^\}}",
                source,
                re.MULTILINE | re.DOTALL,
            )
            assert declaration is not None, (
                f"{FRONTEND_AUTH_CONSTANTS} no longer builds {name} from a"
                f" minLength argument, so the sentence on screen cannot follow"
                f" the floor the session call reported."
            )
            assert "${minLength}" in declaration.group("body"), (
                f"{name} spells a minimum out instead of interpolating the one"
                f" it was given, so the sentence on screen can drift from the"
                f" bound the form actually enforces."
            )

    def test_the_name_cap_equals_the_python_one(self) -> None:
        """``maxlength`` on the name fields is the API's own cap."""
        source = (_REPO_ROOT / FRONTEND_AUTH_CONSTANTS).read_text()
        match = _TS_NAME_LENGTH.search(source)

        assert match is not None, (
            f"{FRONTEND_AUTH_CONSTANTS} no longer exports NAME_MAX_LENGTH as a"
            f" plain integer literal, so it can no longer be checked against"
            f" MAX_ACCOUNT_NAME_LENGTH in {PYTHON_PASSWORD_BOUND}."
        )
        assert int(match.group("value")) == MAX_ACCOUNT_NAME_LENGTH, (
            f"The name cap is {match.group('value')} in"
            f" {FRONTEND_AUTH_CONSTANTS} and {MAX_ACCOUNT_NAME_LENGTH} in"
            f" {PYTHON_PASSWORD_BOUND}; the field stops accepting a name the"
            f" API takes, or takes one it refuses."
        )


class TestTheAccountShapeIsTheSameOnBothInterfaces:
    """``account show --format json`` and ``GET /api/auth/session`` agree.

    The CLI prints ``AccountRecord`` whole; the web splits it between
    ``UserResponse`` and ``claimed``. ``password_updated_at`` was on one and
    not the other.
    """

    def test_every_field_of_the_record_reaches_both(self) -> None:
        """``claimed`` is the session report's; the rest are the user's."""
        assert "claimed" in SessionResponse.model_fields
        assert set(AccountRecord.__annotations__) == set(UserResponse.model_fields) | {
            "claimed"
        }


class TestTheNameBoundIsMeasuredAfterTheTrim:
    """Regression: the interfaces measured different strings.

    Bug reported: two spaces plus a full-width name was stored by the CLI and
    422'd by the web.
    Root cause: ``Field(max_length=...)`` runs before any ``AfterValidator``.
    Fix: ``normalize_account_name`` caps after the trim.
    """

    @staticmethod
    def _cli_rename(storage: StorageManager, username: str) -> Any:
        return _invoke_with_mocks(
            CliRunner(), ["account", "set-name", "--username", username], storage
        )

    @staticmethod
    def _web_rename(storage: StorageManager, username: str) -> Any:
        with booted_web_app(storage, {}) as app:
            return authenticated_client(app).patch(
                "/api/users/1", json={"username": username, "display_name": ""}
            )

    def test_both_interfaces_take_the_padded_name_at_the_cap(
        self, tmp_path: Path
    ) -> None:
        """The trimmed name fits the column, so neither may refuse it."""
        padded = "  " + "x" * MAX_ACCOUNT_NAME_LENGTH
        cli, web = _claimed(tmp_path, "cli.db"), _claimed(tmp_path, "web.db")

        assert self._cli_rename(cli, padded).exit_code == 0
        assert self._web_rename(web, padded).status_code == 200
        assert _username(cli) == _username(web) == padded.strip()

    def test_both_interfaces_refuse_the_padded_name_past_it(
        self, tmp_path: Path
    ) -> None:
        """Anchors the acceptance above: the padding is not what is measured."""
        too_long = "  " + "x" * (MAX_ACCOUNT_NAME_LENGTH + 1)
        cli, web = _claimed(tmp_path, "cli.db"), _claimed(tmp_path, "web.db")

        assert self._cli_rename(cli, too_long).exit_code != 0
        assert self._web_rename(web, too_long).status_code == 422
        assert _username(cli) == _username(web) == "owner"


class TestReviewLengthBoundIsTheSameOnBothApiSurfaces:
    """The two endpoints that take a review accept exactly the same lengths.

    ``PATCH /api/items/{id}`` and ``POST /api/complete`` bound the same free
    text on its way to the same column, and ``library edit`` bounds it against
    the same number in Python. (CLI ``complete`` bounds no length at all — it
    checks only that a review is not blank.) Spelled as separate
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


class TestManualMetadataBoundsAreTheSameOnBothSurfaces:
    """What ``library edit`` stores, the web edit dialog can still save.

    ``EditModal`` resends the description on every save, so an item the CLI
    wrote past the web's bound answers 422 forever, on a save that changed
    only the rating.
    """

    _AT_THE_CLI_BOUND: dict[str, Any] = {
        "status": "completed",
        "genres": ["g" * MAX_GENRE_TAG_LENGTH] * MAX_GENRES,
        "tags": ["t" * MAX_GENRE_TAG_LENGTH] * MAX_TAGS,
        "description": "x" * MAX_DESCRIPTION_LENGTH,
    }

    def test_the_largest_edit_the_cli_accepts_validates_on_the_web(self) -> None:
        accepted = ItemEditRequest(**self._AT_THE_CLI_BOUND)

        assert accepted.model_dump()["description"] == "x" * MAX_DESCRIPTION_LENGTH

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("genres", ["g"] * (MAX_GENRES + 1), id="too-many-genres"),
            pytest.param("tags", ["t"] * (MAX_TAGS + 1), id="too-many-tags"),
            pytest.param(
                "genres", ["g" * (MAX_GENRE_TAG_LENGTH + 1)], id="over-long-genre"
            ),
            pytest.param(
                "tags", ["t" * (MAX_GENRE_TAG_LENGTH + 1)], id="over-long-tag"
            ),
            pytest.param(
                "description",
                "x" * (MAX_DESCRIPTION_LENGTH + 1),
                id="over-long-description",
            ),
        ],
    )
    def test_one_past_each_bound_is_refused_as_the_cli_refuses_it(
        self, field: str, value: object
    ) -> None:
        """Anchors the acceptance above: the bounds are where the CLI's are."""
        with pytest.raises(ValidationError):
            ItemEditRequest(**{**self._AT_THE_CLI_BOUND, field: value})


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


class TestSetLengthChoicesAreTheEnums:
    """``preferences set-length`` spells its two choice lists as literals while
    the request model derives both from the enums. They agree today, so this
    guards drift: a member added to ``ContentType`` reaches only the web.
    """

    @pytest.mark.parametrize(
        ("argument", "members"),
        [
            pytest.param("content_type", ContentType, id="content-type"),
            pytest.param("length_preference", LengthPreference, id="length"),
        ],
    )
    def test_the_click_choices_are_exactly_the_enum_members(
        self, argument: str, members: type[Enum]
    ) -> None:
        param = next(p for p in preferences_set_length.params if p.name == argument)

        assert isinstance(param.type, click.Choice)
        assert set(param.type.choices) == {member.value for member in members}


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


class TestRecommendationPayloadKeysAgree:
    """One recommendation shape, declared three times.

    ``RecommendationPayload`` is what both interfaces serialise,
    ``RecommendationResponse`` is what the web endpoint validates it into, and
    the TypeScript ``RecommendationResponse`` is what the UI reads off the
    wire. Pydantic drops a key its model does not declare and TypeScript never
    sees the Python at all, so a field added on one side and forgotten on
    another is a divergence no single surface's suite can see.
    """

    def test_the_payload_and_the_response_model_declare_the_same_fields(
        self,
    ) -> None:
        """Nothing the payload sends is dropped, and nothing else is declared."""
        assert set(RecommendationPayload.__annotations__) == set(
            RecommendationResponse.model_fields
        )

    def test_the_typescript_interface_declares_the_same_fields(self) -> None:
        """The UI's interface is the payload's field set, exactly."""
        source = (_REPO_ROOT / FRONTEND_TYPES).read_text()
        match = _TS_RECOMMENDATION_FIELDS.search(source)

        assert match is not None, (
            f"{FRONTEND_TYPES} no longer declares RecommendationResponse as a"
            f" plain interface body, so it can no longer be checked against"
            f" RecommendationPayload."
        )
        assert set(re.findall(r"^\s*(\w+):", match.group("body"), re.MULTILINE)) == set(
            RecommendationPayload.__annotations__
        ), (
            f"{FRONTEND_TYPES} and RecommendationPayload disagree about which"
            f" fields a recommendation carries, so the UI reads a field the API"
            f" never sends or ignores one it does."
        )


class TestRecommendationJsonIsTheSameOnBothSurfaces:
    """``recommend --format json`` and ``GET /api/recommendations`` agree.

    Both serialise one :class:`Recommendation` through ``to_payload``, so the
    field set, the values and the key order are one decision made in one place.
    The two surfaces are driven over one engine here and their documents
    compared, so the claim is about what they emit rather than about two
    literals somebody kept in step by hand.
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
    )

    CLI_JSON = """[
  {
    "db_id": 42,
    "title": "Hyperion",
    "author": "Dan Simmons",
    "score": 0.875,
    "reasoning": "Recommended because you liked the book Dune",
    "score_breakdown": {
      "genre_match": 0.9,
      "creator_match": 0.5
    },
    "variety_penalty": 0.25
  }
]"""

    def _engine(self) -> MagicMock:
        """An engine that recommends exactly the record above."""
        engine = MagicMock(spec=RecommendationEngine)
        engine.generate_recommendations.return_value = [self.RECOMMENDATION]
        return engine

    @staticmethod
    def _cli_document(engine: MagicMock) -> str:
        """The document ``recommend --type book --format json`` prints."""
        storage = MagicMock(spec=StorageManager)
        storage.get_user_preference_config.return_value = None

        result = _invoke_with_mocks(
            # Split streams, so this is the document a pipe receives: the
            # command's progress line goes to stderr.
            CliRunner(mix_stderr=False),
            ["recommend", "--type", "book", "--format", "json"],
            storage,
            engine=engine,
        )

        assert result.exit_code == 0
        return result.stdout

    @staticmethod
    def _web_document(engine: MagicMock) -> str:
        """The document ``GET /api/recommendations`` returns."""
        storage = MagicMock(spec=StorageManager)
        storage.get_user_preference_config.return_value = None

        with booted_web_app(storage, {}) as app:
            app_state.engine = engine
            response = authenticated_client(app).get(
                "/api/recommendations?type=book&count=1"
            )

        assert response.status_code == 200
        return response.text

    def test_the_cli_emits_the_pinned_json(self) -> None:
        """``recommend --type book --format json`` prints it, byte for byte.

        The one byte-level pin left, because the CLI's document is the one a
        user reads: it fixes the indentation and the key order as well as the
        values.
        """
        assert self._cli_document(self._engine()) == self.CLI_JSON + "\n"

    def test_both_surfaces_produce_the_same_document(self) -> None:
        """Neither surface adds, drops or renames a field the other kept.

        Both documents are produced here rather than quoted, so this fails on
        a change to either one of them. A change inside ``to_payload`` moves
        both identically and stays green here, which is what the byte pin
        above catches.
        """
        engine = self._engine()

        assert json.loads(self._cli_document(engine)) == json.loads(
            self._web_document(engine)
        )
