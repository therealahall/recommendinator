"""The web half of the duplicates review: /api/duplicates and /api/merges."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.storage.manager import StorageManager
from tests.factories import authenticated_client, booted_web_app


@pytest.fixture()
def storage(tmp_path: Path) -> StorageManager:
    manager = StorageManager(sqlite_path=tmp_path / "duplicates.db")
    rows = [
        ("calibre", "1", "Deadhouse Gates", ContentType.BOOK),
        ("goodreads_csv", "2", "Deadhouse Gates (Malazan Book 2)", ContentType.BOOK),
        ("gog", "3", "Hades", ContentType.VIDEO_GAME),
        ("steam", "4", "Hades (Supergiant)", ContentType.VIDEO_GAME),
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


def test_a_pair_is_offered_with_both_sides_and_the_key_that_paired_them(
    client: TestClient,
) -> None:
    page = _suggestions(client, type="book")

    assert page["total"] == 1
    (pair,) = page["suggestions"]
    assert pair["content_type"] == "book"
    assert pair["evidence"] == "title_qualifier"
    assert pair["survivor"]["title"] == "Deadhouse Gates"
    assert pair["survivor"]["source"] == "calibre"
    assert pair["absorbed"]["title"] == "Deadhouse Gates (Malazan Book 2)"


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
    client: TestClient,
) -> None:
    survivor = _ids(client, "Deadhouse Gates")
    absorbed = _ids(client, "Deadhouse Gates (Malazan Book 2)")

    merged = client.post(
        "/api/merges", json={"survivor_id": survivor, "absorbed_id": absorbed}
    )
    listed = client.get("/api/merges")

    assert merged.status_code == 200, merged.text
    record = merged.json()
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


def test_an_undo_out_of_order_names_the_merge_to_deal_with_first(
    client: TestClient,
) -> None:
    survivor = _ids(client, "Deadhouse Gates")
    absorbed = _ids(client, "Deadhouse Gates (Malazan Book 2)")
    first = client.post(
        "/api/merges", json={"survivor_id": survivor, "absorbed_id": absorbed}
    ).json()
    second = client.post(
        "/api/merges",
        json={"survivor_id": survivor, "absorbed_id": _ids(client, "Hades")},
    )

    assert second.status_code == 409
    assert client.delete(f"/api/merges/{first['id']}").status_code == 200


def test_undoing_a_merge_that_is_not_there_says_so(client: TestClient) -> None:
    response = client.delete("/api/merges/404")

    assert response.status_code == 404
    assert response.json()["detail"] == "Merge 404 not found."


def test_a_declined_pair_stops_being_offered_and_lifting_it_offers_it_again(
    client: TestClient,
) -> None:
    one = _ids(client, "Deadhouse Gates")
    other = _ids(client, "Deadhouse Gates (Malazan Book 2)")

    declined = client.post(
        "/api/duplicates/declined", json={"one_id": one, "other_id": other}
    )

    assert declined.status_code == 200, declined.text
    assert declined.json() == {
        "one_id": one,
        "one_title": "Deadhouse Gates",
        "other_id": other,
        "other_title": "Deadhouse Gates (Malazan Book 2)",
    }
    assert client.get("/api/duplicates/declined").json() == [declined.json()]
    assert _suggestions(client, type="book")["total"] == 0

    lifted = client.delete(f"/api/duplicates/declined/{one}/{other}")

    assert lifted.status_code == 200, lifted.text
    assert client.get("/api/duplicates/declined").json() == []
    assert _suggestions(client, type="book")["total"] == 1


def test_declining_what_is_not_a_live_pair_refuses_by_id(client: TestClient) -> None:
    one = _ids(client, "Deadhouse Gates")

    response = client.post(
        "/api/duplicates/declined", json={"one_id": one, "other_id": 9999}
    )

    assert response.status_code == 404
    assert f"Items {one} and 9999 are not a live pair" in response.json()["detail"]


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


@pytest.fixture()
def trio(tmp_path: Path) -> Iterator[TestClient]:
    """Three live rows of one work: the state a group of copies is decided from."""
    manager = StorageManager(sqlite_path=tmp_path / "trio.db")
    rows = [
        ("goodreads_csv", "1", "Deadhouse Gates"),
        ("goodreads_csv", "2", "Deadhouse Gates (Malazan, #2)"),
        ("calibre", "3", "Deadhouse Gates (Malazan Book Two)"),
    ]
    for source, external_id, title in rows:
        manager.save_content_item(
            ContentItem(
                id=external_id,
                source=source,
                title=title,
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.UNREAD,
            ),
            user_id=1,
        )
    with booted_web_app(manager, {"storage": {"database_path": "data/test.db"}}) as app:
        yield authenticated_client(app)


def _offered_pairs(client: TestClient) -> list[set[int]]:
    return [
        {pair["survivor"]["db_id"], pair["absorbed"]["db_id"]}
        for pair in _suggestions(client)["suggestions"]
    ]


def test_a_lift_a_merge_blocks_is_refused_in_the_storage_layer_s_own_words(
    trio: TestClient,
) -> None:
    """Uncaught, it is a 500 saying nothing about which merge to undo."""
    one, two, three = sorted({db_id for pair in _offered_pairs(trio) for db_id in pair})
    trio.post("/api/duplicates/declined", json={"one_id": one, "other_id": three})
    merged = trio.post("/api/merges", json={"survivor_id": two, "absorbed_id": three})

    response = trio.delete(f"/api/duplicates/declined/{one}/{three}")

    assert response.status_code == 409
    assert f"before merge {merged.json()['id']}" in response.json()["detail"]


def test_offering_a_pair_back_keeps_the_refusal_until_it_can_be_honoured(
    trio: TestClient,
) -> None:
    """Reported: a dismissed pair left both lists at once and could not be got back.

    Root cause: ``undecline_duplicate`` deletes the refusal on the two ids
    alone, while ``decline_duplicate`` takes one only over two live rows. So
    lifting a refusal whose other side a later merge has hidden throws the
    decision away and offers nothing in its place, under an acknowledgement
    saying the pair may be offered again. Fix: refuse the lift while the pair
    cannot be offered, leaving the refusal listed.
    """
    one, two, three = sorted({db_id for pair in _offered_pairs(trio) for db_id in pair})
    assert (
        trio.post(
            "/api/duplicates/declined", json={"one_id": one, "other_id": three}
        ).status_code
        == 200
    )
    assert (
        trio.post(
            "/api/merges", json={"survivor_id": two, "absorbed_id": three}
        ).status_code
        == 200
    )

    trio.delete(f"/api/duplicates/declined/{one}/{three}")

    assert trio.get("/api/duplicates/declined").json() != [] or {
        one,
        three,
    } in _offered_pairs(trio)
