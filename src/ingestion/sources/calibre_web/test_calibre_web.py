"""Tests for the Calibre-Web OPDS book import plugin."""

from pathlib import Path
from unittest.mock import Mock, patch
from xml.etree import ElementTree

import pytest
import requests

from src.ingestion.plugin_base import SourceError
from src.ingestion.sources.calibre_web.calibre_web import (
    CalibreWebPlugin,
    _parse_opds_xml,
)
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.sources.service import (
    SourceConfigError,
    resolve_inputs,
    update_source_config_values,
)
from src.storage.manager import StorageManager
from src.storage.sqlite_db import SQLiteDB

_FEED_HEADER = (
    '<feed xmlns="http://www.w3.org/2005/Atom" '
    'xmlns:dc="http://purl.org/dc/terms/" '
    'xmlns:schema="http://schema.org/">'
)


def _entry(
    entry_id: str = "urn:uuid:abc-123",
    title: str = "The Hobbit",
    author: str = "J.R.R. Tolkien",
    extra: str = "",
) -> str:
    """Build an OPDS <entry> XML fragment for tests."""
    return (
        "<entry>"
        f"<id>{entry_id}</id>"
        f"<title>{title}</title>"
        f"<author><name>{author}</name></author>"
        f"{extra}"
        "</entry>"
    )


def _feed(entries: str, next_href: str | None = None) -> str:
    """Wrap entry fragments in an OPDS feed, optionally with a next link."""
    next_link = (
        f'<link rel="next" href="{next_href}" '
        'type="application/atom+xml;profile=opds-catalog"/>'
        if next_href
        else ""
    )
    return f"{_FEED_HEADER}{next_link}{entries}</feed>"


def _xml_response(body: str, status_code: int = 200) -> Mock:
    """Build a mocked requests.Response carrying an OPDS feed body."""
    response = Mock(spec=requests.Response)
    response.status_code = status_code
    response.content = body.encode("utf-8")
    response.raise_for_status = Mock(spec=requests.Response.raise_for_status)
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(
            f"{status_code} error"
        )
    return response


def _empty_read_feed() -> Mock:
    """A read-books feed with no entries (nothing marked completed)."""
    return _xml_response(_feed(""))


@pytest.fixture()
def plugin() -> CalibreWebPlugin:
    """Create a CalibreWebPlugin instance."""
    return CalibreWebPlugin()


@pytest.fixture()
def config() -> dict[str, object]:
    """Minimal valid runtime config for fetch()."""
    return {
        "url": "http://localhost:8083",
        "username": "reader",
        "password": "secret",
        "verify_ssl": True,
    }


class TestCalibreWebTransformConfig:
    """Tests for transform_config normalisation."""

    def test_strips_trailing_slash_and_whitespace(self) -> None:
        result = CalibreWebPlugin.transform_config(
            {
                "url": "  http://host:8083/  ",
                "username": "  reader  ",
                "password": "  secret  ",
            }
        )
        assert result["url"] == "http://host:8083"
        assert result["username"] == "reader"
        assert result["password"] == "secret"


class TestCalibreWebValidateConfig:
    """Tests for validate_config."""

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_url_username_password_required(
        self, plugin: CalibreWebPlugin, blank: str
    ) -> None:
        """Empty-string and whitespace-only fields are reported as required."""
        errors = plugin.validate_config(
            {"url": blank, "username": blank, "password": blank}
        )
        assert "'url' is required" in errors
        assert "'username' is required" in errors
        assert "'password' is required" in errors

    def test_password_from_credential_store_passes(
        self, plugin: CalibreWebPlugin
    ) -> None:
        """Password absent from config but present in the DB should validate."""
        mock_storage = Mock(spec=StorageManager)
        mock_storage.credentials.get_for_source.return_value = {"password": "db_secret"}
        errors = plugin.validate_config(
            {"url": "http://host", "username": "u", "_source_id": "my_calibre"},
            storage=mock_storage,
            user_id=1,
        )
        assert errors == []
        mock_storage.credentials.get_for_source.assert_called_once_with(1, "my_calibre")


class TestCalibreWebFetch:
    """Tests for fetch() OPDS parsing behaviour."""

    def test_happy_path_parses_entry_fields(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        entry = _entry(
            entry_id="urn:uuid:hobbit-uuid",
            title="The Hobbit",
            author="J.R.R. Tolkien",
            extra=(
                "<summary>A hobbit's adventure.</summary>"
                "<dc:publisher>Allen &amp; Unwin</dc:publisher>"
                "<dc:language>en</dc:language>"
                "<published>1937-09-21</published>"
                "<dc:identifier>isbn:9780261103283</dc:identifier>"
                "<series>Middle-earth</series>"
                "<series_index>1</series_index>"
                '<category term="fantasy" label="Fantasy"/>'
                '<link rel="http://opds-spec.org/image" '
                'href="/cover/1.jpg"/>'
                '<link rel="http://opds-spec.org/image/thumbnail" '
                'href="/thumb/1.jpg"/>'
            ),
        )
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert len(items) == 1
        item = items[0]
        assert item.title == "The Hobbit"
        assert item.author == "J.R.R. Tolkien"
        assert item.id == "calibre:hobbit-uuid"
        assert item.content_type == ContentType.BOOK
        assert item.status == ConsumptionStatus.UNREAD
        assert item.source == "calibre_web"
        assert item.metadata["summary"] == "A hobbit's adventure."
        assert item.metadata["publisher"] == "Allen & Unwin"
        assert item.metadata["language"] == "en"
        assert item.metadata["published"] == "1937-09-21"
        assert item.metadata["isbn"] == "9780261103283"
        assert item.metadata["series"] == "Middle-earth"
        assert item.metadata["series_index"] == 1.0
        assert item.metadata["tags"] == ["Fantasy"]
        assert item.metadata["cover_url"] == "/cover/1.jpg"
        assert item.metadata["thumbnail_url"] == "/thumb/1.jpg"

    def test_pagination_follows_next_links(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        page1 = _feed(
            _entry(entry_id="urn:uuid:b1", title="Book One"),
            next_href="/opds/new?offset=1",
        )
        page2 = _feed(_entry(entry_id="urn:uuid:b2", title="Book Two"))
        responses = [
            _empty_read_feed(),
            _xml_response(page1),
            _xml_response(page2),
        ]
        with patch("requests.get", side_effect=responses) as mock_get:
            items = list(plugin.fetch(config))

        titles = [item.title for item in items]
        assert titles == ["Book One", "Book Two"]
        # read feed + 2 catalog pages
        assert mock_get.call_count == 3
        second_page_url = mock_get.call_args_list[2].args[0]
        assert second_page_url == "http://localhost:8083/opds/new?offset=1"

    def test_read_shelf_marks_completed(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        read_feed = _feed(_entry(entry_id="urn:uuid:read-1", title="Read Book"))
        catalog = _feed(
            _entry(entry_id="urn:uuid:read-1", title="Read Book")
            + _entry(entry_id="urn:uuid:unread-1", title="Unread Book")
        )
        responses = [_xml_response(read_feed), _xml_response(catalog)]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        by_id = {item.id: item for item in items}
        assert by_id["calibre:read-1"].status == ConsumptionStatus.COMPLETED
        assert by_id["calibre:unread-1"].status == ConsumptionStatus.UNREAD

    def test_read_shelf_unavailable_defaults_unread(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """A missing read-books shelf (404) must not crash or guess COMPLETED."""
        catalog = _feed(_entry(entry_id="urn:uuid:x", title="Book"))
        responses = [
            _xml_response("<html>not found</html>", status_code=404),
            _xml_response(catalog),
        ]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert len(items) == 1
        assert items[0].status == ConsumptionStatus.UNREAD

    def test_rating_scheme_category_not_emitted_as_tag(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """A rating category's star label must not leak into the tag list."""
        entry = _entry(
            extra=(
                "<rating>10</rating>"
                '<category scheme="http://opds-spec.org/2010/catalog/ratings" '
                'term="8" label="8"/>'
            )
        )
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert "tags" not in items[0].metadata
        assert items[0].rating is None

    def test_entry_missing_optional_fields(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        bare = "<entry><id>urn:uuid:bare</id><title>Bare Book</title></entry>"
        responses = [_empty_read_feed(), _xml_response(_feed(bare))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        item = items[0]
        assert item.title == "Bare Book"
        assert item.author is None
        assert item.rating is None
        assert item.metadata == {}

    def test_entry_without_title_skipped(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        no_title = "<entry><id>urn:uuid:notitle</id></entry>"
        catalog = _feed(no_title + _entry(title="Has Title"))
        responses = [_empty_read_feed(), _xml_response(catalog)]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert [item.title for item in items] == ["Has Title"]

    def test_calibre_id_prefix_scheme(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        entry = _entry(entry_id="urn:calibre:42", title="Numbered")
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].id == "calibre:42"

    def test_auth_failure_raises_source_error(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        unauthorized = _xml_response("<html>unauthorized</html>", status_code=401)
        with patch("requests.get", return_value=unauthorized):
            with pytest.raises(SourceError, match="Authentication failed"):
                list(plugin.fetch(config))

    def test_malformed_xml_raises_source_error(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        responses = [
            _empty_read_feed(),
            _xml_response("<feed><entry></broken>"),
        ]
        with patch("requests.get", side_effect=responses):
            with pytest.raises(SourceError, match="Failed to parse OPDS feed"):
                list(plugin.fetch(config))

    def test_verify_ssl_passed_to_requests(self, plugin: CalibreWebPlugin) -> None:
        config = {
            "url": "https://host",
            "username": "u",
            "password": "p",
            "verify_ssl": False,
        }
        responses = [_empty_read_feed(), _xml_response(_feed(_entry()))]
        with patch("requests.get", side_effect=responses) as mock_get:
            list(plugin.fetch(config))

        assert mock_get.call_args_list[0].kwargs["verify"] is False
        assert mock_get.call_args_list[0].kwargs["auth"] == ("u", "p")


class TestCalibreWebSeries:
    """Series parsing against the real schema.org Calibre-Web OPDS shape."""

    def test_schema_org_series_attributes(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """Calibre-Web's <schema:Series schema:name/schema:position> is read."""
        entry = _entry(
            entry_id="urn:uuid:series-attr",
            title="The Fellowship of the Ring",
            extra=(
                '<schema:Series schema:name="The Lord of the Rings" '
                'schema:position="1"/>'
            ),
        )
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].metadata["series"] == "The Lord of the Rings"
        assert items[0].metadata["series_index"] == 1.0

    def test_bare_series_elements_fallback(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """Bare <series>/<series_index> children are read when no schema:Series."""
        entry = _entry(
            entry_id="urn:uuid:series-bare",
            title="Bare Series Book",
            extra="<series>Discworld</series><series_index>5</series_index>",
        )
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].metadata["series"] == "Discworld"
        assert items[0].metadata["series_index"] == 5.0


class TestCalibreWebEdgeCases:
    """QA edge-case probes added during issue #32 verification."""

    def test_off_host_next_link_not_followed(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """SSRF guard: a rel=next link to a foreign host is not requested.

        The next-page URL is fetched with the user's basic-auth credentials, so
        a rel=next pointing at an internal/foreign host (cloud metadata,
        localhost service, etc.) must be refused. Pagination stops and no
        request is ever made to the foreign host.
        """
        page1 = _feed(
            _entry(entry_id="urn:uuid:safe", title="Safe Book"),
            next_href="http://169.254.169.254/latest/meta-data/",
        )
        responses = [_empty_read_feed(), _xml_response(page1)]
        with patch("requests.get", side_effect=responses) as mock_get:
            items = list(plugin.fetch(config))

        assert [i.title for i in items] == ["Safe Book"]
        # read feed + page 1 only; the off-host next link is not fetched.
        assert mock_get.call_count == 2
        requested_hosts = [call.args[0] for call in mock_get.call_args_list]
        assert all("169.254.169.254" not in url for url in requested_hosts)

    def test_scheme_downgrade_next_link_not_followed(
        self, plugin: CalibreWebPlugin
    ) -> None:
        """SSRF guard: a same-host rel=next that downgrades HTTPS->HTTP is refused.

        Basic-auth credentials must never be sent over plaintext, so a rel=next
        keeping the configured host but switching the scheme to http is treated
        as off-origin: pagination stops and no plaintext request is made.
        """
        https_config = {
            "url": "https://library.example.com",
            "username": "reader",
            "password": "secret",
            "verify_ssl": True,
        }
        page1 = _feed(
            _entry(entry_id="urn:uuid:safe", title="Safe Book"),
            next_href="http://library.example.com/opds/new?offset=1",
        )
        responses = [_empty_read_feed(), _xml_response(page1)]
        with patch("requests.get", side_effect=responses) as mock_get:
            items = list(plugin.fetch(https_config))

        assert [i.title for i in items] == ["Safe Book"]
        # read feed + page 1 only; the http downgrade link is not fetched.
        assert mock_get.call_count == 2
        requested_urls = [call.args[0] for call in mock_get.call_args_list]
        assert all(not url.startswith("http://") for url in requested_urls)


class TestCalibreWebXmlHardening:
    """Tests that OPDS parsing rejects XXE / billion-laughs vectors."""

    def test_doctype_rejected(self) -> None:
        """A DOCTYPE (entity-definition vector) must be refused."""
        payload = (
            b'<?xml version="1.0"?>' b'<!DOCTYPE feed [<!ENTITY lol "lol">]>' b"<feed/>"
        )
        with pytest.raises(ElementTree.ParseError):
            _parse_opds_xml(payload)


class TestCalibreWebRegression:
    """Regression tests for fixed Calibre-Web plugin bugs (issue #32)."""

    def test_numeric_category_label_preserved_as_tag_regression(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """Bug: a non-rating numeric category label was dropped from tags.

        Bug: Calibre-Web books carry numeric facets such as a publication year
        ("2008") as ordinary ``<category>`` elements with no rating scheme. An
        earlier implementation treated any numeric category label as a rating
        signal, so a year-tagged book had the year silently dropped from its
        tag list.

        Root cause: the rating-category check accepted any numeric ``<category>``
        label rather than requiring a category whose ``scheme`` marks it as a
        rating.

        Fix: only a category whose scheme contains ``"rating"`` is excluded as a
        rating category; bare numeric labels are preserved as tags by
        ``_parse_tags``.
        """
        entry = _entry(
            entry_id="urn:uuid:year",
            title="Year Tagged",
            extra='<category label="2008"/>',
        )
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].metadata.get("tags") == ["2008"]

    def test_unread_book_yields_unread_status_regression(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """Bug: a re-import could regress a completed book back to unread.

        Bug: status is resolved forward-only by resolve_status_forward in
        src/storage/merge.py, so a re-sync never reverts a COMPLETED item.
        That protection only works if the plugin emits UNREAD (not some other
        status) for unread library books; emitting anything else would defeat
        the forward-only guard.

        Root cause / fix: the plugin must assign ConsumptionStatus.UNREAD to
        unread library books. The DB-level forward-only behaviour itself is
        covered generically by tests/test_sqlite_db.py::TestStatusForwardOnly;
        here we pin the plugin's contribution to it.
        """
        catalog = _feed(_entry(entry_id="urn:uuid:fwd", title="Backlog Book"))
        responses = [_empty_read_feed(), _xml_response(catalog)]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].status == ConsumptionStatus.UNREAD

    def test_resync_does_not_wipe_user_rating_regression(
        self,
        plugin: CalibreWebPlugin,
        config: dict[str, object],
        tmp_path: Path,
    ) -> None:
        """Bug: Calibre star ratings overwrote the user's own rating on sync.

        Bug: Calibre's "download metadata" feature writes a community-average
        star rating that the plugin used to map onto the user's 1-5 rating. On a
        re-sync that fabricated rating could clobber a rating the user had set in
        Recommendinator.

        Root cause / fix: the plugin no longer extracts any rating, so it emits
        ContentItem.rating == None. The DB's set-once rating rule (covered
        generically by tests/test_sqlite_db.py::TestRatingSetOnce) then leaves an
        existing user rating untouched. Here we pin the end-to-end contract: a
        re-sync of an already-rated book preserves that rating.
        """
        db = SQLiteDB(tmp_path / "resync.db")
        db.save_content_item(
            ContentItem(
                id="calibre:resync-1",
                title="Already Rated",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                source="calibre_web",
            )
        )

        catalog = _feed(_entry(entry_id="urn:uuid:resync-1", title="Already Rated"))
        responses = [_empty_read_feed(), _xml_response(catalog)]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].rating is None
        db_id = db.save_content_item(items[0])

        retrieved = db.get_content_item(db_id)
        assert retrieved is not None
        assert retrieved.rating == 5

    def test_read_shelf_partial_pagination_keeps_collected_ids_regression(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """Bug: a failed later read-shelf page discarded already-collected ids.

        Bug: when the read-books shelf spans multiple pages and a 2nd+ page
        request fails, _fetch_read_book_ids returned an empty set, throwing away
        read ids gathered from earlier pages. On a first sync (nothing persisted
        yet) those books — correctly identified COMPLETED on page 1 — would be
        yielded UNREAD with no way for the forward-only guard to recover them.

        Root cause: the SourceError handler returned set() unconditionally
        instead of distinguishing a missing shelf (first page fails -> empty)
        from a partial pagination failure (later page fails -> keep what we have).

        Fix: return the accumulated read_ids on a later-page failure and log a
        WARNING; only a first-page failure yields an empty set.
        """
        read_page1 = _feed(
            _entry(entry_id="urn:uuid:r1", title="Read One")
            + _entry(entry_id="urn:uuid:r2", title="Read Two"),
            next_href="/opds/readbooks?offset=2",
        )
        catalog = _feed(
            _entry(entry_id="urn:uuid:r1", title="Read One")
            + _entry(entry_id="urn:uuid:r2", title="Read Two")
            + _entry(entry_id="urn:uuid:u1", title="Unread")
        )
        responses = [
            _xml_response(read_page1),
            _xml_response("<html>boom</html>", status_code=500),
            _xml_response(catalog),
        ]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        by_id = {i.id: i.status for i in items}
        assert by_id["calibre:r1"] == ConsumptionStatus.COMPLETED
        assert by_id["calibre:r2"] == ConsumptionStatus.COMPLETED
        assert by_id["calibre:u1"] == ConsumptionStatus.UNREAD

    def test_transform_config_none_values_regression(self) -> None:
        """Bug: None config values crashed transform_config with AttributeError.

        Bug: when a YAML key is present with no value (e.g. ``url:``), PyYAML
        parses it as None. ``config.get("url", "").strip()`` then returns None
        (the key exists) and ``.strip()`` raises ``AttributeError: 'NoneType'``.
        Steam hit this exact class of crash in production.

        Root cause: relying on the .get default instead of guarding None.

        Fix: use the ``(value or "")`` pattern before ``.strip()`` so explicit
        None coerces to "" without raising. The resulting config is invalid and
        is rejected by validate_config.
        """
        result = CalibreWebPlugin.transform_config(
            {"url": None, "username": None, "password": None}
        )
        assert result["url"] == ""
        assert result["username"] == ""
        assert result["password"] == ""
        errors = CalibreWebPlugin().validate_config(result)
        assert "'url' is required" in errors
        assert "'username' is required" in errors
        assert "'password' is required" in errors


class TestCalibreWebCredentialMoveRegression:
    """Regression: editing this source's settings deleted its password.

    Repointing ``url`` once sent the password to the new host; the clear that
    fixed that then fired on any edit. Fix: only a change of host counts, and
    it is refused.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "calibre.db")

    @pytest.fixture()
    def migrated(self, storage: StorageManager) -> StorageManager:
        storage.upsert_source_config(
            1,
            "calibre_web",
            "calibre_web",
            {"url": "http://localhost:8083", "username": "reader", "verify_ssl": True},
            enabled=True,
        )
        storage.credentials.save(1, "calibre_web", "password", "hunter2")
        return storage

    def _resolved(self, storage: StorageManager) -> dict[str, object]:
        entries = resolve_inputs({}, storage=storage)
        assert [entry.source_id for entry in entries] == ["calibre_web"]
        return entries[0].config

    def test_the_password_resolves_before_the_rewrite(
        self, migrated: StorageManager
    ) -> None:
        """The arrange half: without it the exfiltration test proves nothing."""
        assert self._resolved(migrated)["password"] == "hunter2"

    def test_repointing_the_url_is_refused_and_the_source_stands_still(
        self, plugin: CalibreWebPlugin, migrated: StorageManager
    ) -> None:
        with pytest.raises(SourceConfigError, match="different host"):
            update_source_config_values(
                "calibre_web", plugin, migrated, {"url": "https://attacker.example"}
            )

        resolved = self._resolved(migrated)
        assert resolved["url"] == "http://localhost:8083"
        assert resolved["password"] == "hunter2"
        assert plugin.validate_config(resolved, storage=migrated) == []

    def test_upgrading_to_https_keeps_the_password(
        self, plugin: CalibreWebPlugin, migrated: StorageManager
    ) -> None:
        """The reported edit: same Calibre-Web, now behind TLS."""
        update_source_config_values(
            "calibre_web", plugin, migrated, {"url": "https://localhost:8083"}
        )

        resolved = self._resolved(migrated)
        assert resolved["url"] == "https://localhost:8083"
        assert resolved["password"] == "hunter2"


class TestCalibreWebUrlValidation:
    """A base URL that would read local files or carry the password inline."""

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("file:///etc/passwd", "'url' must start with http:// or https://"),
            ("ftp://host/books", "'url' must start with http:// or https://"),
            ("http:///books", "'url' must name a host"),
            (
                "http://user:pw@attacker.example",
                "'url' must not embed a username or password",
            ),
        ],
    )
    def test_validate_rejects_an_unusable_url(
        self, plugin: CalibreWebPlugin, url: str, expected: str
    ) -> None:
        errors = plugin.validate_config({"url": url, "username": "u", "password": "p"})
        assert errors == [expected]

    def test_fetch_refuses_before_any_request(self, plugin: CalibreWebPlugin) -> None:
        """A sync of every source never calls validate_config."""
        with patch("requests.get") as get:
            with pytest.raises(SourceError, match="http:// or https://"):
                list(
                    plugin.fetch(
                        {
                            "url": "file:///etc/passwd",
                            "username": "u",
                            "password": "p",
                        }
                    )
                )
        get.assert_not_called()
