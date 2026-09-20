from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.enrichment.manager import EnrichmentManager, EnrichmentStart, PinRefused
from src.enrichment.provider_base import ProviderError
from src.enrichment.providers.hardcover.hardcover import HardcoverProvider
from src.enrichment.registry import EnrichmentRegistry, get_enrichment_registry
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.enrichment_status import ResetCounts
from src.storage.field_writes import FieldWriter, WriterBand
from src.storage.manager import StorageManager
from src.utils.matching import Candidate
from tests.enrichment.test_enrichment_manager import (
    WrappedRequestErrorProvider,
    http_error,
    save_movie,
)
from tests.factories import authenticated_client, booted_web_app, make_storage_mock


@pytest.fixture
def mock_config() -> dict:
    return {
        "enrichment": {
            "enabled": True,
            "batch_size": 50,
            "providers": {
                "tmdb": {"enabled": True, "api_key": "test-key"},
            },
        },
    }


@pytest.fixture
def mock_config_disabled() -> dict:
    return {
        "enrichment": {
            "enabled": False,
        },
    }


@contextmanager
def _client(storage: MagicMock, config: dict) -> Iterator[TestClient]:
    with booted_web_app(storage, config) as app:
        yield authenticated_client(app)


class TestEnrichmentStart:
    @pytest.mark.parametrize(
        ("body", "expected_kwargs", "expected_message"),
        [
            (
                {},
                {"content_type": None, "user_id": 1, "include_not_found": False},
                "Started enrichment for all types",
            ),
            (
                {"content_type": "movie", "retry_not_found": True, "user_id": 3},
                {
                    "content_type": ContentType.MOVIE,
                    "user_id": 3,
                    "include_not_found": True,
                },
                "Started enrichment for movie (retrying not_found)",
            ),
        ],
        ids=["defaults", "narrowed_and_retrying"],
    )
    def test_start_runs_the_job_the_request_asked_for(
        self,
        mock_config: dict,
        body: dict,
        expected_kwargs: dict,
        expected_message: str,
    ) -> None:
        with (
            _client(make_storage_mock(), mock_config) as client,
            patch("src.web.api._enrichment.EnrichmentManager") as mock_manager_cls,
        ):
            mock_manager = MagicMock(spec=EnrichmentManager)
            mock_manager.start_enrichment.return_value = EnrichmentStart.STARTED
            mock_manager_cls.return_value = mock_manager

            response = client.post("/api/enrichment/start", json=body)

        assert mock_manager.start_enrichment.call_args.kwargs == expected_kwargs
        assert response.status_code == 200
        assert response.json() == {"message": expected_message, "status": "started"}

    def test_start_is_refused_while_a_job_is_already_claimed(
        self, mock_config: dict, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        assert storage.enrichment_jobs.claim(None) is True

        with _client(storage, mock_config) as client:
            response = client.post("/api/enrichment/start", json={})

        assert response.status_code == 409
        assert response.json()["detail"] == "Enrichment job already running"

    def test_disabled_enrichment_names_the_surface_that_turns_it_on(
        self, mock_config_disabled: dict
    ) -> None:
        with _client(make_storage_mock(), mock_config_disabled) as client:
            response = client.post("/api/enrichment/start", json={})

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "config.yaml" not in detail
        assert "Data tab" in detail
        assert "settings set enrichment.enabled true" in detail

    def test_start_enrichment_invalid_content_type(self, mock_config: dict) -> None:
        with _client(make_storage_mock(), mock_config) as client:
            response = client.post(
                "/api/enrichment/start",
                json={"content_type": "invalid"},
            )

        assert response.status_code == 400
        assert "invalid" in response.json()["detail"].lower()


class TestEnrichmentStop:
    def test_stop_with_no_job_running(self, tmp_path: Path) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")

        with _client(storage, {}) as client:
            response = client.post("/api/enrichment/stop")

        assert response.status_code == 400
        assert response.json()["detail"] == "No enrichment job is running."

    def test_stop_asks_the_claimed_job_to_stop(self, tmp_path: Path) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        assert storage.enrichment_jobs.claim(None) is True

        with _client(storage, {}) as client:
            response = client.post("/api/enrichment/stop")

        assert response.status_code == 200
        assert response.json() == {
            "message": "Enrichment job stop requested",
            "status": "stopping",
        }
        assert storage.enrichment_jobs.stop_requested() is True


class TestEnrichmentStatus:
    def test_get_status_no_job(self, tmp_path: Path) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        with _client(storage, {}) as client:
            response = client.get("/api/enrichment/status")

        assert response.status_code == 200
        data = response.json()
        assert data["running"] is False

    def test_a_wrapped_provider_error_reaches_the_wire_derived(
        self, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        save_movie(storage)
        registry = EnrichmentRegistry()
        registry._discovered = True
        registry.register(
            WrappedRequestErrorProvider(
                http_error(401), message="GET ?api_key=SECRET_KEY_123 failed"
            )
        )
        config = {
            "enrichment": {
                "enabled": True,
                "batch_size": 10,
                "providers": {"wrapped_request": {"enabled": True}},
            }
        }
        manager = EnrichmentManager(storage, config, registry)
        manager.start_enrichment(content_type=ContentType.MOVIE)
        assert manager._wait_for_completion()

        with _client(storage, config) as client:
            response = client.get("/api/enrichment/status")

        assert response.status_code == 200
        assert response.json()["errors"] == ["wrapped_request: HTTP 401"]
        assert "SECRET_KEY_123" not in response.text


class TestEnrichmentStats:
    def test_stats_count_the_library_and_name_every_installed_provider(self) -> None:
        storage = make_storage_mock()
        stats = {
            "total": 100,
            "resettable": 88,
            "legacy_items": 7,
            "enriched": 80,
            "pending": 15,
            "not_found": 3,
            "failed": 2,
            "resettable_by_provider": {"tmdb": 320, "openlibrary": 95},
            "legacy_by_provider": {"tmdb": 5, "openlibrary": 2},
            "by_provider": {"tmdb": 50, "openlibrary": 30},
            "by_quality": {"high": 60, "medium": 20},
        }
        storage.enrichment.stats.return_value = stats

        with _client(storage, {}) as client:
            response = client.get("/api/enrichment/stats")

        assert response.status_code == 200
        # The reset control narrows to these, so a list written out here rather
        # than discovered is one that goes stale as providers ship.
        assert response.json() == {
            "enabled": False,
            **stats,
            "providers": [
                {"name": name, "display_name": provider.display_name}
                for name, provider in sorted(
                    get_enrichment_registry().get_all_providers().items()
                )
            ],
        }


class TestEnrichmentPinning:
    @staticmethod
    def _storage() -> MagicMock:
        storage = make_storage_mock()
        storage.get_content_item.return_value = ContentItem(
            db_id=7,
            title="Prey",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"enrichment_ids": {"rawg": "3328"}},
        )
        return storage

    def test_candidates_carry_what_tells_two_records_apart(self) -> None:
        manager = MagicMock(spec=EnrichmentManager)
        manager.candidates.return_value = [
            ("rawg", Candidate(record_id="41494", title="Prey", year=2017))
        ]

        with (
            patch("src.web.api._enrichment.EnrichmentManager", return_value=manager),
            _client(self._storage(), {}) as client,
        ):
            response = client.get(
                "/api/enrichment/candidates",
                params={"item_id": 7, "query": "Prey 2017"},
            )

        assert response.status_code == 200
        assert response.json() == {
            "item_id": 7,
            "candidates": [
                {
                    "provider": "rawg",
                    "record_id": "41494",
                    "title": "Prey",
                    "year": 2017,
                    "creator": None,
                    "cover_url": None,
                }
            ],
            "pinned": {"rawg": "3328"},
        }
        assert manager.candidates.call_args.args[1] == "Prey 2017"

    def test_candidates_come_from_a_provider_that_searches_without_enriching(
        self,
    ) -> None:
        storage = make_storage_mock()
        storage.get_content_item.return_value = ContentItem(
            db_id=7,
            title="Dune",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )
        config = {
            "enrichment": {
                "enabled": True,
                "providers": {"hardcover": {"enabled": True}},
            }
        }

        with (
            patch.object(
                HardcoverProvider,
                "search",
                return_value=[Candidate(record_id="1234", title="Dune")],
            ),
            _client(storage, config) as client,
        ):
            response = client.get("/api/enrichment/candidates", params={"item_id": 7})

        assert response.status_code == 200
        assert response.json()["candidates"][0]["record_id"] == "1234"

    def test_a_link_its_own_provider_could_not_read_is_reported_not_left_empty(
        self,
    ) -> None:
        manager = MagicMock(spec=EnrichmentManager)
        manager.candidates.side_effect = ProviderError("rawg", "HTTP 401")

        with (
            patch("src.web.api._enrichment.EnrichmentManager", return_value=manager),
            _client(self._storage(), {}) as client,
        ):
            response = client.get(
                "/api/enrichment/candidates",
                params={"item_id": 7, "query": "https://rawg.io/games/prey"},
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "rawg: HTTP 401"

    def test_candidates_reach_no_provider_with_enrichment_switched_off(self) -> None:
        config = {
            "enrichment": {"enabled": False, "providers": {"rawg": {"enabled": True}}}
        }

        with _client(self._storage(), config) as client:
            response = client.get("/api/enrichment/candidates", params={"item_id": 7})

        assert response.status_code == 400
        assert "settings set enrichment.enabled true" in response.json()["detail"]

    @pytest.mark.parametrize(
        ("record", "stored"),
        [("41494", {"rawg": "41494"}), (None, {})],
        ids=["binds", "clears"],
    )
    def test_pin_binds_the_item_to_the_record_the_body_names_or_to_none(
        self, record: str | None, stored: dict[str, str]
    ) -> None:
        manager = MagicMock(spec=EnrichmentManager)
        manager.pin.return_value = (
            stored,
            EnrichmentStart.STARTED if record else None,
        )

        with (
            patch("src.web.api._enrichment.EnrichmentManager", return_value=manager),
            _client(self._storage(), {}) as client,
        ):
            response = client.post(
                "/api/enrichment/pin",
                json={"item_id": 7, "provider": "rawg", "record_id": record},
            )

        assert response.status_code == 200
        assert response.json()["pinned"] == stored
        assert manager.pin.call_args.args[2:] == ("rawg", record)

    @pytest.mark.parametrize(
        ("started", "clause"),
        [
            (EnrichmentStart.STARTED, "Enriching it now."),
            (
                EnrichmentStart.ALREADY_RUNNING,
                "Queued for the next enrichment run.",
            ),
            (
                EnrichmentStart.UNAVAILABLE,
                "Queued: enrichment is off, or no enabled provider covers this type.",
            ),
        ],
        ids=["claimed", "claim-lost", "nobody-to-ask"],
    )
    def test_a_pin_says_whether_the_run_it_asked_for_started(
        self, started: EnrichmentStart, clause: str
    ) -> None:
        manager = MagicMock(spec=EnrichmentManager)
        manager.pin.return_value = ({"rawg": "41494"}, started)

        with (
            patch("src.web.api._enrichment.EnrichmentManager", return_value=manager),
            _client(self._storage(), {}) as client,
        ):
            response = client.post(
                "/api/enrichment/pin",
                json={"item_id": 7, "provider": "rawg", "record_id": "41494"},
            )

        assert response.json()["message"] == (
            f"Item 7 now enriches from rawg record 41494. {clause}"
        )
        assert response.json()["run"] == started.value

    def test_a_pin_the_manager_refuses_is_a_400_naming_what_is_valid(self) -> None:
        manager = MagicMock(spec=EnrichmentManager)
        manager.pin.side_effect = PinRefused("pin one of rawg, tmdb.")

        with (
            patch("src.web.api._enrichment.EnrichmentManager", return_value=manager),
            _client(self._storage(), {}) as client,
        ):
            response = client.post(
                "/api/enrichment/pin",
                json={"item_id": 7, "provider": "rawgg", "record_id": "1"},
            )

        assert response.status_code == 400
        assert response.json()["detail"] == "pin one of rawg, tmdb."

    def test_a_failure_that_is_not_the_authored_refusal_is_never_echoed(self) -> None:
        manager = MagicMock(spec=EnrichmentManager)
        manager.pin.side_effect = ValueError("no such column: enrichment_ids")

        with (
            patch("src.web.api._enrichment.EnrichmentManager", return_value=manager),
            _client(self._storage(), {}) as client,
            pytest.raises(ValueError),
        ):
            client.post(
                "/api/enrichment/pin",
                json={"item_id": 7, "provider": "rawg", "record_id": "1"},
            )

    def test_pinning_an_item_that_is_not_there_is_a_404(self) -> None:
        storage = make_storage_mock()
        storage.get_content_item.return_value = None

        with _client(storage, {}) as client:
            response = client.post(
                "/api/enrichment/pin",
                json={"item_id": 7, "provider": "rawg", "record_id": "1"},
            )

        assert response.status_code == 404


class TestEnrichmentRequeue:
    def test_requeue_enriches_the_item_again_with_every_ledger_row_standing(
        self, tmp_path: Path
    ) -> None:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        db_id = save_movie(storage)
        item = storage.get_content_item(db_id)
        assert item is not None
        storage.save_enrichment_metadata(
            db_id, item, FieldWriter(WriterBand.PROVIDER, "tmdb"), {"runtime": 136}
        )
        storage.enrichment.mark_complete(db_id, "tmdb", "high")

        with _client(storage, {}) as client:
            response = client.post("/api/enrichment/requeue", json={"item_id": db_id})

        assert response.status_code == 200
        assert response.json()["item_id"] == db_id
        rebuilt = storage.get_content_item(db_id)
        assert rebuilt is not None and rebuilt.metadata["runtime"] == 136
        status = storage.enrichment.status(db_id)
        assert status is not None and status["needs_enrichment"] is True

    def test_requeue_reports_an_item_that_is_not_there(self) -> None:
        storage = make_storage_mock()
        storage.get_content_item.return_value = None

        with _client(storage, {}) as client:
            response = client.post("/api/enrichment/requeue", json={"item_id": 999})

        assert response.status_code == 404
        storage.enrichment.requeue.assert_not_called()


class TestEnrichmentReset:
    def test_reset_all(self) -> None:
        storage = make_storage_mock()
        storage.enrichment.reset.return_value = ResetCounts(requeued=50, stripped=12)

        with _client(storage, {}) as client:
            response = client.post("/api/enrichment/reset", json={})

        assert response.status_code == 200
        data = response.json()
        assert (data["requeued"], data["dropped"]) == (50, 12)
        assert "for 12 item(s) and re-queued 50 item(s)" in data["message"]

    def test_reset_one_item_narrows_the_reset_to_it_and_enriches_it(self) -> None:
        storage = make_storage_mock()
        storage.enrichment.reset.return_value = ResetCounts(requeued=1, stripped=1)
        manager = MagicMock(spec=EnrichmentManager)
        manager.start_enrichment.return_value = EnrichmentStart.STARTED

        with (
            patch("src.web.api._enrichment.EnrichmentManager", return_value=manager),
            _client(storage, {}) as client,
        ):
            response = client.post("/api/enrichment/reset", json={"item_id": 7})

        assert response.status_code == 200
        assert response.json() == {
            "message": (
                "Dropped what the providers stated for 1 item(s) and re-queued"
                " 1 item(s). Enriching it now."
            ),
            "dropped": 1,
            "requeued": 1,
            "run": "started",
        }
        assert storage.enrichment.reset.call_args[1]["content_item_id"] == 7
        assert manager.start_enrichment.call_args.kwargs["content_item_id"] == 7

    @pytest.mark.parametrize("provider", ["all", ""])
    def test_reset_reads_an_unfiltered_provider_as_no_filter_beside_an_item_regression(
        self, provider: str
    ) -> None:
        storage = make_storage_mock()
        storage.enrichment.reset.return_value = ResetCounts(requeued=1, stripped=1)
        manager = MagicMock(spec=EnrichmentManager)
        manager.start_enrichment.return_value = EnrichmentStart.STARTED

        with (
            patch("src.web.api._enrichment.EnrichmentManager", return_value=manager),
            _client(storage, {}) as client,
        ):
            response = client.post(
                "/api/enrichment/reset", json={"item_id": 7, "provider": provider}
            )

        assert response.status_code == 200
        assert storage.enrichment.reset.call_args.kwargs["provider"] is None
        assert manager.start_enrichment.call_args.kwargs["content_item_id"] == 7

    def test_a_hard_reset_drops_the_unclaimed_values_and_starts_no_run(self) -> None:
        storage = make_storage_mock()
        storage.enrichment.reset.return_value = ResetCounts(requeued=50, stripped=37)
        manager = MagicMock(spec=EnrichmentManager)

        with (
            patch("src.web.api._enrichment.EnrichmentManager", return_value=manager),
            _client(storage, {}) as client,
        ):
            response = client.post("/api/enrichment/reset", json={"hard": True})

        assert response.status_code == 200
        assert response.json()["message"] == (
            "Dropped what the providers stated and the values no writer claimed"
            " for 37 item(s) and re-queued 50 item(s)"
        )
        assert storage.enrichment.reset.call_args.kwargs["hard"] is True
        manager.start_enrichment.assert_not_called()

    def test_reset_refuses_an_item_id_beside_a_filter(self) -> None:
        storage = make_storage_mock()

        with _client(storage, {}) as client:
            response = client.post(
                "/api/enrichment/reset", json={"item_id": 7, "provider": "tmdb"}
            )

        assert response.status_code == 400
        assert "cannot be combined" in response.json()["detail"]
        storage.enrichment.reset.assert_not_called()

    def test_reset_reports_an_item_that_is_not_there(self) -> None:
        storage = make_storage_mock()
        storage.get_content_item.return_value = None

        with _client(storage, {}) as client:
            response = client.post("/api/enrichment/reset", json={"item_id": 999})

        assert response.status_code == 404
        storage.enrichment.reset.assert_not_called()

    def test_reset_refuses_a_provider_nothing_installed_and_names_what_is(self) -> None:
        storage = make_storage_mock()

        with _client(storage, {}) as client:
            response = client.post(
                "/api/enrichment/reset", json={"provider": "nosuchprovider"}
            )

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "Unknown provider 'nosuchprovider'" in detail
        for name in get_enrichment_registry().get_all_providers():
            assert name in detail
        storage.enrichment.reset.assert_not_called()

    def test_reset_invalid_content_type(self) -> None:
        with _client(make_storage_mock(), {}) as client:
            response = client.post(
                "/api/enrichment/reset",
                json={"content_type": "invalid"},
            )

        assert response.status_code == 400
        assert "invalid" in response.json()["detail"].lower()
