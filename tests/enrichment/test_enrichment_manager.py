import logging
import sqlite3
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
    EnrichmentStart,
    PinRefused,
    merge_enrichment,
)
from src.enrichment.provider_base import (
    ConfigField,
    EnrichmentProvider,
    EnrichmentResult,
    ProviderError,
    SeriesOrdinal,
)
from src.enrichment.providers.tmdb.tmdb import TMDBProvider
from src.enrichment.providers.wikidata.wikidata import WikidataProvider
from src.enrichment.registry import EnrichmentRegistry
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.enrichment_status import EnrichmentStore
from src.storage.manager import StorageManager
from src.storage.schema import _LEGACY_EXTERNAL_ID_SOURCE
from src.utils.matching import Candidate
from tests.factories import make_storage_mock


def job_backed(storage: MagicMock, tmp_path: Path) -> MagicMock:
    storage.enrichment_jobs = StorageManager(
        sqlite_path=tmp_path / "enrichment-job.db"
    ).enrichment_jobs
    return storage


class MockProvider(EnrichmentProvider):
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
        return 100.0

    def get_config_schema(self) -> list[ConfigField]:
        return []

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        return []

    def accepts_record_id(self, record_id: str) -> bool:
        return bool(record_id.strip())

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        self.enrich_calls.append(item)

        if self._should_fail:
            raise ProviderError(self._name, "Simulated failure")

        if self._should_not_find:
            return EnrichmentResult(match_quality="not_found")

        return EnrichmentResult(
            genres=["Action", "Drama"],
            tags=["test-tag"],
            description="A test description.",
            extra_metadata={"source_rating": 8.5},
            match_quality="high",
        )


class RawRequestErrorProvider(EnrichmentProvider):
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


WRAPPING_PROVIDERS = [
    WrappedRequestErrorProvider,
    SuppressedContextRequestErrorProvider,
]


def http_error(status_code: int, message: str = "") -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(message or f"{status_code} Error", response=response)


def save_movie(
    storage_manager: StorageManager,
    title: str = "The Matrix",
    metadata: dict[str, Any] | None = None,
) -> int:
    return storage_manager.save_content_item(
        ContentItem(
            id=title.lower().replace(" ", "-"),
            title=title,
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata=metadata or {},
        )
    )


def queued_ids(storage_manager: StorageManager) -> set[int]:
    return {
        db_id
        for db_id, _item in storage_manager.enrichment.items_needing(
            content_type=ContentType.MOVIE
        )
    }


def manager_over(
    storage_manager: StorageManager, *providers: EnrichmentProvider
) -> EnrichmentManager:
    registry = EnrichmentRegistry()
    registry._discovered = True
    enabled: dict[str, dict[str, bool]] = {}
    for provider in providers:
        registry.register(provider)
        enabled[provider.name] = {"enabled": True}
    return EnrichmentManager(
        storage_manager,
        {"enrichment": {"enabled": True, "providers": enabled}},
        registry,
    )


def enrichment_buckets(storage_manager: StorageManager) -> dict[str, int]:
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
    def test_merge_fills_and_combines_fields(self) -> None:
        existing = {
            "genres": ["Comedy"],
            "director": "Someone",
        }
        result = EnrichmentResult(
            genres=["Action"],
            tags=["funny"],
            description="A comedy.",
            extra_metadata={"director": "Someone Else", "runtime": 90},
        )

        merged = merge_enrichment(existing, result)

        assert merged["genres"] == ["Action", "Comedy"]
        assert merged["tags"] == ["funny"]
        assert merged["description"] == "A comedy."
        assert merged["director"] == "Someone"
        assert merged["runtime"] == 90

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
        result = EnrichmentResult()
        setattr(result, field, ["Science Fiction"])

        assert merge_enrichment({field: stored}, result)[field] == expected

    def test_re_enriching_replaces_a_franchise_stored_from_a_worse_guess(self) -> None:
        better = EnrichmentResult(extra_metadata={"franchise": "Donkey Kong"})

        assert merge_enrichment({"franchise": "Donkey"}, better) == {
            "franchise": "Donkey Kong"
        }

    def test_a_series_key_ingestion_also_writes_is_never_replaced(self) -> None:
        stored = {"series_name": "Alien", "series_position": 1}
        result = EnrichmentResult(
            extra_metadata={"series_name": "Alien Collection", "series_position": 3},
        )

        assert merge_enrichment(stored, result) == stored

    def test_merging_leaves_the_callers_metadata_untouched(self) -> None:
        existing = {"genres": ["Comedy"]}
        merge_enrichment(existing, EnrichmentResult(genres=["Action"]))
        assert existing == {"genres": ["Comedy"]}


class TestEnrichmentManager:
    @pytest.fixture
    def mock_storage(self, tmp_path: Path) -> MagicMock:
        storage = make_storage_mock()
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
                "enabled": True,
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
            assert manager.start_enrichment() is EnrichmentStart.STARTED
            assert started_enrich.wait(timeout=5.0)

            result = manager.start_enrichment()

            assert result is EnrichmentStart.ALREADY_RUNNING
        finally:
            release_enrich.set()
            manager._wait_for_completion()

    def test_no_providers_for_content_type(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
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

        provider = MockProvider(content_types=[ContentType.MOVIE])
        mock_registry.register(provider)

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        manager.start_enrichment()

        manager._wait_for_completion()

        status = manager.get_status()
        assert status.items_not_found == 1

    def test_a_type_scoped_run_with_no_provider_for_it_never_starts(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        mock_registry.register(MockProvider(content_types=[ContentType.MOVIE]))
        manager = EnrichmentManager(mock_storage, config, mock_registry)

        started = manager.start_enrichment(content_type=ContentType.BOOK)

        assert started is EnrichmentStart.UNAVAILABLE
        mock_storage.enrichment.items_needing.assert_not_called()

    def test_a_run_scoped_to_an_item_that_is_gone_never_takes_the_claim(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        mock_registry.register(MockProvider())
        mock_storage.get_content_item.return_value = None
        manager = EnrichmentManager(mock_storage, config, mock_registry)

        started = manager.start_enrichment(content_item_id=404)

        assert started is EnrichmentStart.UNAVAILABLE
        assert mock_storage.enrichment_jobs.claim(None)


class TestEnrichmentStatusApiKeyScrubbingRegression:
    _API_KEY = "SECRET_MANAGER_KEY_123"

    @pytest.fixture
    def mock_storage(self, tmp_path: Path) -> MagicMock:
        storage = make_storage_mock()
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
                "enabled": True,
                "batch_size": 10,
                "providers": {
                    "raw_request": {"enabled": True},
                    "wrapped_request": {"enabled": True},
                },
            }
        }

    def _http_error(self, status_code: int = 401) -> requests.HTTPError:
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
    _FORGED = "Real Title\nWARNING  | forged | line"

    @pytest.fixture
    def mock_storage(self, tmp_path: Path) -> MagicMock:
        storage = make_storage_mock()
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
                "enabled": True,
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
        mock_storage.enrichment.count_needing.side_effect = ValueError(
            f"query failed for {self._FORGED}"
        )
        mock_registry.register(RawRequestErrorProvider(ValueError("unreached")))

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
        def _raise_inside_the_job(*_args: object, **_kwargs: object) -> int:
            raise ValueError("query failed")

        mock_storage.enrichment.count_needing.side_effect = _raise_inside_the_job
        mock_registry.register(RawRequestErrorProvider(ValueError("unreached")))

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        with caplog.at_level(logging.ERROR, logger="src.enrichment.manager"):
            manager.start_enrichment()
            manager._wait_for_completion()

        assert "Enrichment job failed: ValueError" in caplog.text
        assert (
            "_raise_inside_the_job" in caplog.text
        ), "expected the traceback to locate the frame that raised"


class TestEnrichmentProgressRegression:
    """Regression tests for enrichment progress reporting (issue #60)."""

    @pytest.fixture
    def mock_storage(self, tmp_path: Path) -> MagicMock:
        storage = make_storage_mock()
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
                "enabled": True,
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
            content_item_id=None,
        )

    def test_total_items_includes_not_found_when_retrying_regression(
        self,
        mock_storage: MagicMock,
        mock_registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        not_found_item = ContentItem(
            id="movie99",
            title="Previously Not Found",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        not_found_item.db_id = 99
        mock_storage.enrichment.not_found_ids.return_value = [99]
        mock_storage.enrichment.items_needing.side_effect = [[], []]
        mock_storage.enrichment.count_needing.return_value = 5
        mock_storage.get_content_items_by_db_ids.return_value = [not_found_item]

        provider = MockProvider()
        mock_registry.register(provider)

        manager = EnrichmentManager(mock_storage, config, mock_registry)
        manager.start_enrichment(include_not_found=True)
        manager._wait_for_completion()

        status = manager.get_status()
        assert status.total_items == 6
        assert status.completed is True
        assert status.errors == []
        mock_storage.enrichment.count_needing.assert_called_once_with(
            content_type=None,
            user_id=None,
            content_item_id=None,
        )


class TestRetryNotFoundSetIsBuiltInOneQuery:
    def test_a_retry_run_reaches_the_settled_misses_without_per_item_status_reads(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        registry = EnrichmentRegistry()
        registry._discovered = True
        config = {
            "enrichment": {"enabled": True, "providers": {"mock": {"enabled": True}}}
        }
        settled = save_movie(storage_manager, "Missing Movie")
        storage_manager.enrichment.mark_complete(settled, "none", "not_found")
        enriched = save_movie(storage_manager, "Found Movie")
        storage_manager.enrichment.mark_complete(enriched, "tmdb", "high")
        save_movie(storage_manager, "New Movie")
        provider = MockProvider()
        registry.register(provider)

        manager = EnrichmentManager(storage_manager, config, registry)
        with patch.object(EnrichmentStore, "status", autospec=True) as per_item_status:
            manager.start_enrichment(
                content_type=ContentType.MOVIE, include_not_found=True
            )
            assert manager._wait_for_completion()

        per_item_status.assert_not_called()
        assert sorted(item.title for item in provider.enrich_calls) == [
            "Missing Movie",
            "New Movie",
        ]
        assert manager.get_status().items_processed == 2


class TestTransientProviderFailureIsRetryable:
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
                "enabled": True,
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
        assert job_status.items_processed == 5
        assert job_status.total_items == 5

    def test_error_from_one_provider_does_not_block_a_later_match(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
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
        assert enrichment_buckets(storage_manager)["failed"] == 1, (
            "the run counted the item failed, so a not_found row would tell "
            "the operator no provider had it when one did"
        )

    def test_mixed_batch_requeues_only_the_failed_item(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        class ScriptedProvider(MockProvider):
            def enrich(
                self, item: ContentItem, config: dict[str, Any]
            ) -> EnrichmentResult | None:
                self.enrich_calls.append(item)
                if item.title == "Broken Movie":
                    raise ProviderError(self._name, "upstream 503")
                if item.title == "Missing Movie":
                    return EnrichmentResult(match_quality="not_found")
                return EnrichmentResult(genres=["Action"], match_quality="high")

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
                "enabled": True,
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
            "the reason the run stopped must not sit behind the duplicate "
            "rejections that caused it"
        )

    def test_abandoning_one_types_providers_ends_a_run_scoped_to_that_type(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        for index in range(20):
            save_movie(storage_manager, f"Movie {index}")
        registry.register(RawRequestErrorProvider(http_error(401)))
        registry.register(MockProvider(content_types=[ContentType.BOOK]))

        pages = 0
        real_fetch = storage_manager.enrichment.items_needing

        def spy(**kwargs: Any) -> list[tuple[int, ContentItem]]:
            nonlocal pages
            pages += 1
            return real_fetch(**kwargs)

        with patch.object(storage_manager.enrichment, "items_needing", side_effect=spy):
            manager = EnrichmentManager(storage_manager, config, registry)
            manager.start_enrichment(content_type=ContentType.MOVIE)
            assert manager._wait_for_completion()

        assert pages == 1, (
            "the run must stop at the batch that abandoned the movie provider, "
            f"not page the rest of the queue to skip it — took {pages} fetches"
        )
        job_status = manager.get_status()
        assert not job_status.completed, (
            "a run that gave up left items untouched; reporting it completed "
            "sends the operator looking anywhere but at the rejected key"
        )
        assert not job_status.cancelled

    def test_an_unfiltered_run_that_gave_up_on_one_type_is_not_completed(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        for index in range(20):
            save_movie(storage_manager, f"Movie {index}")
        book_id = storage_manager.save_content_item(
            ContentItem(
                id="a-book",
                title="A Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )
        registry.register(RawRequestErrorProvider(http_error(401)))
        registry.register(MockProvider(content_types=[ContentType.BOOK]))

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment()
        assert manager._wait_for_completion()

        job_status = manager.get_status()
        assert not job_status.completed
        assert not job_status.cancelled
        book_status = storage_manager.enrichment.status(book_id)
        assert book_status is not None
        assert book_status["enrichment_quality"] == "high"

    def test_a_second_provider_for_the_type_keeps_the_run_going(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        for index in range(20):
            save_movie(storage_manager, f"Movie {index}")
        registry.register(RawRequestErrorProvider(http_error(401)))
        answering = MockProvider()
        registry.register(answering)

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        assert len(answering.enrich_calls) == 20
        assert queued_ids(storage_manager) == set()
        assert manager.get_status().items_enriched == 20

    def test_failure_with_no_request_error_stays_retryable(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
        db_id = save_movie(storage_manager)
        registry.register(MockProvider(should_fail=True))

        manager = EnrichmentManager(storage_manager, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        assert queued_ids(storage_manager) == {db_id}
        assert manager.get_status().items_failed == 1


class TestPersistedEnrichmentErrorIsDerived:
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
                "enabled": True,
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
                "enabled": True,
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
                "enabled": True,
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
                "enabled": True,
                "batch_size": 10,
                "providers": {"mock": {"enabled": True}},
            }
        }

    def test_auto_enrichment_never_overwrites_a_held_field_regression(
        self,
        storage_manager: StorageManager,
        registry: EnrichmentRegistry,
        config: dict[str, Any],
    ) -> None:
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

        # Both are offered to the provider — the holds, not a skipped item, are
        # what keep the hand-written values.
        assert {item.title for item in provider.enrich_calls} == {
            "Auto Movie",
            "Manual Movie",
        }

        manual = storage_manager.get_content_item(manual_id)
        assert manual.metadata.get("genres") == ["Drama"]
        assert manual.metadata.get("tags") == ["slow-burn"]
        assert manual.metadata.get("description") == "Hand written synopsis."
        assert manual.enriched is True

        auto = storage_manager.get_content_item(auto_id)
        assert auto.metadata.get("genres") == ["Action", "Drama"]
        assert auto.enriched is True


class TestPinnedProviderRecord:
    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @staticmethod
    def _movie() -> ContentItem:
        return ContentItem(
            id="tt1",
            title="Prey",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            source="radarr",
        )

    @staticmethod
    def _manager(
        storage_manager: StorageManager,
        *,
        enabled: bool = True,
        serves: list[ContentType] | None = None,
    ) -> EnrichmentManager:
        registry = EnrichmentRegistry()
        registry._discovered = True
        registry.register(MockProvider(content_types=serves))
        return EnrichmentManager(
            storage_manager,
            {
                "enrichment": {
                    "enabled": enabled,
                    "providers": {"mock": {"enabled": True}},
                }
            },
            registry,
        )

    def test_a_pin_outlives_a_later_sync_of_the_same_item(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(self._movie())
        manager = self._manager(storage_manager)

        manager.pin(
            db_id, storage_manager.get_content_item(db_id), "mock", "603", user_id=1
        )
        manager._wait_for_completion()
        storage_manager.save_content_item(self._movie())

        resynced = storage_manager.get_content_item(db_id)
        assert resynced.metadata["enrichment_ids"] == {"mock": "603"}

    def test_a_pin_outlives_the_enrichment_run_that_merges_metadata_over_it(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(self._movie())
        manager = self._manager(storage_manager)

        manager.pin(
            db_id, storage_manager.get_content_item(db_id), "mock", "603", user_id=1
        )
        manager._wait_for_completion()

        stored = storage_manager.get_content_item(db_id)
        assert stored.metadata["genres"] == ["Action", "Drama"]
        assert stored.metadata["enrichment_ids"] == {"mock": "603"}

    def test_clearing_a_pin_drops_it_and_re_queues_a_settled_item(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(self._movie())
        manager = self._manager(storage_manager)
        manager.pin(
            db_id, storage_manager.get_content_item(db_id), "mock", "603", user_id=1
        )
        manager._wait_for_completion()

        _pins, started = manager.pin(
            db_id, storage_manager.get_content_item(db_id), "mock", None, user_id=1
        )

        # Clearing hands the item back to the title match it was pinned away
        # from, so running now would re-apply the metadata that was rejected.
        assert started is None
        assert storage_manager.get_content_item(db_id).metadata["enrichment_ids"] == {}
        assert storage_manager.enrichment.status(db_id)["needs_enrichment"] is True

    def test_a_pin_enriches_the_item_pinned_and_no_other(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(self._movie())
        untouched_id = save_movie(storage_manager, "Dune")
        manager = self._manager(storage_manager)

        _pins, started = manager.pin(
            db_id, storage_manager.get_content_item(db_id), "mock", "603", user_id=1
        )
        manager._wait_for_completion()

        assert started is EnrichmentStart.STARTED
        assert manager.get_status().total_items == 1
        assert queued_ids(storage_manager) == {untouched_id}

    def test_a_pin_taken_behind_a_claimed_job_leaves_the_item_queued(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(self._movie())
        manager = self._manager(storage_manager)
        storage_manager.enrichment_jobs.claim(None)

        _pins, started = manager.pin(
            db_id, storage_manager.get_content_item(db_id), "mock", "603", user_id=1
        )

        assert started is EnrichmentStart.ALREADY_RUNNING
        assert queued_ids(storage_manager) == {db_id}

    def test_a_pin_with_enrichment_switched_off_leaves_the_item_queued(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(self._movie())
        manager = self._manager(storage_manager, enabled=False)

        _pins, started = manager.pin(
            db_id, storage_manager.get_content_item(db_id), "mock", "603", user_id=1
        )
        manager._wait_for_completion()

        assert started is EnrichmentStart.UNAVAILABLE
        assert queued_ids(storage_manager) == {db_id}

    def test_a_pin_with_no_provider_for_the_type_leaves_the_item_queued(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(self._movie())
        manager = self._manager(storage_manager, serves=[ContentType.BOOK])

        _pins, started = manager.pin(
            db_id, storage_manager.get_content_item(db_id), "mock", "603", user_id=1
        )
        manager._wait_for_completion()

        assert started is EnrichmentStart.UNAVAILABLE
        assert queued_ids(storage_manager) == {db_id}

    def test_an_automatic_match_leaves_the_item_unpinned_so_a_rename_rematches(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(self._movie())
        manager = self._manager(storage_manager)

        manager.start_enrichment()
        manager._wait_for_completion()

        stored = storage_manager.get_content_item(db_id)
        assert stored.metadata.get("enrichment_ids", {}) == {}

    def test_candidates_search_under_the_title_the_operator_gave(
        self, storage_manager: StorageManager
    ) -> None:
        item = self._movie()
        manager = self._manager(storage_manager)
        offered = [
            Candidate(
                record_id="9", title="Prey", year=2022, creator="Dan Trachtenberg"
            )
        ]

        with patch.object(MockProvider, "search", return_value=offered) as mock_search:
            found = manager.candidates(item, "Predator 5")

        assert found == [("mock", offered[0])]
        assert mock_search.call_args.args[0].title == "Predator 5"

    def test_one_provider_refusing_does_not_empty_the_picker(
        self, storage_manager: StorageManager
    ) -> None:
        manager = self._manager(storage_manager)
        manager.registry.register(MockProvider(name="other"))
        manager.config["enrichment"]["providers"]["other"] = {"enabled": True}
        offered = [Candidate(record_id="9", title="Prey")]

        with patch.object(
            MockProvider,
            "search",
            side_effect=[ProviderError("mock", "HTTP 401"), offered],
        ):
            found = manager.candidates(self._movie())

        assert found == [("other", offered[0])]

    @pytest.mark.parametrize(
        ("provider_name", "record_id"),
        [("themoviedb", "603"), ("tmdb", "tt0468569")],
        ids=["a-name-no-provider-answers-to", "an-id-the-provider-cannot-look-up"],
    )
    def test_a_pin_no_run_could_read_back_is_refused_rather_than_stored(
        self,
        storage_manager: StorageManager,
        provider_name: str,
        record_id: str,
    ) -> None:
        db_id = storage_manager.save_content_item(self._movie())
        manager = self._manager(storage_manager)
        manager.registry.register(TMDBProvider())
        item = storage_manager.get_content_item(db_id)

        with pytest.raises(PinRefused):
            manager.pin(db_id, item, provider_name, record_id, user_id=1)

        assert "enrichment_ids" not in storage_manager.get_content_item(db_id).metadata

    def test_a_provider_that_never_reads_a_pin_is_refused_and_left_unlisted(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(self._movie())
        manager = self._manager(storage_manager)
        manager.registry.register(WikidataProvider())

        with pytest.raises(PinRefused) as refused:
            manager.pin(
                db_id,
                storage_manager.get_content_item(db_id),
                "wikidata",
                "Q1128199",
                user_id=1,
            )

        assert "pin one of mock." in str(refused.value)
        assert "enrichment_ids" not in storage_manager.get_content_item(db_id).metadata

    def test_a_provider_named_in_another_case_pins_under_the_name_runs_read(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(self._movie())
        manager = self._manager(storage_manager)

        manager.pin(
            db_id, storage_manager.get_content_item(db_id), "MOCK", "tt1", user_id=1
        )
        manager._wait_for_completion()

        stored = storage_manager.get_content_item(db_id)
        assert stored.metadata["enrichment_ids"] == {"mock": "tt1"}


class TestEnrichmentWritesTheRowItWasHandedRegression:
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
        storage_manager.enrichment.mark_complete(older, "tmdb", "high")
        registry = EnrichmentRegistry()
        registry._discovered = True
        registry.register(MockProvider())

        manager = EnrichmentManager(
            storage_manager,
            {"enrichment": {"enabled": True, "providers": {"mock": {"enabled": True}}}},
            registry,
        )
        manager.start_enrichment()
        manager._wait_for_completion()

        assert "genres" in storage_manager.get_content_item(newer).metadata
        assert "genres" not in storage_manager.get_content_item(older).metadata


class OrdinalOnlyProvider(EnrichmentProvider):
    def __init__(self) -> None:
        self.ordinal_calls: list[ContentItem] = []

    @property
    def name(self) -> str:
        return "ordinal"

    @property
    def display_name(self) -> str:
        return "Ordinal Only Provider"

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

    def fetch_series_ordinal(
        self, item: ContentItem, config: dict[str, Any]
    ) -> SeriesOrdinal | None:
        self.ordinal_calls.append(item)
        return SeriesOrdinal(position=3.0, series_name="The Matrix")


class TestTheOrdinalPass:
    def _run(self, storage_manager: StorageManager) -> OrdinalOnlyProvider:
        # Beside a matcher that finds nothing: a run with no matcher is refused.
        provider = OrdinalOnlyProvider()
        manager = manager_over(
            storage_manager, MockProvider(should_not_find=True), provider
        )
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()
        return provider

    def test_a_provider_stating_only_an_ordinal_is_never_the_items_match(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        db_id = save_movie(storage_manager)

        self._run(storage_manager)

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_provider"] == "none"
        item = storage_manager.get_content_item(db_id)
        assert item.metadata["series_position"] == 3.0
        assert item.metadata["series_position_authority"] == "authored"

    def test_an_ordinal_a_title_marker_stated_is_asked_about_and_replaced(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        db_id = save_movie(
            storage_manager,
            metadata={"series_position": 9, "series_position_authority": "stated"},
        )

        provider = self._run(storage_manager)

        assert [item.db_id for item in provider.ordinal_calls] == [db_id]
        item = storage_manager.get_content_item(db_id)
        assert item.metadata["series_position"] == 3.0
        assert item.metadata["series_position_authority"] == "authored"

    def test_a_run_with_nothing_but_an_ordinal_provider_never_starts(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        db_id = save_movie(storage_manager)
        manager = manager_over(storage_manager, OrdinalOnlyProvider())

        started = manager.start_enrichment(content_type=ContentType.MOVIE)

        assert started is EnrichmentStart.UNAVAILABLE
        assert queued_ids(storage_manager) == {db_id}

    def test_a_matching_provider_still_owns_the_item_the_ordinal_pass_positioned(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        db_id = save_movie(storage_manager)
        manager = manager_over(storage_manager, MockProvider(), OrdinalOnlyProvider())

        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_provider"] == "mock"
        item = storage_manager.get_content_item(db_id)
        assert item.metadata["genres"] == ["Action", "Drama"]
        assert item.metadata["series_position"] == 3.0
        assert item.metadata["series_position_authority"] == "authored"

    def test_an_ordinal_the_library_stated_is_never_asked_about(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        db_id = save_movie(
            storage_manager,
            metadata={"series_position": 4, "series_position_authority": "library"},
        )

        provider = self._run(storage_manager)

        assert provider.ordinal_calls == []
        assert storage_manager.get_content_item(db_id).metadata["series_position"] == 4

    def test_the_stored_series_name_survives_the_ordinal_that_positions_it(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        db_id = save_movie(
            storage_manager, metadata={"series_name": "The Matrix Collection"}
        )

        self._run(storage_manager)

        item = storage_manager.get_content_item(db_id)
        assert item.metadata["series_name"] == "The Matrix Collection"
        assert item.metadata["series_position"] == 3.0
        assert item.metadata["series_position_authority"] == "authored"


class SubSeriesOrdinalProvider(OrdinalOnlyProvider):
    """Wikidata numbers a Star Wars film within its trilogy, not the franchise."""

    def fetch_series_ordinal(
        self, item: ContentItem, config: dict[str, Any]
    ) -> SeriesOrdinal | None:
        self.ordinal_calls.append(item)
        return SeriesOrdinal(position=1.0, series_name="Star Wars: Original Trilogy")


class TestAnOrdinalCountedInAnotherSeries:
    def test_a_sub_series_position_is_never_filed_under_the_stored_collection(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        db_id = save_movie(
            storage_manager,
            "A New Hope",
            metadata={"series_name": "Star Wars Collection"},
        )
        manager = manager_over(
            storage_manager,
            MockProvider(should_not_find=True),
            SubSeriesOrdinalProvider(),
        )

        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        item = storage_manager.get_content_item(db_id)
        assert item.metadata["series_name"] == "Star Wars Collection"
        assert "series_position" not in item.metadata


class CollectionNamingProvider(MockProvider):
    """TMDB names the collection, for every film of it alike."""

    def enrich(
        self, item: ContentItem, config: dict[str, Any]
    ) -> EnrichmentResult | None:
        result = super().enrich(item, config)
        assert result is not None
        result.extra_metadata = {"series_name": "The Matrix Collection"}
        return result


class UnevenOrdinalProvider(OrdinalOnlyProvider):
    """Wikidata holds a P179 for one film of a collection and not the other."""

    def fetch_series_ordinal(
        self, item: ContentItem, config: dict[str, Any]
    ) -> SeriesOrdinal | None:
        self.ordinal_calls.append(item)
        if item.title.endswith("Revolutions"):
            return None
        return SeriesOrdinal(position=2.0, series_name="The Matrix")


class TestOneCollectionEnrichedFromScratch:
    def test_the_film_an_ordinal_positioned_keeps_its_collections_name(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        films = {
            title: save_movie(storage_manager, title)
            for title in ("The Matrix Reloaded", "The Matrix Revolutions")
        }
        manager = manager_over(
            storage_manager, CollectionNamingProvider(), UnevenOrdinalProvider()
        )

        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        enriched = {
            title: storage_manager.get_content_item(db_id)
            for title, db_id in films.items()
        }
        assert {item.metadata["series_name"] for item in enriched.values()} == {
            "The Matrix Collection"
        }
        assert enriched["The Matrix Reloaded"].metadata["series_position"] == 2.0


class TestAnOrdinalIsRefusedWhereNoReaderCouldReadItBack:
    @pytest.mark.parametrize("position", [1001, -1, float("nan")])
    def test_a_position_outside_the_range_cannot_be_constructed(
        self, position: float
    ) -> None:
        with pytest.raises(ValueError, match="between 0 and 1000"):
            SeriesOrdinal(position=position, series_name="The Matrix")

    def test_zero_is_a_prequel_rather_than_a_refusal(self) -> None:
        ordinal = SeriesOrdinal(position=0, series_name="The Matrix")

        assert ordinal.as_metadata()["series_position"] == 0

    def test_a_position_naming_no_series_cannot_be_constructed(self) -> None:
        with pytest.raises(ValueError, match="must name the series"):
            SeriesOrdinal(position=3.0, series_name="  ")


class UnreliableOrdinalProvider(OrdinalOnlyProvider):
    """404s on every other item, as a merged or redirected entity id does."""

    def fetch_series_ordinal(
        self, item: ContentItem, config: dict[str, Any]
    ) -> SeriesOrdinal | None:
        self.ordinal_calls.append(item)
        if len(self.ordinal_calls) % 2 == 0:
            raise ProviderError(self.name, "HTTP 404") from http_error(404)
        return SeriesOrdinal(position=3.0, series_name="The Matrix")


class TestTheOrdinalPassCountsRejectionsInARow:
    def test_a_provider_that_keeps_answering_is_never_abandoned_regression(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        movies = [save_movie(storage_manager, f"Movie {index}") for index in range(20)]
        provider = UnreliableOrdinalProvider()
        manager = manager_over(
            storage_manager, MockProvider(should_not_find=True), provider
        )

        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        assert len(provider.ordinal_calls) == len(movies)
        assert not any("abandoned" in error for error in manager.get_status().errors)


class TestAnOrdinalProviderBesideAnAbandonedMatcher:
    def test_the_items_no_surviving_matcher_reached_stay_queued_regression(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        db_ids = [save_movie(storage_manager, f"Movie {index}") for index in range(10)]
        manager = manager_over(
            storage_manager,
            RawRequestErrorProvider(http_error(401)),
            OrdinalOnlyProvider(),
        )

        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        assert queued_ids(storage_manager) == set(db_ids[_MAX_CONSECUTIVE_REJECTIONS:])
        assert not manager.get_status().completed


class FailingOrdinalProvider(OrdinalOnlyProvider):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def fetch_series_ordinal(
        self, item: ContentItem, config: dict[str, Any]
    ) -> SeriesOrdinal | None:
        self.ordinal_calls.append(item)
        raise self._error


class TestAnUnansweredOrdinalPass:
    def test_a_timed_out_ordinal_keeps_the_matched_item_settled_regression(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        db_id = save_movie(storage_manager)
        ordinal = FailingOrdinalProvider(requests.Timeout("timed out"))
        manager = manager_over(storage_manager, MockProvider(), ordinal)

        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        assert queued_ids(storage_manager) == set()
        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_provider"] == "mock"
        assert status["enrichment_quality"] == "high"

    @pytest.mark.parametrize(
        ("error", "queues"),
        [(requests.Timeout("timed out"), True), (http_error(401), False)],
    )
    def test_only_a_retryable_ordinal_failure_requeues_an_unmatched_item_regression(
        self, tmp_path: Path, error: Exception, queues: bool
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        titles = [f"Movie {index}" for index in range(_MAX_CONSECUTIVE_REJECTIONS + 2)]
        db_ids = {save_movie(storage_manager, title) for title in titles}
        ordinal = FailingOrdinalProvider(error)
        manager = manager_over(
            storage_manager, MockProvider(should_not_find=True), ordinal
        )

        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        assert queued_ids(storage_manager) == (db_ids if queues else set())


class TestAFailedOrdinalSave:
    def test_a_locked_database_settles_the_item_rather_than_ending_the_run(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        db_ids = [save_movie(storage_manager, f"Movie {index}") for index in range(2)]
        manager = manager_over(
            storage_manager, MockProvider(should_not_find=True), OrdinalOnlyProvider()
        )

        with patch.object(
            storage_manager,
            "save_enrichment_metadata",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            manager.start_enrichment(content_type=ContentType.MOVIE)
            assert manager._wait_for_completion()

        job_status = manager.get_status()
        assert job_status.completed
        assert job_status.items_processed == len(db_ids)
        assert job_status.items_failed == len(db_ids)
        assert queued_ids(storage_manager) == set()

    def test_the_matched_providers_attribution_survives_it_regression(
        self, tmp_path: Path
    ) -> None:
        storage_manager = StorageManager(sqlite_path=tmp_path / "test.db")
        db_id = save_movie(storage_manager)
        manager = manager_over(storage_manager, MockProvider(), OrdinalOnlyProvider())

        with patch.object(
            storage_manager,
            "save_enrichment_metadata",
            side_effect=[None, sqlite3.OperationalError("database is locked")],
        ):
            manager.start_enrichment(content_type=ContentType.MOVIE)
            assert manager._wait_for_completion()

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_provider"] == "mock"
        assert status["enrichment_quality"] == "high"


class TestARunReachesTMDBThroughTheGlobalRegistry:
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
                "enabled": True,
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
        db_id = save_movie(storage_manager)
        payloads = {
            self._tmdb_url("search/movie"): {
                "results": [{"id": 603, "title": "The Matrix"}]
            },
            self._tmdb_url("movie/603"): {
                "id": 603,
                "overview": "A hacker learns the true nature of reality.",
                "genres": [{"name": "Action"}, {"name": "Science Fiction"}],
                "runtime": 136,
                "release_date": "1999-03-30",
                "poster_path": "/matrix.jpg",
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
        assert enriched.cover_url == "https://image.tmdb.org/t/p/w500/matrix.jpg"
        assert manager.get_status().items_enriched == 1
        assert queued_ids(storage_manager) == set()
