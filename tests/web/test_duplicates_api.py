"""The web half of the duplicates review: /api/duplicates and /api/merges."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager
from src.utils.duplicate_serialization import merge_to_dict, suggestion_to_dict
from tests.factories import authenticated_client, booted_web_app


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    manager = StorageManager(sqlite_path=tmp_path / "duplicates.db")
    rows = [
        ("calibre", "1", "Deadhouse Gates", ContentType.BOOK),
        ("goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)", ContentType.BOOK),
        (
            "storygraph_csv",
            "3",
            "Deadhouse Gates (Malazan, Book Two)",
            ContentType.BOOK,
        ),
        ("gog", "4", "Hades", ContentType.VIDEO_GAME),
        ("steam", "5", "Hades (Supergiant)", ContentType.VIDEO_GAME),
    ]
    for source, external_id, title, content_type in rows:
        manager.save_content_item(
            ContentItem(
                id=external_id,
                source=source,
                title=title,
                content_type=content_type,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=1,
        )
    return manager


@pytest.fixture()
def client(storage: StorageManager) -> Iterator[TestClient]:
    with booted_web_app(storage, {"storage": {"database_path": "data/test.db"}}) as app:
        yield authenticated_client(app)


def _ids(client: TestClient, title: str) -> int:
    items = client.get("/api/items", params={"limit": 200}).json()
    (item,) = [one for one in items if one["title"] == title]
    return int(item["db_id"])


def _suggestions(client: TestClient, **params: Any) -> Any:
    response = client.get("/api/duplicates", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _blocks(client: TestClient, **params: Any) -> list[list[int]]:
    return [
        [copy["db_id"] for copy in block["copies"]]
        for block in _suggestions(client, **params)["suggestions"]
    ]


def test_a_block_is_offered_with_every_copy_and_the_key_that_grouped_them(
    client: TestClient, storage: StorageManager
) -> None:
    page = _suggestions(client, type="book")

    assert page["total"] == 1
    (block,) = page["suggestions"]
    (offered,) = storage.list_duplicate_suggestions(
        user_id=1, content_type=ContentType.BOOK
    ).suggestions
    assert block == suggestion_to_dict(offered)
    assert block["evidence"] == "title_qualifier"
    assert [copy["title"] for copy in block["copies"]] == [
        "Deadhouse Gates",
        "Deadhouse Gates (Malazan Book 2)",
        "Deadhouse Gates (Malazan, Book Two)",
    ]
    assert block["survivor_id"] == block["copies"][0]["db_id"]


def test_a_limit_cuts_the_offer_and_the_count_still_says_what_is_left(
    client: TestClient,
) -> None:
    page = _suggestions(client, limit=1)

    assert page["total"] == 2
    assert len(page["suggestions"]) == 1


def test_a_limit_past_the_ceiling_is_refused_rather_than_served(
    client: TestClient,
) -> None:
    assert client.get("/api/duplicates", params={"limit": 201}).status_code == 422


def test_an_unknown_type_is_refused_by_name(client: TestClient) -> None:
    response = client.get("/api/duplicates", params={"type": "audiobook"})

    assert response.status_code == 400
    assert "Valid options" in response.json()["detail"]


def test_a_merge_is_listed_and_the_undo_puts_the_absorbed_row_back(
    client: TestClient, storage: StorageManager
) -> None:
    survivor = _ids(client, "Deadhouse Gates")
    absorbed = _ids(client, "Deadhouse Gates (Malazan Book 2)")

    merged = client.post(
        "/api/merges", json={"survivor_id": survivor, "absorbed_id": absorbed}
    )
    listed = client.get("/api/merges")

    assert merged.status_code == 200, merged.text
    record = merged.json()
    (stored,) = storage.list_content_item_merges(user_id=1)
    assert record == merge_to_dict(stored)
    assert record["evidence"] == "manual"
    assert record["survivor_id"] == survivor
    assert record["absorbed_id"] == absorbed
    assert listed.json() == [record]

    undone = client.delete(f"/api/merges/{record['id']}")

    assert undone.status_code == 200, undone.text
    assert undone.json()["absorbed_id"] == absorbed
    assert client.get("/api/merges").json() == []
    assert _ids(client, "Deadhouse Gates (Malazan Book 2)") == absorbed


def test_a_refused_merge_answers_the_storage_layer_s_own_words(
    client: TestClient,
) -> None:
    book = _ids(client, "Deadhouse Gates")
    game = _ids(client, "Hades")

    response = client.post(
        "/api/merges", json={"survivor_id": book, "absorbed_id": game}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "A book cannot absorb a video_game."


def test_undoing_a_merge_that_is_not_there_says_so(client: TestClient) -> None:
    response = client.delete("/api/merges/404")

    assert response.status_code == 404
    assert response.json()["detail"] == "Merge 404 not found."


def test_a_copy_declined_out_of_a_block_leaves_the_rest_of_it_offered(
    client: TestClient,
) -> None:
    one = _ids(client, "Deadhouse Gates")
    other = _ids(client, "Deadhouse Gates (Malazan Book 2)")
    third = _ids(client, "Deadhouse Gates (Malazan, Book Two)")

    declined = client.post(
        "/api/duplicates/declined", json={"one_id": third, "other_ids": [one, other]}
    )

    assert declined.status_code == 200, declined.text
    assert declined.json() == [
        {
            "one_id": one,
            "one_title": "Deadhouse Gates",
            "other_id": third,
            "other_title": "Deadhouse Gates (Malazan, Book Two)",
        },
        {
            "one_id": other,
            "one_title": "Deadhouse Gates (Malazan Book 2)",
            "other_id": third,
            "other_title": "Deadhouse Gates (Malazan, Book Two)",
        },
    ]
    assert client.get("/api/duplicates/declined").json() == declined.json()
    assert _blocks(client, type="book") == [[one, other]]

    lifted = client.delete(f"/api/duplicates/declined/{one}/{third}")

    assert lifted.status_code == 200, lifted.text
    assert _blocks(client, type="book") == [[one, other], [one, third]]


def test_declining_what_is_not_a_live_pair_refuses_in_the_clis_own_words(
    client: TestClient,
) -> None:
    one = _ids(client, "Deadhouse Gates")
    other = _ids(client, "Deadhouse Gates (Malazan Book 2)")

    single = client.post(
        "/api/duplicates/declined", json={"one_id": one, "other_ids": [9999]}
    )
    several = client.post(
        "/api/duplicates/declined", json={"one_id": one, "other_ids": [other, 9999]}
    )

    assert single.status_code == 404
    assert single.json()["detail"] == (
        f"Item {one} and item 9999 are not a live pair to decline."
    )
    assert several.status_code == 404
    assert several.json()["detail"] == (
        f"Item {one} and items {other}, 9999 are not live pairs to decline."
    )
    assert client.get("/api/duplicates/declined").json() == []


def test_lifting_a_refusal_nobody_made_refuses_by_id(client: TestClient) -> None:
    response = client.delete("/api/duplicates/declined/1/2")

    assert response.status_code == 404
    assert response.json()["detail"] == "Items 1 and 2 are not a declined pair."


def test_no_route_here_offers_to_delete_an_item(client: TestClient) -> None:
    """Deleting a hidden row orphans its children, and these are those ids."""
    absorbed = _ids(client, "Deadhouse Gates (Malazan Book 2)")

    assert client.delete(f"/api/items/{absorbed}").status_code == 405


def test_an_empty_library_answers_an_empty_view_on_every_listing(
    tmp_path: Path,
) -> None:
    empty = StorageManager(sqlite_path=tmp_path / "empty.db")
    with booted_web_app(empty, {"storage": {"database_path": "data/test.db"}}) as app:
        client = authenticated_client(app)

        assert client.get("/api/duplicates").json() == {"total": 0, "suggestions": []}
        assert client.get("/api/merges").json() == []
        assert client.get("/api/duplicates/declined").json() == []


def test_a_lift_a_merge_blocks_is_refused_in_the_storage_layer_s_own_words(
    client: TestClient,
) -> None:
    """Uncaught, it is a 500 saying nothing about which merge to undo."""
    one = _ids(client, "Deadhouse Gates")
    other = _ids(client, "Deadhouse Gates (Malazan Book 2)")
    client.post("/api/duplicates/declined", json={"one_id": one, "other_ids": [other]})
    merged = client.post("/api/merges", json={"survivor_id": one, "absorbed_id": other})

    response = client.delete(f"/api/duplicates/declined/{one}/{other}")

    assert response.status_code == 409
    assert f"before merge {merged.json()['id']}" in response.json()["detail"]
    assert [
        (pair["one_id"], pair["other_id"])
        for pair in client.get("/api/duplicates/declined").json()
    ] == [(one, other)]
