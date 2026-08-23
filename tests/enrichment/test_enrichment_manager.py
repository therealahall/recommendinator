"""Tests for the enrichment manager."""

import logging
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.enrichment.manager import (
    _MAX_CONSECUTIVE_REJECTIONS,
    MAX_RECORDED_ERRORS,
    EnrichmentManager,
    merge_enrichment,
)
from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
    ProviderError,
)
from src.enrichment.registry import EnrichmentRegistry
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager
from src.storage.schema import _LEGACY_EXTERNAL_ID_SOURCE


def job_backed(storage: MagicMock, tmp_path: Path) -> MagicMock:
    """Put a real job record behind the mock.

    A bare MagicMock answers "already stopped" truthy, cancelling every run.
    """
    storage.enrichment_jobs = StorageManager(
        sqlite_path=tmp_path / "enrichment-job.db"
    ).enrichment_jobs
    return storage


class MockProvider(EnrichmentProvider):
    """Mock provider for testing."""

    def __init__(
        self,
        name: str = "mock",
        content_types: list[ContentType] | None = None,
        should_fail: bool = False,
        should_not_find: bool = False,
    ) -> None:
        self._name = name
        self._content_types = content_types or [ContentType.MOVIE]
        self._should_fail = should_fail
        self._should_not_find = should_not_find
        self.enrich_calls: list[ContentItem] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return f"Mock Provider ({self._name})"

    @property
    def content_types(self) -> list[ContentType]:
        return self._content_types

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def rate_limit_requests_per_second(self) -> float:
        return 100.0  # High limit for fast tests

    def get_config_schema(self) -> list[ConfigField]:
        return []

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        return []

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        self.enrich_calls.append(item)

        if self._should_fail:
            raise ProviderError(self._name, "Simulated failure")

        if self._should_not_find:
            return EnrichmentResult(match_quality="not_found", provider=self._name)

        return EnrichmentResult(
            external_id=f"{self._name}:{item.id}",
            genres=["Action", "Drama"],
            tags=["test-tag"],
            description="A test description.",
            extra_metadata={"source_rating": 8.5},
            match_quality="high",
            provider=self._name,
        )


class RawRequestErrorProvider(EnrichmentProvider):
    """Provider that lets a raw requests.HTTPError escape from enrich().

    Models the failure mode where a provider forgets to wrap a ``requests``
    exception in ``ProviderError``, so it falls through to the manager's
    broad ``except Exception`` catch-all rather than the ``ProviderError``
    branch.
    """

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.enrich_calls: list[ContentItem] = []

    @property
    def name(self) -> str:
        return "raw_request"

    @property
    def display_name(self) -> str:
        return "Raw Request Provider"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.MOVIE]

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def rate_limit_requests_per_second(self) -> float:
        return 100.0

    def get_config_schema(self) -> list[ConfigField]:
        return []

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        return []

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        self.enrich_calls.append(item)
        raise self._error


class WrappedRequestErrorProvider(EnrichmentProvider):
    """Provider that wraps a ``requests`` failure in a ``ProviderError``.

    Raises ``from error``, keeping the original on ``__cause__``. The default
    message is what a third-party provider is free to write: the raw exception
    interpolated in, API key and all.
    """

    def __init__(self, error: Exception, message: str | None = None) -> None:
        self._error = error
        self._message = message

    @property
    def name(self) -> str:
        return "wrapped_request"

    @property
    def display_name(self) -> str:
        return "Wrapped Request Provider"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.MOVIE]

    @property
    def requires_api_key(self) -> bool:
        return False

    @property
    def rate_limit_requests_per_second(self) -> float:
        return 100.0

    def get_config_schema(self) -> list[ConfigField]:
        return []

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        return []

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        try:
            raise self._error
        except Exception as error:
            raise ProviderError(
                self.name, self._message or f"request failed: {error}"
            ) from error


class SuppressedContextRequestErrorProvider(WrappedRequestErrorProvider):
    """The in-tree shape since TMDB and RAWG started raising ``from None``.

    ``__cause__`` is cleared and ``__suppress_context__`` set, to keep the
    API-key URL off a caller's traceback. Only ``__context__`` is left for the
    manager to classify the failure from.
    """

    @property
    def name(self) -> str:
        return "suppressed_request"

    @property
    def display_name(self) -> str:
        return "Suppressed Context Provider"

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        try:
            raise self._error
        except Exception as error:
            raise ProviderError(
                self.name, self._message or f"request failed: {error}"
            ) from None


#: Both wrapping doubles, so a classification test proves it for either raise
#: form. The two are not interchangeable to the manager: one leaves the
#: ``requests`` failure on ``__cause__``, the other only on ``__context__``.
WRAPPING_PROVIDERS = [
    WrappedRequestErrorProvider,
    SuppressedContextRequestErrorProvider,
]


def http_error(status_code: int, message: str = "") -> requests.HTTPError:
    """Build the ``HTTPError`` ``raise_for_status`` would raise for a status."""
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(message or f"{status_code} Error", response=response)


def save_movie(storage_manager: StorageManager, title: str = "The Matrix") -> int:
    """Store an unenriched movie and return its database ID."""
    return storage_manager.save_content_item(
        ContentItem(
            id=title.lower().replace(" ", "-"),
            title=title,
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
    )


def queued_ids(storage_manager: StorageManager) -> set[int]:
    """db_ids a plain (non-retry) enrichment run would pick up."""
    return {
        db_id
        for db_id, _item in storage_manager.enrichment.items_needing(
            content_type=ContentType.MOVIE, include_not_found=False
        )
    }


def enrichment_buckets(storage_manager: StorageManager) -> dict[str, int]:
    """The four reported enrichment states, proven to still partition the library.

    ``get_enrichment_stats`` reports enriched/pending/not_found/failed side by
    side, so an item landing in two of them reads to the operator as two items.
    A caller wants the counts and that invariant together, never one without
    the other, so this returns the first only after asserting the second.
    """
    stats = storage_manager.enrichment.stats()
    buckets = {
        state: stats[state] for state in ("enriched", "pending", "not_found", "failed")
    }
    assert sum(buckets.values()) == stats["total"], (
        "the four reported states must account for each item exactly once, got "
        f"{buckets} against a total of {stats['total']}"
    )
    return buckets


class TestMergeEnrichment:
    """Tests for the merge_enrichment function."""

    def test_merge_fills_and_combines_fields(self) -> None:
        """Test that genres/tags are merged while other fields follow priority rules."""
        existing = {
            "genres": ["Comedy"],
            "director": "Someone",
        }
        result = EnrichmentResult(
            genres=["Action"],
            tags=["funny"],
            description="A comedy.",
            extra_metadata={"director": "Someone Else", "runtime": 90},
            provider="tmdb",
        )

        merged = merge_enrichment(existing, result)

        # Genres merged (enrichment first)
        assert merged["genres"] == ["Action", "Comedy"]
        assert merged["tags"] == ["funny"]  # Added
        assert merged["description"] == "A comedy."  # Added
        assert (
            merged["director"] == "Someone"
        )  # Preserved (extra_metadata doesn't overwrite)
        assert merged["runtime"] == 90  # Added

    @pytest.mark.parametrize("field", ["genres", "tags"])
    @pytest.mark.parametrize(
        ("stored", "expected"),
        [
            ('["Fantasy", "Horror"]', ["Science Fiction", "Fantasy", "Horror"]),
            ("Fantasy", ["Science Fiction", "Fantasy"]),
            ("", ["Science Fiction"]),
            (["Fantasy", "Science Fiction"], ["Science Fiction", "Fantasy"]),
        ],
    )
    def test_a_stored_value_of_any_shape_keeps_its_place_behind_the_enrichment(
        self, field: str, stored: str | list[str], expected: list[str]
    ) -> None:
        """SQLite hands a stored list back as JSON, and scoring weights order."""
        result = EnrichmentResult(provider="tmdb")
        setattr(result, field, ["Science Fiction"])

        assert merge_enrichment({field: stored}, result)[field] == expected

    def test_merging_leaves_the_callers_metadata_untouched(self) -> None:
        existing = {"genres": ["Comedy"]}
        merge_enrichment(existing, EnrichmentResult(genres=["Action"], provider="tmdb"))
        assert existing == {"genres": ["Comedy"]}


class TestEnrichmentManager:
    """Tests for the EnrichmentManager class."""

    @pytest.fixture
    def mock_storage(self, tmp_path: Path) -> MagicMock:
        """Create a mock storage manager."""
        storage = MagicMock(spec=StorageManager)
        storage.enrichment.items_needing.return_value = []
        # Return an int so `_run_enrichment` can compute total_items without
        # silently producing a MagicMock when tests don't override the value.
        storage.enrichment.count_needing.return_value = 0
        return job_backed(storage, tmp_path)

    @pytest.fixture
    def mock_registry(self) -> EnrichmentRegistry:
        """Create a mock registry with test providers."""
        registry = EnrichmentRegistry()
        registry._discovered = True
        return registry

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        """Create test configuration."""
        return {
            "enrichment": {
                "batch_size": 10,
                "providers": {
                    "mock": {"enabled": True},
                },
            }
        }

    def test_start_enrichment_when_running_returns_false(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Test starting enrichment when already running returns False.

        The first job must be provably still in-flight when the second
        ``start_enrichment()`` is called, otherwise a no-op job could finish
        between the two calls and the guard would (wrongly) let the second in.
        We park the first job inside the provider's ``enrich()`` on an Event so
        ``running`` is still True for the second call, assert it is rejected,
        then release the Event so the first job completes for clean teardown.
        """
        started_enrich = threading.Event()
        release_enrich = threading.Event()

        class BlockingProvider(MockProvider):
            def enrich(
                self, item: ContentItem, config: dict[str, Any]
            ) -> EnrichmentResult | None:
                started_enrich.set()
                release_enrich.wait(timeout=5.0)
                return super().enrich(item, config)

        item = ContentItem(
            id="movie1",
            title="Movie 1",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        mock_storage.enrichment.items_needing.side_effect = [[(1, item)], []]
        mock_storage.enrichment.count_needing.return_value = 1
        mock_registry.register(BlockingProvider())

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        try:
            assert manager.start_enrichment() is True
            # Wait until the first job is parked mid-enrich, so it is provably
            # still running when we make the second call.
            assert started_enrich.wait(timeout=5.0)

            result = manager.start_enrichment()

            assert result is False
        finally:
            release_enrich.set()
            manager._wait_for_completion()

    def test_no_providers_for_content_type(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Test handling when no providers match content type."""
        # Book item but only movie provider registered
        items = [
            (
                1,
                ContentItem(
                    id="book1",
                    title="Book 1",
                    content_type=ContentType.BOOK,
                    status=ConsumptionStatus.UNREAD,
                ),
            ),
        ]
        mock_storage.enrichment.items_needing.side_effect = [items, []]

        # Only movie provider
        provider = MockProvider(content_types=[ContentType.MOVIE])
        mock_registry.register(provider)

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        manager.start_enrichment()

        manager._wait_for_completion()

        status = manager.get_status()
        assert status.items_not_found == 1


class TestEnrichmentStatusApiKeyScrubbingRegression:
    """Regression: a provider's api_key reached ``status.errors``.

    ``GET /api/enrichment/status`` and the CLI surface that list. Both failure
    branches fed it text a provider wrote. Fix: one branch, reporting the
    description ``_classify_failure`` derives.
    """

    _API_KEY = "SECRET_MANAGER_KEY_123"

    @pytest.fixture
    def mock_storage(self, tmp_path: Path) -> MagicMock:
        storage = MagicMock(spec=StorageManager)
        storage.enrichment.items_needing.return_value = []
        storage.enrichment.count_needing.return_value = 0
        return job_backed(storage, tmp_path)

    @pytest.fixture
    def mock_registry(self) -> EnrichmentRegistry:
        registry = EnrichmentRegistry()
        registry._discovered = True
        return registry

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        return {
            "enrichment": {
                "batch_size": 10,
                "providers": {
                    "raw_request": {"enabled": True},
                    "wrapped_request": {"enabled": True},
                },
            }
        }

    def _http_error(self, status_code: int = 401) -> requests.HTTPError:
        """Build an HTTPError whose str() embeds the api_key, like requests."""
        response = MagicMock(spec=requests.Response)
        response.status_code = status_code
        url = (
            "https://api.themoviedb.org/3/search/movie"
            f"?api_key={self._API_KEY}&query=The+Matrix"
        )
        return requests.HTTPError(
            f"{status_code} Error for url: {url}", response=response
        )

    def test_a_failure_on_every_item_does_not_grow_the_record_without_bound(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Re-serialising the whole list per item cost quadratic writes."""
        items = [
            (
                index,
                ContentItem(
                    id=f"movie{index}",
                    title=f"Movie {index}",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                ),
            )
            for index in range(1, MAX_RECORDED_ERRORS + 21)
        ]
        mock_storage.enrichment.items_needing.side_effect = [items, []]
        mock_storage.enrichment.count_needing.return_value = len(items)
        mock_registry.register(RawRequestErrorProvider(self._http_error(503)))

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        manager.start_enrichment()
        manager._wait_for_completion()

        errors = manager.get_status().errors
        assert len(errors) == MAX_RECORDED_ERRORS + 1
        assert errors[-1] == "… and 20 more"

    def test_raw_request_error_does_not_leak_api_key_in_status(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A raw, unwrapped requests error must be scrubbed in status and logs."""
        item = ContentItem(
            id="movie1",
            title="The Matrix",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        mock_storage.enrichment.items_needing.side_effect = [[(1, item)], []]
        mock_storage.enrichment.count_needing.return_value = 1

        mock_registry.register(RawRequestErrorProvider(self._http_error()))

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        with caplog.at_level(logging.WARNING, logger="src.enrichment.manager"):
            manager.start_enrichment()
            manager._wait_for_completion()

        status = manager.get_status()
        assert status.errors, "expected the unwrapped request error to be recorded"
        joined = " ".join(status.errors)
        assert self._API_KEY not in joined
        assert "api_key=" not in joined
        assert "raw_request: HTTP 401" in joined

        # The same scrubbed value must reach the logs (CWE-532): the catch-all
        # logger call must not lazily stringify the raw error's leaky URL.
        assert "raw_request" in caplog.text, "expected the error to be logged"
        assert self._API_KEY not in caplog.text
        assert "api_key=" not in caplog.text

    def test_a_wrapped_provider_message_does_not_leak_api_key_in_status(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The key a provider wrote into its own message reaches neither sink."""
        item = ContentItem(
            id="movie1",
            title="The Matrix",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        mock_storage.enrichment.items_needing.side_effect = [[(1, item)], []]
        mock_storage.enrichment.count_needing.return_value = 1

        mock_registry.register(WrappedRequestErrorProvider(self._http_error()))

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        with caplog.at_level(logging.WARNING, logger="src.enrichment.manager"):
            manager.start_enrichment()
            manager._wait_for_completion()

        status = manager.get_status()
        assert status.errors, "expected the wrapped provider error to be recorded"
        joined = " ".join(status.errors)
        assert self._API_KEY not in joined
        assert "api_key=" not in joined
        assert "wrapped_request: HTTP 401" in joined

        assert "wrapped_request" in caplog.text, "expected the error to be logged"
        assert self._API_KEY not in caplog.text
        assert "api_key=" not in caplog.text


class TestAFailureCannotCarryTheItemsTitleRegression:
    """Reported: scrubbing covered ``requests`` errors and nothing else.

    A provider raising a plain exception put ``str(error)`` — the item's own
    title, newlines and all — into the log and into ``status.errors``.
    """

    _FORGED = "Real Title\nWARNING  | forged | line"

    @pytest.fixture
    def mock_storage(self, tmp_path: Path) -> MagicMock:
        storage = MagicMock(spec=StorageManager)
        storage.enrichment.items_needing.return_value = []
        storage.enrichment.count_needing.return_value = 0
        return job_backed(storage, tmp_path)

    @pytest.fixture
    def mock_registry(self) -> EnrichmentRegistry:
        registry = EnrichmentRegistry()
        registry._discovered = True
        return registry

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        return {
            "enrichment": {
                "batch_size": 10,
                "providers": {"raw_request": {"enabled": True}},
            }
        }

    def test_a_non_request_exception_reports_its_type_not_its_message(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        item = ContentItem(
            id="movie1",
            title=self._FORGED,
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        mock_storage.enrichment.items_needing.side_effect = [[(1, item)], []]
        mock_storage.enrichment.count_needing.return_value = 1
        mock_registry.register(
            RawRequestErrorProvider(ValueError(f"bad response for {self._FORGED}"))
        )

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        with caplog.at_level(logging.WARNING, logger="src.enrichment.manager"):
            manager.start_enrichment()
            manager._wait_for_completion()

        joined = " ".join(manager.get_status().errors)
        assert joined == "raw_request: ValueError"
        assert "bad response" not in caplog.text
        assert self._FORGED not in caplog.text

    def test_a_job_level_failure_reports_only_its_type_to_clients(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """``status.errors`` is served over HTTP, so it carries no message."""
        mock_storage.enrichment.count_needing.side_effect = ValueError(
            f"query failed for {self._FORGED}"
        )

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        manager.start_enrichment()
        manager._wait_for_completion()

        assert manager.get_status().errors == ["Job error: ValueError"]

    def test_a_job_level_failure_logs_a_traceback_for_the_operator(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Withholding the message left a bare class name nobody could act on."""

        def _raise_inside_the_job(*_args: object, **_kwargs: object) -> int:
            raise ValueError("query failed")

        mock_storage.enrichment.count_needing.side_effect = _raise_inside_the_job

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        with caplog.at_level(logging.ERROR, logger="src.enrichment.manager"):
            manager.start_enrichment()
            manager._wait_for_completion()

        assert "Enrichment job failed: ValueError" in caplog.text
        assert (
            "_raise_inside_the_job" in caplog.text
        ), "expected the traceback to locate the frame that raised"


class TestEnrichmentProgressRegression:
    """Regression tests for enrichment progress reporting (issue #60).

    Reported symptom: the web UI showed enrichment ``total_items`` jumping in
    batch_size steps (e.g. 50, 100, 150, 200) for a 200-item enrichment run
    rather than displaying the real total from the start.

    Root cause: ``EnrichmentManager._run_enrichment`` accumulated the total
    via ``self._status.total_items += len(items)`` inside the batch loop, so
    the value reported by ``get_status()`` only matched reality once every
    page had been fetched.

    Fix: query ``EnrichmentStore.count_needing`` once before
    the batch loop and assign ``total_items`` in a single statement (combined
    with the precomputed ``not_found_ids`` set when retrying not-found items).
    """

    @pytest.fixture
    def mock_storage(self, tmp_path: Path) -> MagicMock:
        storage = MagicMock(spec=StorageManager)
        storage.enrichment.items_needing.return_value = []
        storage.enrichment.count_needing.return_value = 0
        return job_backed(storage, tmp_path)

    @pytest.fixture
    def mock_registry(self) -> EnrichmentRegistry:
        registry = EnrichmentRegistry()
        registry._discovered = True
        return registry

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        return {
            "enrichment": {
                "batch_size": 10,
                "providers": {"mock": {"enabled": True}},
            }
        }

    def test_total_items_set_before_first_batch_regression(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """total_items must equal the upfront count when the first batch starts.

        Pre-fix the value would have been ``len(items)`` for the first batch
        (1) and grown to 3 only after the third batch. Post-fix the count
        method is consulted once and total_items reflects the real total
        from batch 1 onward.
        """
        items_batches = [
            [
                (
                    db_id,
                    ContentItem(
                        id=f"movie{db_id}",
                        title=f"Movie {db_id}",
                        content_type=ContentType.MOVIE,
                        status=ConsumptionStatus.UNREAD,
                    ),
                )
            ]
            for db_id in (1, 2, 3)
        ]
        mock_storage.enrichment.items_needing.side_effect = items_batches + [[]]
        mock_storage.enrichment.count_needing.return_value = 3

        provider = MockProvider()
        mock_registry.register(provider)

        manager = EnrichmentManager(mock_storage, config, mock_registry)

        observed_totals: list[int] = []
        original_process_batch = manager._process_batch

        def capture_total(items: list[tuple[int, ContentItem]]) -> None:
            with manager._lock:
                observed_totals.append(manager._status.total_items)
            original_process_batch(items)

        with patch.object(manager, "_process_batch", side_effect=capture_total):
            manager.start_enrichment()
            manager._wait_for_completion()

        assert observed_totals == [3, 3, 3], (
            "total_items should report the full count from the first batch onward, "
            f"got {observed_totals}"
        )
        mock_storage.enrichment.count_needing.assert_called_once_with(
            content_type=None,
            user_id=None,
        )

    def test_total_items_includes_not_found_when_retrying_regression(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """With include_not_found=True, total_items = pending count + not_found IDs.

        A retried miss is counted only once it joins a batch, so a total
        without the upfront set understates the work.
        """
        not_found_item = ContentItem(
            id="movie99",
            title="Previously Not Found",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        not_found_item.db_id = 99
        mock_storage.enrichment.not_found_ids.return_value = [99]
        # Two empty batches: the loop must exit on the break, not StopIteration.
        mock_storage.enrichment.items_needing.side_effect = [[], []]
        mock_storage.enrichment.count_needing.return_value = 5
        mock_storage.get_content_items_by_db_ids.return_value = [not_found_item]

        provider = MockProvider()
        mock_registry.register(provider)

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        manager.start_enrichment(include_not_found=True)
        manager._wait_for_completion()

        status = manager.get_status()
        assert status.total_items == 6  # 5 pending + 1 not_found
        # Guard that the job ended via the normal completion path, not via
        # the broad except in _run_enrichment swallowing a mock-setup error.
        assert status.completed is True
        assert status.errors == []
        mock_storage.enrichment.count_needing.assert_called_once_with(
            content_type=None,
            user_id=None,
        )


class TestTransientProviderFailureIsRetryable:
    """Regression: a transient provider failure must not settle as not_found.

    Reported symptom: enriching while the network was down (or while TMDB was
    rate-limiting) finished with every movie listed under "Items not found".
    Restoring the network and re-running enriched nothing — recovery needed
    ``enrichment reset`` or ``--retry-not-found``, and neither the CLI nor the
    web UI said the items had been skipped for a transport reason.

    Root cause: ``_process_item`` caught every provider exception and then fell
    out of the provider loop into the same terminal branch used when a provider
    genuinely reported no match, writing
    ``mark_enrichment_complete(db_id, "none", "not_found")``. Because
    ``get_items_needing_enrichment`` excludes not_found rows by default, those
    items were never picked up again.

    Fix: the manager tracks the providers that raised for an item. When the
    loop ends with no match and at least one provider raised, it calls
    ``mark_enrichment_failed`` — which leaves ``needs_enrichment = 1`` so a
    later run retries the item — and counts the item under ``items_failed``.
    The settled path, where every provider answered and none had a match, still
    writes ``not_found``.
    """

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @pytest.fixture
    def registry(self) -> EnrichmentRegistry:
        registry = EnrichmentRegistry()
        registry._discovered = True
        return registry

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        return {
            "enrichment": {
                "batch_size": 10,
                "providers": {
                    "mock": {"enabled": True},
                    "raw_request": {"enabled": True},
                },
            }
        }

    def test_raising_provider_leaves_item_requeued_regression(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """An unreachable provider leaves the item queued, not marked not_found."""
        db_id = save_movie(storage_manager)
        registry.register(
            RawRequestErrorProvider(requests.ConnectionError("network is unreachable"))
        )

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_quality"] != "not_found"
        assert status["enrichment_error"] is not None
        assert queued_ids(storage_manager) == {db_id}

        job_status = manager.get_status()
        assert job_status.completed is True
        assert job_status.items_failed == 1
        assert job_status.items_not_found == 0

    def test_second_run_enriches_item_once_provider_recovers(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """The retry is real: a run after the outage enriches the same item.

        The reported counts have to move with it. ``write_enrichment_complete``
        clears ``enrichment_error``, and only that clearing takes the recovered
        item out of ``failed`` — left behind, it would be reported as enriched
        *and* failed, which is the double-count the four-state partition was
        just fixed for, one transition later.
        """
        db_id = save_movie(storage_manager)
        registry.register(
            RawRequestErrorProvider(requests.ConnectionError("network is unreachable"))
        )

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()
        assert storage_manager.get_content_item(db_id).enriched is False
        assert enrichment_buckets(storage_manager) == {
            "enriched": 0,
            "pending": 0,
            "not_found": 0,
            "failed": 1,
        }

        # Network is back: a working provider answers under the same name.
        recovered = MockProvider(name="raw_request")
        recovered_registry = EnrichmentRegistry()
        recovered_registry._discovered = True
        recovered_registry.register(recovered)

        manager = EnrichmentManager(storage_manager, config, recovered_registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        assert [item.title for item in recovered.enrich_calls] == ["The Matrix"]
        enriched_item = storage_manager.get_content_item(db_id)
        assert enriched_item.enriched is True
        assert enriched_item.metadata.get("genres") == ["Action", "Drama"]
        assert manager.get_status().items_enriched == 1
        assert enrichment_buckets(storage_manager) == {
            "enriched": 1,
            "pending": 0,
            "not_found": 0,
            "failed": 0,
        }

    def test_clean_no_match_still_settles_as_not_found(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Positive control: an answered no-match settles and is not re-queued.

        Guards the over-correction where every miss becomes an infinite retry.
        """
        db_id = save_movie(storage_manager)
        registry.register(MockProvider(should_not_find=True))

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_quality"] == "not_found"
        assert status["enrichment_error"] is None
        assert queued_ids(storage_manager) == set()

        job_status = manager.get_status()
        assert job_status.items_not_found == 1
        assert job_status.items_failed == 0

    def test_partial_failure_does_not_settle_as_not_found(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """One provider answering "no match" cannot settle for one that errored.

        The erroring provider may well have been the item's only chance of a
        match, so the item stays queued even though the other provider replied.
        """
        db_id = save_movie(storage_manager)
        answering = MockProvider(should_not_find=True)
        registry.register(answering)
        registry.register(
            RawRequestErrorProvider(requests.ConnectionError("network is unreachable"))
        )

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        assert len(answering.enrich_calls) == 1, "the healthy provider was consulted"
        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_quality"] != "not_found"
        assert queued_ids(storage_manager) == {db_id}

        job_status = manager.get_status()
        assert job_status.items_failed == 1
        assert job_status.items_not_found == 0

    def test_run_reaches_the_items_queued_behind_a_failing_batch(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Failures in batch one must not starve the items behind them.

        A failed item stays in the pending query, so the fetch window has to
        widen past the items already attempted. Without that, the second fetch
        would return the same failed rows, the skip filter would empty the
        batch, and the run would break out with later items never tried.
        """
        config["enrichment"]["batch_size"] = 2
        db_ids = {save_movie(storage_manager, f"Movie {index}") for index in range(5)}
        failing = MockProvider(should_fail=True)
        registry.register(failing)

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        attempted = [item.title for item in failing.enrich_calls]
        assert sorted(attempted) == [
            "Movie 0",
            "Movie 1",
            "Movie 2",
            "Movie 3",
            "Movie 4",
        ]
        assert len(attempted) == len(set(attempted)), "each item is attempted once"
        assert queued_ids(storage_manager) == db_ids

        job_status = manager.get_status()
        assert job_status.completed is True
        assert job_status.items_failed == 5
        assert job_status.items_not_found == 0
        # A re-fetched failure would be counted twice and push progress past
        # 100%, so processed must still match the upfront total.
        assert job_status.items_processed == 5
        assert job_status.total_items == 5

    def test_error_from_one_provider_does_not_block_a_later_match(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """A provider that raises before a healthy one still ends enriched.

        The recorded error must not outweigh a real match found afterwards:
        the item settles as enriched and leaves the queue, while the transport
        failure is still reported in the job status.
        """
        db_id = save_movie(storage_manager)
        registry.register(
            RawRequestErrorProvider(requests.ConnectionError("network is unreachable"))
        )
        registry.register(MockProvider())

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_provider"] == "mock"
        assert status["enrichment_quality"] == "high"
        assert status["enrichment_error"] is None
        assert storage_manager.get_content_item(db_id).enriched is True
        assert queued_ids(storage_manager) == set()

        job_status = manager.get_status()
        assert job_status.items_enriched == 1
        assert job_status.items_failed == 0
        assert any("raw_request" in error for error in job_status.errors)

    def test_retry_not_found_run_records_a_failure_as_retryable(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """A retry pass that hits an outage must not re-settle the item.

        ``--retry-not-found`` pulls settled misses back in from a separate set.
        If the provider errors on that pass, the item has to come back out as a
        retryable failure rather than being written back as not_found, which
        would need a second manual retry to recover.
        """
        db_id = save_movie(storage_manager)
        storage_manager.enrichment.mark_complete(db_id, "none", "not_found")
        registry.register(
            RawRequestErrorProvider(requests.ConnectionError("network is unreachable"))
        )

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE, include_not_found=True)
        assert manager._wait_for_completion()

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_quality"] != "not_found"
        assert status["enrichment_error"] is not None
        assert queued_ids(storage_manager) == {db_id}

        job_status = manager.get_status()
        assert job_status.completed is True
        assert job_status.items_failed == 1
        assert job_status.items_not_found == 0

    def test_recorded_error_names_every_provider_that_raised(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """The stored error has to say which providers failed, and how.

        The reported symptom included "nothing tells the user the items were
        skipped for a transport reason", so a failure that swallowed the
        provider names would leave the same blind spot. Covers both catch
        branches at once: a wrapped ``ProviderError`` and a raw ``requests``
        exception. The reason recorded beside each name is the one the manager
        derived, never the provider's own message — see
        ``TestPersistedEnrichmentErrorIsDerived``.
        """
        db_id = save_movie(storage_manager)
        registry.register(MockProvider(should_fail=True))
        registry.register(
            RawRequestErrorProvider(requests.ConnectionError("network is unreachable"))
        )

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert (
            status["enrichment_error"]
            == "mock: ProviderError; raw_request: ConnectionError"
        )
        assert queued_ids(storage_manager) == {db_id}

    def test_a_failed_save_is_ours_and_settles_without_asking_more_providers(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """A broken write was filed as a retryable provider failure."""
        save_movie(storage_manager)
        answering = MockProvider()
        untouched = MockProvider(name="raw_request")
        registry.register(answering)
        registry.register(untouched)

        manager = EnrichmentManager(storage_manager, config, registry)
        with patch.object(
            storage_manager,
            "save_enrichment_metadata",
            side_effect=OSError("disk full"),
        ):
            manager.start_enrichment(content_type=ContentType.MOVIE)
            assert manager._wait_for_completion()

        assert len(answering.enrich_calls) == 1
        assert untouched.enrich_calls == []
        assert queued_ids(storage_manager) == set()
        assert manager.get_status().errors == ["storage: OSError"]
        assert manager.get_status().items_failed == 1

    def test_mixed_batch_requeues_only_the_failed_item(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """One item's failure must not drag its neighbours back into the queue.

        A match, a settled miss and a transport failure in the same batch each
        keep their own outcome.
        """

        class ScriptedProvider(MockProvider):
            """Outcome per item: match, clean no-match, or raise."""

            def enrich(
                self, item: ContentItem, config: dict[str, Any]
            ) -> EnrichmentResult | None:
                self.enrich_calls.append(item)
                if item.title == "Broken Movie":
                    raise ProviderError(self._name, "upstream 503")
                if item.title == "Missing Movie":
                    return EnrichmentResult(
                        match_quality="not_found", provider=self._name
                    )
                return EnrichmentResult(
                    genres=["Action"], match_quality="high", provider=self._name
                )

        found_id = save_movie(storage_manager, "Found Movie")
        missing_id = save_movie(storage_manager, "Missing Movie")
        broken_id = save_movie(storage_manager, "Broken Movie")
        registry.register(ScriptedProvider())

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        assert queued_ids(storage_manager) == {broken_id}
        found_status = storage_manager.enrichment.status(found_id)
        assert found_status is not None
        assert found_status["enrichment_quality"] == "high"
        missing_status = storage_manager.enrichment.status(missing_id)
        assert missing_status is not None
        assert missing_status["enrichment_quality"] == "not_found"
        assert missing_status["enrichment_error"] is None
        broken_status = storage_manager.enrichment.status(broken_id)
        assert broken_status is not None
        assert broken_status["enrichment_error"] is not None

        job_status = manager.get_status()
        assert job_status.items_processed == 3
        assert job_status.items_enriched == 1
        assert job_status.items_not_found == 1
        assert job_status.items_failed == 1


class TestPermanentProviderFailureStopsRetrying:
    """Regression: a provider that rejects every request must not be retried.

    Reported symptom: after a TMDB key was revoked, every enrichment run
    re-attempted the whole library against TMDB. Each item got a 401, each 401
    left the item queued, and the next run did it again — thousands of rejected
    requests per run at a provider that could not answer any of them, which is
    how an API key or an IP address gets rate-limited or banned outright.

    Root cause: the fix for the transient-failure bug above routed *every*
    ``ProviderError`` to ``mark_enrichment_failed``, which keeps
    ``needs_enrichment = 1``. A rejected credential is not transient, so the
    retry could never succeed and nothing bounded it.

    Fix: the manager classifies a failure from the ``requests`` exception on
    the exception chain, which every raise form leaves reachable — ``from
    error`` on ``__cause__``, implicit chaining and ``from None`` on
    ``__context__``. Transport failures and 5xx/408/429 stay retryable; any
    other client error settles the item as before, taking it out of the queue.

    Both wrapping forms are exercised: reading ``__suppress_context__`` — the
    semantically correct reading of the ``from None`` TMDB and RAWG raise —
    would call every one of their 4xx retryable and re-flood the queue.
    """

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @pytest.fixture
    def registry(self) -> EnrichmentRegistry:
        registry = EnrichmentRegistry()
        registry._discovered = True
        return registry

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        return {
            "enrichment": {
                "batch_size": 10,
                "providers": {
                    "mock": {"enabled": True},
                    "raw_request": {"enabled": True},
                    "wrapped_request": {"enabled": True},
                    "suppressed_request": {"enabled": True},
                },
            }
        }

    @pytest.mark.parametrize("provider_class", WRAPPING_PROVIDERS)
    def test_rejected_api_key_settles_the_item_regression(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
        provider_class: type[WrappedRequestErrorProvider],
    ) -> None:
        """A 401 leaves the queue empty, so the next run does not re-ask."""
        db_id = save_movie(storage_manager)
        provider = provider_class(http_error(401))
        registry.register(provider)

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_quality"] == "not_found"
        assert queued_ids(storage_manager) == set()

        job_status = manager.get_status()
        assert job_status.items_not_found == 1
        assert job_status.items_failed == 0
        assert any(provider.name in error for error in job_status.errors)

    def test_unwrapped_client_error_settles_the_item_regression(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """The classification does not depend on the provider wrapping its error.

        A 403 escaping ``enrich`` unwrapped reaches the manager's catch-all
        branch rather than the ``ProviderError`` one, and has to settle there
        too — otherwise a single sloppy provider still floods.
        """
        save_movie(storage_manager)
        registry.register(RawRequestErrorProvider(http_error(403)))

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        assert queued_ids(storage_manager) == set()
        assert manager.get_status().items_not_found == 1

    @pytest.mark.parametrize("provider_class", WRAPPING_PROVIDERS)
    @pytest.mark.parametrize("status_code", [429, 503])
    def test_retryable_status_still_requeues_the_item(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
        status_code: int,
        provider_class: type[WrappedRequestErrorProvider],
    ) -> None:
        """Positive control: throttling and server faults keep their retry.

        Guards the over-correction where narrowing the retry swallows the
        failures that really do clear on their own.
        """
        db_id = save_movie(storage_manager)
        provider = provider_class(http_error(status_code))
        registry.register(provider)

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_error"] == f"{provider.name}: HTTP {status_code}"
        assert queued_ids(storage_manager) == {db_id}
        assert manager.get_status().items_failed == 1

    def test_one_retryable_provider_keeps_the_item_queued(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """A rejected provider must not settle an item another could still match.

        The timing-out provider may be the item's only chance of a match, so
        the permanent rejection from its neighbour does not get to retire it.
        """
        db_id = save_movie(storage_manager)
        registry.register(WrappedRequestErrorProvider(http_error(401)))
        registry.register(RawRequestErrorProvider(requests.Timeout("timed out")))

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        assert queued_ids(storage_manager) == {db_id}
        assert manager.get_status().items_failed == 1

    def test_a_provider_rejecting_every_item_is_abandoned_for_the_rest_of_the_run(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Settling rejections one at a time cost one request per library item."""
        db_ids = [save_movie(storage_manager, f"Movie {index}") for index in range(20)]
        provider = RawRequestErrorProvider(http_error(401))
        registry.register(provider)

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        asked = len(provider.enrich_calls)
        assert asked == _MAX_CONSECUTIVE_REJECTIONS
        assert queued_ids(storage_manager) == set(db_ids[asked:])
        assert storage_manager.enrichment.status(db_ids[-1]) is None
        job_status = manager.get_status()
        assert job_status.items_processed == asked
        assert job_status.items_not_found == asked
        assert job_status.items_failed == 0
        assert f"{provider.name}: abandoned" in " ".join(job_status.errors[:5]), (
            "_echo_errors shows errors[:5], so a reason recorded behind the "
            "rejections that caused it is one no operator reads"
        )

    def test_failure_with_no_request_error_stays_retryable(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """An unclassifiable failure keeps the benefit of the doubt.

        Only a client error positively identifies a request that can never
        succeed. A provider that fails some other way (its own parsing bug, a
        non-``requests`` HTTP client) has nothing on the chain to classify, and
        settling it would resurrect the bug the retry exists to fix.
        """
        db_id = save_movie(storage_manager)
        registry.register(MockProvider(should_fail=True))

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        assert queued_ids(storage_manager) == {db_id}
        assert manager.get_status().items_failed == 1


class TestPersistedEnrichmentErrorIsDerived:
    """Regression: a provider's own error text must never be written to disk.

    Reported concern: enrichment errors are now persisted to
    ``enrichment_status.enrichment_error``, an at-rest sink they never reached
    before, and the SQLite database is the file users are told to back up and
    copy between hosts. Enrichment providers are a documented extension point,
    and a provider is free to write ``ProviderError(name, f"failed: {error}")``
    around a bare ``requests`` exception — whose text is the full request URL,
    ``?api_key=<secret>`` included. That would defeat the encryption at rest
    the credential is otherwise held under.

    Root cause: the ``ProviderError`` branch appended ``error.message``
    verbatim, while the catch-all branch beside it scrubbed its exception.

    Fix: the persisted text is assembled at the sink from values the manager
    derives — the provider name plus the HTTP status or transport error class
    ``requests`` reported — so it is bounded no matter what a provider writes.

    Scope: the database is the sink under test. ``status.errors`` is a second
    sink, pinned by ``TestEnrichmentStatusApiKeyScrubbingRegression``; asserted
    here only for the secret's absence, since both now carry one description.
    """

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @pytest.fixture
    def registry(self) -> EnrichmentRegistry:
        registry = EnrichmentRegistry()
        registry._discovered = True
        return registry

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        return {
            "enrichment": {
                "batch_size": 10,
                "providers": {"wrapped_request": {"enabled": True}},
            }
        }

    def test_persisted_error_omits_the_provider_message_regression(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """An API key in a provider's message does not reach the database."""
        db_id = save_movie(storage_manager)
        registry.register(
            WrappedRequestErrorProvider(
                http_error(
                    503,
                    "503 Server Error for url: https://api.example.test"
                    "/3/search/movie?api_key=super-secret-key",
                )
            )
        )

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_error"] == "wrapped_request: HTTP 503"

        job_status = manager.get_status()
        joined = " ".join(job_status.errors)
        assert "wrapped_request: HTTP 503" in joined
        assert "super-secret-key" not in joined
        assert "api_key=" not in joined


class TestEnrichmentTitleCannotForgeALogLineRegression:
    """Regression: an item title went to the log unescaped.

    Titles come from imported files and ``POST /api/complete``, which restrict
    no characters, and the file log formatter is single-line — so a newline
    wrote what reads as its own entry (CWE-117).
    """

    _FORGED = "Real Title\nWARNING  | forged | line"

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @pytest.fixture
    def registry(self) -> EnrichmentRegistry:
        registry = EnrichmentRegistry()
        registry._discovered = True
        return registry

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        return {
            "enrichment": {
                "batch_size": 10,
                "providers": {"mock": {"enabled": True}},
            }
        }

    def test_a_newline_in_a_title_is_escaped_on_the_success_path(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The enriched-item INFO line runs at the default level."""
        save_movie(storage_manager, self._FORGED)
        registry.register(MockProvider())

        manager = EnrichmentManager(storage_manager, config, registry)
        with caplog.at_level(logging.INFO, logger="src.enrichment.manager"):
            manager.start_enrichment(content_type=ContentType.MOVIE)
            assert manager._wait_for_completion()

        assert "Real Title\\nWARNING" in caplog.text
        assert self._FORGED not in caplog.text

    def test_a_newline_in_a_title_is_escaped_on_the_rejected_path(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Every provider refusing the request logs the title at WARNING."""
        config["enrichment"]["providers"]["wrapped_request"] = {"enabled": True}
        save_movie(storage_manager, self._FORGED)
        registry.register(WrappedRequestErrorProvider(http_error(404)))

        manager = EnrichmentManager(storage_manager, config, registry)
        with caplog.at_level(logging.WARNING, logger="src.enrichment.manager"):
            manager.start_enrichment(content_type=ContentType.MOVIE)
            assert manager._wait_for_completion()

        assert "Real Title\\nWARNING" in caplog.text
        assert self._FORGED not in caplog.text


class TestEnrichmentFetchWindowRegression:
    """Regression: the fetch window must not widen as failures accumulate.

    Nothing was reported, because this never shipped: on ``main`` a provider
    failure settled as ``not_found`` and left the queue, so no run ever
    accumulated queued failures for the window to widen around. It was
    introduced by the transient-failure fix above and fixed here, both inside
    this change. Had it shipped, an outage would have made enrichment slower
    and heavier the larger the library got.

    Root cause: a failed item stays queued, so the manager asked for
    ``batch_size + len(failed_ids)`` rows and dropped the already-attempted
    ones in Python. ``get_items_needing_enrichment`` fully hydrates every row
    it returns into a ``ContentItem`` first, so during an outage — when every
    item fails — round *k* built ``k * batch_size`` items to attempt
    ``batch_size`` of them. Quadratic in the size of the library.

    Fix: the queue is ordered by database ID, so the manager walks a cursor
    forward and the exclusion happens in SQL. Every fetch is one batch wide
    and every row is hydrated exactly once.
    """

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @pytest.fixture
    def registry(self) -> EnrichmentRegistry:
        registry = EnrichmentRegistry()
        registry._discovered = True
        return registry

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        return {
            "enrichment": {
                "batch_size": 2,
                "providers": {"mock": {"enabled": True}},
            }
        }

    def test_every_fetch_stays_one_batch_wide_regression(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """Five items failing at batch size two: four fetches, five rows built."""
        for index in range(5):
            save_movie(storage_manager, f"Movie {index}")
        failing = MockProvider(should_fail=True)
        registry.register(failing)

        requested_limits: list[int] = []
        hydrated_rows = 0
        real_fetch = storage_manager.enrichment.items_needing

        def spy(**kwargs: Any) -> list[tuple[int, ContentItem]]:
            nonlocal hydrated_rows
            requested_limits.append(kwargs["limit"])
            fetched = real_fetch(**kwargs)
            hydrated_rows += len(fetched)
            return fetched

        with patch.object(storage_manager.enrichment, "items_needing", side_effect=spy):
            manager = EnrichmentManager(storage_manager, config, registry)
            manager.start_enrichment(content_type=ContentType.MOVIE)
            assert manager._wait_for_completion()

        assert requested_limits == [2, 2, 2, 2], (
            "the window must stay at batch_size — pre-fix it grew with every "
            f"failure, giving {requested_limits}"
        )
        assert hydrated_rows == 5, (
            "each queued item must be built once — pre-fix the already-failed "
            f"rows were re-fetched and re-built, giving {hydrated_rows}"
        )
        assert len(failing.enrich_calls) == 5
        assert manager.get_status().items_failed == 5


class TestManualEditEnrichmentProtectionRegression:
    """Regression: an automatic enrichment run must not clobber manual edits.

    Reported concern: a user hand-enters genres/tags/description on an item,
    then a later background enrichment run could append provider genres/tags
    (the merge is additive, not gap-fill) or otherwise mutate the manual data.

    Root cause / fix: a manual edit marks the item enriched
    (``needs_enrichment=0`` via the ``manual`` provider), so
    ``get_items_needing_enrichment`` excludes it and ``_apply_enrichment``
    never runs against it. This test drives the real StorageManager queue end
    to end to prove the manual item is never handed to a provider and its
    values survive a full run.
    """

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @pytest.fixture
    def registry(self) -> EnrichmentRegistry:
        registry = EnrichmentRegistry()
        registry._discovered = True
        return registry

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        return {
            "enrichment": {
                "batch_size": 10,
                "providers": {"mock": {"enabled": True}},
            }
        }

    def test_auto_enrichment_skips_manually_edited_item_regression(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        """The provider never sees the manual item; its values are untouched."""
        manual_id = storage_manager.save_content_item(
            ContentItem(
                id="manual_movie",
                title="Manual Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )
        auto_id = storage_manager.save_content_item(
            ContentItem(
                id="auto_movie",
                title="Auto Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )

        storage_manager.update_item_from_ui(
            db_id=manual_id,
            status="unread",
            genres=["Drama"],
            tags=["slow-burn"],
            description="Hand written synopsis.",
        )

        provider = MockProvider()
        registry.register(provider)

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment()
        manager._wait_for_completion()

        # Only the un-edited item was offered to the provider — exactly one
        # enrich call, never the manual item.
        assert len(provider.enrich_calls) == 1
        enriched_titles = {item.title for item in provider.enrich_calls}
        assert enriched_titles == {"Auto Movie"}

        # Manual values survive verbatim — no provider genres/tags appended.
        manual = storage_manager.get_content_item(manual_id)
        assert manual.metadata.get("genres") == ["Drama"]
        assert manual.metadata.get("tags") == ["slow-burn"]
        assert manual.metadata.get("description") == "Hand written synopsis."
        assert manual.enriched is True

        # The auto item did get enriched, proving the run actually executed.
        auto = storage_manager.get_content_item(auto_id)
        assert auto.metadata.get("genres") == ["Action", "Drama"]
        assert auto.enriched is True


class TestEnrichmentWritesTheRowItWasHandedRegression:
    """An upgraded library files its ids under a source no plugin answers to,
    so a run that looked its row up again wrote onto the older namesake."""

    @staticmethod
    def _dune(external_id: str) -> ContentItem:
        return ContentItem(
            id=external_id,
            title="Dune",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            source="trakt",
        )

    def test_the_namesake_that_was_not_queued_keeps_its_own_metadata(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        older = storage_manager.save_content_item(self._dune("1984"))
        newer = storage_manager.save_content_item(self._dune("2021"))
        with storage_manager.connection() as conn:
            conn.execute(
                "UPDATE content_item_external_ids SET source = ?"
                " WHERE content_item_id = ?",
                (_LEGACY_EXTERNAL_ID_SOURCE, newer),
            )
            conn.commit()
        # Leaves the queue holding the migrated row alone.
        storage_manager.enrichment.mark_complete(older, "tmdb", "high")
        registry = EnrichmentRegistry()
        registry._discovered = True
        registry.register(MockProvider())

        manager = EnrichmentManager(
            storage_manager,
            {"enrichment": {"providers": {"mock": {"enabled": True}}}},
            registry,
        )
        manager.start_enrichment()
        manager._wait_for_completion()

        assert "genres" in storage_manager.get_content_item(newer).metadata
        assert "genres" not in storage_manager.get_content_item(older).metadata


class TestARunReachesTMDBThroughTheGlobalRegistry:
    """Every other case here injects a registry of mocks.

    So a ``tmdb`` folder that stops being discovered, or a rename of either
    half of the name/config-key pair, leaves the suite green and enriches
    nothing.
    """

    _API_KEY = "tmdb-secret-key"

    @pytest.fixture(autouse=True)
    def _global_registry(self) -> Iterator[None]:
        EnrichmentRegistry.reset_instance()
        yield
        EnrichmentRegistry.reset_instance()

    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @pytest.fixture
    def config(self) -> dict[str, Any]:
        return {
            "enrichment": {
                "batch_size": 10,
                "providers": {
                    "tmdb": {
                        "enabled": True,
                        "api_key": self._API_KEY,
                        "include_keywords": False,
                    }
                },
            }
        }

    def _tmdb_url(self, path: str) -> str:
        return f"https://api.themoviedb.org/3/{path}"

    def test_a_movie_is_enriched_by_tmdb_with_no_registry_injected(
        self, storage_manager: StorageManager, config: dict[str, Any]
    ) -> None:
        """``EnrichmentManager(storage, config)`` is what the CLI command builds."""
        db_id = save_movie(storage_manager)
        payloads = {
            self._tmdb_url("search/movie"): {"results": [{"id": 603}]},
            self._tmdb_url("movie/603"): {
                "id": 603,
                "overview": "A hacker learns the true nature of reality.",
                "genres": [{"name": "Action"}, {"name": "Science Fiction"}],
                "runtime": 136,
                "release_date": "1999-03-30",
            },
        }

        def fake_get(url: str, **_kwargs: Any) -> MagicMock:
            return MagicMock(
                spec=requests.Response,
                status_code=200,
                json=lambda: payloads[url],
            )

        manager = EnrichmentManager(storage_manager, config)
        with patch("src.enrichment.providers.tmdb.tmdb.requests.get", fake_get):
            manager.start_enrichment(content_type=ContentType.MOVIE)
            assert manager._wait_for_completion()

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_provider"] == "tmdb"
        assert status["enrichment_quality"] == "high"
        enriched = storage_manager.get_content_item(db_id)
        assert enriched.metadata.get("genres") == ["Action", "Science Fiction"]
        assert manager.get_status().items_enriched == 1
        assert queued_ids(storage_manager) == set()
