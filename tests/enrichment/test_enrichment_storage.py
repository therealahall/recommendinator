import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.enrichment.manager import merge_enrichment
from src.enrichment.provider_base import EnrichmentResult, pins_of
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.recommendations.content_length import (
    LengthPreference,
    classify_length,
    score_length_match,
)
from src.storage.derived import write_derived_columns
from src.storage.field_writes import (
    LEGACY_WRITER,
    FieldWriter,
    WriterBand,
    read_field_writes,
)
from src.storage.item_merges import MergeEvidence
from src.storage.manager import StorageManager
from src.storage.schema import create_user


def save_unenriched(
    storage_manager: StorageManager,
    item_id: str,
    content_type: ContentType = ContentType.MOVIE,
    user_id: int | None = None,
    source: str | None = None,
) -> int:
    return storage_manager.save_content_item(
        ContentItem(
            id=item_id,
            title=item_id,
            content_type=content_type,
            status=ConsumptionStatus.UNREAD,
            source=source,
        ),
        user_id=user_id,
    )


def library_holds(storage_manager: StorageManager, db_id: int, **stated: Any) -> None:
    item = storage_manager.get_content_item(db_id)
    assert item is not None
    storage_manager.save_enrichment_metadata(db_id, item, LEGACY_WRITER, stated)


def provider_enriches(
    storage_manager: StorageManager, db_id: int, provider: str, **stated: Any
) -> None:
    item = storage_manager.get_content_item(db_id)
    assert item is not None
    storage_manager.save_enrichment_metadata(
        db_id, item, FieldWriter(WriterBand.PROVIDER, provider), stated
    )


def field_writers(
    storage_manager: StorageManager, db_id: int
) -> set[tuple[WriterBand, str, str]]:
    with storage_manager.connection() as conn:
        return {
            (write.writer_kind, write.writer, write.field)
            for write in read_field_writes(conn.cursor(), db_id)
        }


class TestEnrichmentStatusMethods:
    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    @pytest.fixture
    def sample_item(self) -> ContentItem:
        return ContentItem(
            id="test123",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={"genres": ["Action"]},
        )

    def test_mark_enrichment_complete(
        self, storage_manager: StorageManager, sample_item: ContentItem
    ) -> None:
        db_id = storage_manager.save_content_item(sample_item)

        storage_manager.enrichment.mark_complete(db_id, "tmdb", "high")

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_provider"] == "tmdb"
        assert status["enrichment_quality"] == "high"
        assert status["needs_enrichment"] is False
        assert status["enrichment_error"] is None

    def test_mark_enrichment_failed(
        self, storage_manager: StorageManager, sample_item: ContentItem
    ) -> None:
        db_id = storage_manager.save_content_item(sample_item)

        storage_manager.enrichment.mark_failed(db_id, "API rate limit exceeded")

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_error"] == "API rate limit exceeded"
        assert status["needs_enrichment"] is True
        queued = storage_manager.enrichment.items_needing()
        assert [db for db, _item in queued] == [db_id]

    def test_reset_enrichment_status_by_provider(
        self, storage_manager: StorageManager
    ) -> None:
        item1 = ContentItem(
            id="movie1",
            title="Movie 1",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        item2 = ContentItem(
            id="movie2",
            title="Movie 2",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )

        db_id1 = storage_manager.save_content_item(item1)
        db_id2 = storage_manager.save_content_item(item2)

        storage_manager.enrichment.mark_complete(db_id1, "tmdb", "high")
        storage_manager.enrichment.mark_complete(db_id2, "other", "high")

        counts = storage_manager.enrichment.reset(provider="tmdb")

        assert counts.requeued == 1
        assert storage_manager.enrichment.status(db_id1)["needs_enrichment"] is True
        assert storage_manager.enrichment.status(db_id2)["needs_enrichment"] is False

    def test_reset_drops_the_settled_miss_that_retry_not_found_reads(
        self, storage_manager: StorageManager, sample_item: ContentItem
    ) -> None:
        db_id = storage_manager.save_content_item(sample_item)
        storage_manager.enrichment.mark_complete(db_id, "none", "not_found")

        storage_manager.enrichment.reset(content_item_id=db_id)

        status = storage_manager.enrichment.status(db_id)
        assert status is not None
        assert status["enrichment_quality"] is None
        assert status["needs_enrichment"] is True

    def test_reset_drops_what_the_provider_stated_and_rebuilds_on_what_is_left(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Prey",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                source="radarr",
                metadata={"release_year": 1984},
            )
        )
        provider_enriches(
            storage_manager, db_id, "tmdb", release_year=1985, runtime=137
        )
        storage_manager.enrichment.mark_complete(db_id, "tmdb", "high")

        assert storage_manager.enrichment.reset().stripped == 1

        rebuilt = storage_manager.get_content_item(db_id)
        assert rebuilt is not None
        assert rebuilt.metadata["release_year"] == 1984
        assert rebuilt.metadata.get("runtime") is None
        assert field_writers(storage_manager, db_id) == {
            (WriterBand.SOURCE, "radarr", "title"),
            (WriterBand.SOURCE, "radarr", "release_year"),
        }

    def test_reset_by_provider_reaches_an_item_another_provider_was_credited_with(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = save_unenriched(storage_manager, "item7")
        provider_enriches(storage_manager, db_id, "tmdb", genres=["Action"])
        storage_manager.enrichment.mark_complete(db_id, "hardcover", "high")

        assert storage_manager.enrichment.reset(provider="tmdb").stripped == 1

        assert (WriterBand.PROVIDER, "tmdb", "genres") not in field_writers(
            storage_manager, db_id
        )
        status = storage_manager.enrichment.status(db_id)
        assert status is not None and status["needs_enrichment"] is True

    def test_a_reset_commits_each_item_before_it_walks_to_the_next(
        self, storage_manager: StorageManager
    ) -> None:
        first = save_unenriched(storage_manager, "item1")
        second = save_unenriched(storage_manager, "item2")
        provider_enriches(storage_manager, first, "tmdb", runtime=137)
        provider_enriches(storage_manager, second, "tmdb", runtime=140)
        committed = write_derived_columns
        seen: list[int] = []

        def peek(cursor: sqlite3.Cursor, db_id: int) -> None:
            if db_id == second:
                reader = sqlite3.connect(storage_manager.sqlite_db.db_path)
                try:
                    seen.append(
                        reader.execute(
                            "SELECT COUNT(*) FROM content_item_field_writes"
                            " WHERE content_item_id = ? AND writer = 'tmdb'",
                            (first,),
                        ).fetchone()[0]
                    )
                finally:
                    reader.close()
            committed(cursor, db_id)

        with patch("src.storage.sqlite_db.write_derived_columns", side_effect=peek):
            storage_manager.sqlite_db.reset_provider_writes([first, second], "tmdb")

        assert seen == [0]

    def test_reset_by_provider_leaves_the_field_another_provider_also_stated(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = save_unenriched(storage_manager, "item7")
        provider_enriches(storage_manager, db_id, "tmdb", runtime=137)
        provider_enriches(storage_manager, db_id, "omdb", runtime=140)

        storage_manager.enrichment.reset(provider="tmdb")

        rebuilt = storage_manager.get_content_item(db_id)
        assert rebuilt is not None
        assert rebuilt.metadata["runtime"] == 140

    def test_reset_empties_a_field_whose_only_word_left_is_an_absorbed_rows_edit(
        self, storage_manager: StorageManager
    ) -> None:
        survivor = save_unenriched(storage_manager, "item8")
        absorbed = save_unenriched(storage_manager, "item9")
        assert storage_manager.update_item_from_ui(db_id=absorbed, genres=["Co-op"])
        provider_enriches(storage_manager, survivor, "tmdb", genres=["Action"])
        storage_manager.merge_content_items(survivor, absorbed, MergeEvidence.MANUAL)

        storage_manager.enrichment.reset(content_item_id=survivor)

        rebuilt = storage_manager.get_content_item(survivor)
        assert rebuilt is not None
        assert rebuilt.metadata.get("genres") is None


class TestHardReset:
    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_a_hard_reset_of_an_all_legacy_item_leaves_its_title_standing(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = save_unenriched(storage_manager, "item10")
        library_holds(storage_manager, db_id, description="From before the ledger")

        storage_manager.enrichment.reset(content_item_id=db_id, hard=True)

        rebuilt = storage_manager.get_content_item(db_id)
        assert rebuilt is not None
        assert rebuilt.title == "item10"
        assert rebuilt.metadata.get("description") is None

    def test_a_reset_that_is_not_hard_leaves_what_the_library_itself_held(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = save_unenriched(storage_manager, "item11", source="radarr")
        library_holds(storage_manager, db_id, description="From before the ledger")

        storage_manager.enrichment.reset(content_item_id=db_id)

        rebuilt = storage_manager.get_content_item(db_id)
        assert rebuilt is not None
        assert rebuilt.metadata["description"] == "From before the ledger"

    def test_a_hard_reset_keeps_the_operators_own_word_and_the_pin(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = save_unenriched(storage_manager, "item12", source="radarr")
        library_holds(storage_manager, db_id, description="From before the ledger")
        assert storage_manager.update_item_from_ui(db_id=db_id, genres=["Co-op"])
        storage_manager.set_enrichment_pins(db_id, {"rawg": "3328"})

        storage_manager.enrichment.reset(content_item_id=db_id, hard=True)

        rebuilt = storage_manager.get_content_item(db_id)
        assert rebuilt is not None
        assert rebuilt.metadata.get("description") is None
        assert rebuilt.metadata["genres"] == ["Co-op"]
        assert pins_of(rebuilt.metadata) == {"rawg": "3328"}

    def test_a_hard_reset_keeps_the_creator_the_completion_door_recorded(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.complete_content_item(
            ContentItem(
                id=None,
                title="Piranesi",
                author="Susanna Clarke",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
            )
        )

        storage_manager.enrichment.reset(hard=True)

        rebuilt = storage_manager.get_content_item(db_id)
        assert rebuilt is not None
        assert rebuilt.author == "Susanna Clarke"

    def test_a_hard_reset_blanks_a_cover_no_writer_claimed(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = save_unenriched(storage_manager, "item15", source="radarr")
        library_holds(storage_manager, db_id, cover_url="https://covers/old.jpg")
        held = storage_manager.get_content_item(db_id)
        assert held is not None and held.cover_url == "https://covers/old.jpg"

        storage_manager.enrichment.reset(content_item_id=db_id, hard=True)

        rebuilt = storage_manager.get_content_item(db_id)
        assert rebuilt is not None
        assert rebuilt.cover_url is None

    def test_a_hard_reset_of_one_item_leaves_another_items_unclaimed_values(
        self, storage_manager: StorageManager
    ) -> None:
        named = save_unenriched(storage_manager, "item16", source="radarr")
        bystander = save_unenriched(storage_manager, "item17", source="radarr")
        library_holds(storage_manager, named, description="From before the ledger")
        library_holds(storage_manager, bystander, description="Still the library's")

        storage_manager.enrichment.reset(content_item_id=named, hard=True)

        rebuilt = storage_manager.get_content_item(bystander)
        assert rebuilt is not None
        assert rebuilt.metadata["description"] == "Still the library's"

    def test_legacy_count_counts_only_items_holding_a_value_no_writer_claimed(
        self, storage_manager: StorageManager
    ) -> None:
        held = save_unenriched(storage_manager, "item13", source="radarr")
        library_holds(storage_manager, held, description="From before the ledger")
        save_unenriched(storage_manager, "item14", source="radarr")

        assert storage_manager.enrichment.legacy_count() == 1
        assert storage_manager.enrichment.stats()["legacy_items"] == 1

        storage_manager.enrichment.reset(content_item_id=held, hard=True)

        assert storage_manager.enrichment.legacy_count() == 0

    def test_legacy_count_passes_over_an_item_whose_source_states_the_field_too(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="item18",
                title="item18",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                source="radarr",
                metadata={"description": "What radarr says"},
            )
        )
        library_holds(storage_manager, db_id, description="From before the ledger")

        assert storage_manager.enrichment.legacy_count() == 0

        storage_manager.enrichment.reset(hard=True)

        rebuilt = storage_manager.get_content_item(db_id)
        assert rebuilt is not None
        assert rebuilt.metadata["description"] == "What radarr says"

    def test_legacy_count_counts_an_item_only_when_this_reset_drops_its_other_word(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = save_unenriched(storage_manager, "item21", source="radarr")
        library_holds(storage_manager, db_id, description="From before the ledger")
        provider_enriches(storage_manager, db_id, "tmdb", description="What tmdb says")
        provider_enriches(storage_manager, db_id, "openlibrary", runtime=120)

        assert storage_manager.enrichment.legacy_count() == 1
        assert storage_manager.enrichment.legacy_count(provider="openlibrary") == 0

        storage_manager.enrichment.reset(hard=True)

        rebuilt = storage_manager.get_content_item(db_id)
        assert rebuilt is not None
        assert rebuilt.metadata.get("description") is None

    def test_legacy_count_counts_an_item_whose_only_other_word_is_a_choice(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = save_unenriched(storage_manager, "item22", source="radarr")
        library_holds(storage_manager, db_id, description="From before the ledger")
        provider_enriches(storage_manager, db_id, "tmdb", description="What tmdb says")
        assert storage_manager.set_field_choice(db_id, "description", "tmdb") is True

        assert storage_manager.enrichment.legacy_count() == 1

        storage_manager.enrichment.reset(hard=True)

        rebuilt = storage_manager.get_content_item(db_id)
        assert rebuilt is not None
        assert rebuilt.metadata.get("description") is None

    def test_a_reset_counts_what_it_stripped_apart_from_what_it_re_queued(
        self, storage_manager: StorageManager
    ) -> None:
        untracked = save_unenriched(storage_manager, "item19")
        library_holds(storage_manager, untracked, description="From before the ledger")
        tracked = save_unenriched(storage_manager, "item20", source="radarr")
        storage_manager.enrichment.mark_complete(tracked, "tmdb", "high")

        counts = storage_manager.enrichment.reset(hard=True)

        assert (counts.requeued, counts.stripped) == (1, 1)


class TestGetItemsNeedingEnrichment:
    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    def test_get_items_no_status(self, storage_manager: StorageManager) -> None:
        item = ContentItem(
            id="test1",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        storage_manager.save_content_item(item)

        items = storage_manager.enrichment.items_needing()

        assert len(items) == 1
        assert items[0][1].title == "Test Movie"

    def test_get_items_excludes_enriched(self, storage_manager: StorageManager) -> None:
        item = ContentItem(
            id="test1",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        db_id = storage_manager.save_content_item(item)
        storage_manager.enrichment.mark_complete(db_id, "tmdb", "high")

        items = storage_manager.enrichment.items_needing()

        assert len(items) == 0

    def test_get_items_by_content_type(self, storage_manager: StorageManager) -> None:
        movie = ContentItem(
            id="movie1",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
        )
        book = ContentItem(
            id="book1",
            title="Test Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.UNREAD,
        )

        storage_manager.save_content_item(movie)
        storage_manager.save_content_item(book)

        movie_items = storage_manager.enrichment.items_needing(
            content_type=ContentType.MOVIE
        )
        book_items = storage_manager.enrichment.items_needing(
            content_type=ContentType.BOOK
        )

        assert len(movie_items) == 1
        assert movie_items[0][1].title == "Test Movie"

        assert len(book_items) == 1
        assert book_items[0][1].title == "Test Book"

    def test_after_db_id_pages_forward_through_the_queue(
        self, storage_manager: StorageManager
    ) -> None:
        db_ids = [
            storage_manager.save_content_item(
                ContentItem(
                    id=f"movie{index}",
                    title=f"Movie {index}",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                )
            )
            for index in range(4)
        ]

        page = storage_manager.enrichment.items_needing(limit=2, after_db_id=db_ids[1])

        assert [db_id for db_id, _item in page] == db_ids[2:]

    def test_not_found_ids_holds_settled_misses_alone(
        self, storage_manager: StorageManager
    ) -> None:
        db_ids = [
            save_unenriched(storage_manager, f"movie{index}") for index in range(4)
        ]
        storage_manager.enrichment.mark_complete(db_ids[0], "none", "not_found")
        storage_manager.enrichment.mark_complete(db_ids[1], "tmdb", "high")
        storage_manager.enrichment.mark_failed(db_ids[2], "tmdb: HTTP 503")

        assert storage_manager.enrichment.not_found_ids() == [db_ids[0]]

    def test_not_found_ids_holds_every_miss_past_one_queue_page(
        self, storage_manager: StorageManager
    ) -> None:
        db_ids = [
            save_unenriched(storage_manager, f"movie{index}") for index in range(150)
        ]
        for db_id in db_ids:
            storage_manager.enrichment.mark_complete(db_id, "none", "not_found")

        assert sorted(storage_manager.enrichment.not_found_ids()) == db_ids

    def test_not_found_ids_filters_by_content_type_and_user(
        self, storage_manager: StorageManager
    ) -> None:
        with storage_manager.sqlite_db.connection() as conn:
            other_user = create_user(conn, username="other")
        my_movie = save_unenriched(storage_manager, "movie1", user_id=1)
        my_book = save_unenriched(
            storage_manager, "book1", content_type=ContentType.BOOK, user_id=1
        )
        their_movie = save_unenriched(storage_manager, "movie2", user_id=other_user)
        for db_id in (my_movie, my_book, their_movie):
            storage_manager.enrichment.mark_complete(db_id, "none", "not_found")

        assert set(storage_manager.enrichment.not_found_ids(user_id=1)) == {
            my_movie,
            my_book,
        }
        assert storage_manager.enrichment.not_found_ids(
            content_type=ContentType.MOVIE, user_id=1
        ) == [my_movie]
        assert storage_manager.enrichment.not_found_ids(user_id=other_user) == [
            their_movie
        ]


class TestCountItemsNeedingEnrichment:
    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    def test_count_filters_by_content_type(
        self, storage_manager: StorageManager
    ) -> None:
        storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )
        storage_manager.save_content_item(
            ContentItem(
                id="book1",
                title="Book",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )

        assert (
            storage_manager.enrichment.count_needing(content_type=ContentType.MOVIE)
            == 1
        )
        assert (
            storage_manager.enrichment.count_needing(content_type=ContentType.BOOK) == 1
        )

    def test_count_matches_get_length(self, storage_manager: StorageManager) -> None:
        for index in range(3):
            db_id = storage_manager.save_content_item(
                ContentItem(
                    id=f"movie{index}",
                    title=f"Movie {index}",
                    content_type=ContentType.MOVIE,
                    status=ConsumptionStatus.UNREAD,
                )
            )
            if index == 0:
                storage_manager.enrichment.mark_complete(db_id, "tmdb", "high")

        items = storage_manager.enrichment.items_needing(limit=100)
        count = storage_manager.enrichment.count_needing()

        assert count == 2
        assert len(items) == 2


class TestEnrichmentStats:
    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    def test_the_four_states_partition_the_library_regression(
        self, storage_manager: StorageManager
    ) -> None:
        db_ids = [
            save_unenriched(storage_manager, f"item{index}") for index in range(6)
        ]
        storage_manager.enrichment.mark_complete(db_ids[0], "tmdb", "high")
        storage_manager.enrichment.mark_complete(db_ids[1], "none", "not_found")
        storage_manager.enrichment.mark_failed(db_ids[2], "tmdb: HTTP 503")
        storage_manager.enrichment.mark_needed(db_ids[3])
        storage_manager.enrichment.mark_complete(db_ids[5], "none", "not_found")
        storage_manager.enrichment.reset(content_item_id=db_ids[5])

        stats = storage_manager.enrichment.stats()

        assert stats["enriched"] == 1
        assert stats["not_found"] == 1
        assert stats["failed"] == 1
        assert stats["pending"] == 3
        assert (
            stats["enriched"] + stats["pending"] + stats["not_found"] + stats["failed"]
            == stats["total"]
        ), "the four reported states must account for each item exactly once"

    def test_resettable_is_the_count_an_unfiltered_reset_re_queues(
        self, storage_manager: StorageManager
    ) -> None:
        db_ids = [
            save_unenriched(storage_manager, f"item{index}") for index in range(3)
        ]
        storage_manager.enrichment.mark_complete(db_ids[0], "tmdb", "high")
        storage_manager.enrichment.mark_needed(db_ids[1])

        stats = storage_manager.enrichment.stats()

        assert (stats["total"], stats["pending"], stats["resettable"]) == (3, 2, 2)
        assert storage_manager.enrichment.reset().requeued == 2

    def test_a_provider_filter_counts_every_item_it_wrote_to_not_the_credited_ones(
        self, storage_manager: StorageManager
    ) -> None:
        credited = save_unenriched(storage_manager, "item1")
        written_to = save_unenriched(storage_manager, "item2")
        provider_enriches(storage_manager, credited, "tmdb", genres=["Action"])
        provider_enriches(storage_manager, written_to, "tmdb", genres=["Drama"])
        storage_manager.enrichment.mark_complete(credited, "tmdb", "high")
        storage_manager.enrichment.mark_complete(written_to, "hardcover", "high")

        stats = storage_manager.enrichment.stats()
        counted = storage_manager.enrichment.reset_count(provider="tmdb")

        assert stats["by_provider"]["tmdb"] == 1
        assert counted == 2
        assert stats["resettable_by_provider"]["tmdb"] == counted
        assert storage_manager.enrichment.reset(provider="tmdb").requeued == counted

    def test_a_not_found_item_names_no_provider_in_the_resettable_counts(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = save_unenriched(storage_manager, "item1")
        storage_manager.enrichment.mark_complete(db_id, "none", "not_found")

        assert storage_manager.enrichment.stats()["resettable_by_provider"] == {}

    def test_stats_for_one_user_exclude_another_users_items(
        self, storage_manager: StorageManager
    ) -> None:
        with storage_manager.sqlite_db.connection() as conn:
            other_user = create_user(conn, username="other")
        mine = [
            save_unenriched(storage_manager, f"mine{index}", user_id=1)
            for index in range(3)
        ]
        theirs = save_unenriched(storage_manager, "theirs", user_id=other_user)
        storage_manager.enrichment.mark_complete(mine[0], "tmdb", "high")
        storage_manager.enrichment.mark_failed(mine[1], "tmdb: HTTP 503")
        storage_manager.enrichment.mark_complete(theirs, "rawg", "medium")

        stats = storage_manager.enrichment.stats(user_id=1)

        assert stats["total"] == 3
        assert stats["enriched"] == 1
        assert stats["failed"] == 1
        assert stats["pending"] == 1
        assert stats["by_provider"] == {"tmdb": 1}
        assert stats["by_quality"] == {"high": 1}


class TestTagsAndDescriptionStorage:
    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    def test_save_and_load_movie_with_tags(
        self, storage_manager: StorageManager
    ) -> None:
        item = ContentItem(
            id="movie1",
            title="Test Movie",
            content_type=ContentType.MOVIE,
            status=ConsumptionStatus.UNREAD,
            metadata={
                "genres": ["Action", "Sci-Fi"],
                "tags": ["blockbuster", "franchise"],
                "description": "An epic space adventure.",
                "director": "Test Director",
            },
        )

        db_id = storage_manager.save_content_item(item)
        loaded = storage_manager.get_content_item(db_id)

        assert loaded is not None
        assert loaded.metadata.get("tags") == ["blockbuster", "franchise"]
        assert loaded.metadata.get("description") == "An epic space adventure."


class TestEnrichmentFilter:
    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    def _save(self, storage: StorageManager, external_id: str) -> int:
        return storage.save_content_item(
            ContentItem(
                id=external_id,
                title=f"Movie {external_id}",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )

    def test_not_enriched_includes_all_four_subcases(
        self, storage_manager: StorageManager
    ) -> None:
        no_row = self._save(storage_manager, "no_row")
        pending = self._save(storage_manager, "pending")
        not_found = self._save(storage_manager, "not_found")
        failed = self._save(storage_manager, "failed")
        enriched = self._save(storage_manager, "enriched")

        storage_manager.enrichment.mark_needed(pending)
        storage_manager.enrichment.mark_complete(not_found, "tmdb", "not_found")
        storage_manager.enrichment.mark_failed(failed, "boom")
        storage_manager.enrichment.mark_complete(enriched, "tmdb", "high")

        items = storage_manager.get_content_items(enrichment="not_enriched")
        db_ids = {item.db_id for item in items}

        assert db_ids == {no_row, pending, not_found, failed}
        assert all(item.enriched is False for item in items)

    def test_enriched_returns_complement(self, storage_manager: StorageManager) -> None:
        self._save(storage_manager, "no_row")
        high = self._save(storage_manager, "high")
        medium = self._save(storage_manager, "medium")

        storage_manager.enrichment.mark_complete(high, "tmdb", "high")
        storage_manager.enrichment.mark_complete(medium, "tmdb", "medium")

        items = storage_manager.get_content_items(enrichment="enriched")
        db_ids = {item.db_id for item in items}

        assert db_ids == {high, medium}
        assert all(item.enriched is True for item in items)

    def test_not_enriched_combines_with_content_type_filter(
        self, storage_manager: StorageManager
    ) -> None:
        movie_pending = self._save(storage_manager, "movie_pending")
        movie_enriched = self._save(storage_manager, "movie_enriched")
        storage_manager.enrichment.mark_complete(movie_enriched, "tmdb", "high")

        book_pending = storage_manager.save_content_item(
            ContentItem(
                id="book_pending",
                title="Book Pending",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            )
        )

        items = storage_manager.get_content_items(
            content_type=ContentType.MOVIE, enrichment="not_enriched"
        )
        db_ids = {item.db_id for item in items}

        assert db_ids == {movie_pending}
        assert movie_enriched not in db_ids
        assert book_pending not in db_ids


class TestManualMetadataEdit:
    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    def test_manual_edit_persists_and_holds_each_field_it_wrote(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Test Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )

        updated = storage_manager.update_item_from_ui(
            db_id=db_id,
            status="unread",
            genres=["Drama", "Thriller"],
            tags=["slow-burn"],
            description="A tense character study.",
        )
        assert updated is True

        loaded = storage_manager.get_content_item(db_id)
        assert loaded.metadata.get("genres") == ["Drama", "Thriller"]
        assert loaded.metadata.get("tags") == ["slow-burn"]
        assert loaded.metadata.get("description") == "A tense character study."
        assert set(loaded.manual_fields) == {
            "status",
            "genres",
            "tags",
            "description",
        }
        # An edit is not enrichment: the item stays in front of the providers,
        # which the holds now keep off these four fields.
        assert loaded.enriched is False
        assert storage_manager.enrichment.status(db_id) is None

    def test_manual_edit_overwrites_existing_values(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Test Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                metadata={
                    "genres": ["Action", "Comedy"],
                    "tags": ["old"],
                    "description": "Original synopsis.",
                },
            )
        )

        storage_manager.update_item_from_ui(
            db_id=db_id,
            status="unread",
            genres=["Drama"],
            tags=["new"],
        )

        loaded = storage_manager.get_content_item(db_id)
        assert loaded.metadata.get("genres") == ["Drama"]
        assert loaded.metadata.get("tags") == ["new"]
        assert loaded.metadata.get("description") == "Original synopsis."

    def test_status_only_edit_does_not_mark_enriched(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Test Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )

        storage_manager.update_item_from_ui(db_id=db_id, status="completed", rating=5)

        assert storage_manager.enrichment.status(db_id) is None
        assert storage_manager.get_content_item(db_id).enriched is False

    @pytest.mark.parametrize("emptied", ["", "   "])
    def test_an_emptied_description_clears_rather_than_storing_blanks(
        self, storage_manager: StorageManager, emptied: str
    ) -> None:
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Test Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                metadata={"description": "Original synopsis."},
            )
        )

        storage_manager.update_item_from_ui(db_id=db_id, description=emptied)

        loaded = storage_manager.get_content_item(db_id)
        assert not loaded.metadata.get("description")

    def test_genres_empty_list_clears_while_none_leaves_as_is(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Test Movie",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
                metadata={"genres": ["Action", "Comedy"]},
            )
        )

        storage_manager.update_item_from_ui(db_id=db_id, status="unread", genres=[])

        loaded = storage_manager.get_content_item(db_id)
        assert loaded.metadata.get("genres", []) == []

        conn = storage_manager.sqlite_db._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT genres FROM movie_details WHERE content_item_id = ?",
                (db_id,),
            )
            assert cursor.fetchone()["genres"] == "[]"
        finally:
            conn.close()


class TestGameLengthAfterEnrichment:
    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        db_path = tmp_path / "test.db"
        return StorageManager(sqlite_path=db_path)

    @staticmethod
    def _steam_game() -> ContentItem:
        return ContentItem(
            id="game1",
            title="Vampire Survivors",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.COMPLETED,
            metadata={"playtime_minutes": 18000, "playtime_hours": 300.0},
        )

    def test_rawg_average_survives_the_round_trip_and_classifies(
        self, storage_manager: StorageManager
    ) -> None:
        item = self._steam_game()
        result = EnrichmentResult(
            extra_metadata={"average_playtime_hours": 6, "metacritic": 77}
        )
        merged = merge_enrichment(item.metadata, result)
        item.metadata = merged

        loaded = storage_manager.get_content_item(
            storage_manager.save_content_item(item)
        )

        assert loaded.metadata.get("average_playtime_hours") == 6
        assert classify_length(loaded) == LengthPreference.SHORT
        assert score_length_match(loaded, {"video_game": "short"}) == 1.0
        assert loaded.metadata.get("playtime_hours") == 300.0

    def test_enrichment_never_fills_the_users_own_hours(
        self, storage_manager: StorageManager
    ) -> None:
        item = ContentItem(
            id="game2",
            title="Cyberpunk 2077",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            metadata={"platform": "GOG"},
        )
        result = EnrichmentResult(extra_metadata={"average_playtime_hours": 60})
        item.metadata = merge_enrichment(item.metadata, result)

        loaded = storage_manager.get_content_item(
            storage_manager.save_content_item(item)
        )

        assert "playtime_hours" not in loaded.metadata
        assert classify_length(loaded) == LengthPreference.LONG


class TestCorrectionsAreNotEnrichment:
    @pytest.fixture
    def storage_manager(self, tmp_path: Path) -> StorageManager:
        db_path = tmp_path / "corrections.db"
        return StorageManager(sqlite_path=db_path)

    @staticmethod
    def _movie(storage_manager: StorageManager) -> int:
        return storage_manager.save_content_item(
            ContentItem(
                id="movie1",
                title="Dune",
                content_type=ContentType.MOVIE,
                status=ConsumptionStatus.UNREAD,
            )
        )

    def test_correcting_the_year_and_creator_does_not_mark_enriched(
        self, storage_manager: StorageManager
    ) -> None:
        db_id = self._movie(storage_manager)

        storage_manager.update_item_from_ui(
            db_id=db_id, release_year=2021, creator="Denis Villeneuve"
        )

        assert storage_manager.enrichment.status(db_id) is None
        assert storage_manager.get_content_item(db_id).enriched is False
