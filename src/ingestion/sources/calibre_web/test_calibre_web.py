"""Tests for the Calibre-Web OPDS book import plugin."""

from unittest.mock import Mock, patch
from xml.etree import ElementTree

import pytest
import requests

from src.ingestion.plugin_base import SourceError, SourcePlugin
from src.ingestion.sources.calibre_web.calibre_web import (
    CalibreWebPlugin,
    _parse_opds_xml,
)
from src.models.content import ConsumptionStatus, ContentType
from src.storage.manager import StorageManager

_FEED_HEADER = (
    '<feed xmlns="http://www.w3.org/2005/Atom" '
    'xmlns:dc="http://purl.org/dc/terms/" '
    'xmlns:schema="http://schema.org/">'
)


def _entry(
    entry_id: str = "urn:uuid:abc-123",
    title: str = "The Hobbit",
    author: str = "J.R.R. Tolkien",
    rating: str | None = None,
    extra: str = "",
) -> str:
    """Build an OPDS <entry> XML fragment for tests."""
    rating_xml = f"<rating>{rating}</rating>" if rating is not None else ""
    return (
        "<entry>"
        f"<id>{entry_id}</id>"
        f"<title>{title}</title>"
        f"<author><name>{author}</name></author>"
        f"{rating_xml}"
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


class TestCalibreWebPluginProperties:
    """Tests for plugin metadata properties."""

    def test_is_source_plugin(self, plugin: CalibreWebPlugin) -> None:
        assert isinstance(plugin, SourcePlugin)

    def test_name(self, plugin: CalibreWebPlugin) -> None:
        assert plugin.name == "calibre_web"

    def test_display_name(self, plugin: CalibreWebPlugin) -> None:
        assert plugin.display_name == "Calibre-Web"

    def test_description(self, plugin: CalibreWebPlugin) -> None:
        assert plugin.description == "Import books from a Calibre-Web library"

    def test_content_types(self, plugin: CalibreWebPlugin) -> None:
        assert plugin.content_types == [ContentType.BOOK]

    def test_requires_api_key(self, plugin: CalibreWebPlugin) -> None:
        assert plugin.requires_api_key is True

    def test_requires_network(self, plugin: CalibreWebPlugin) -> None:
        assert plugin.requires_network is True


class TestCalibreWebConfigSchema:
    """Tests for the config schema definition."""

    def test_field_names(self, plugin: CalibreWebPlugin) -> None:
        names = {field.name for field in plugin.get_config_schema()}
        assert names == {"url", "username", "password", "verify_ssl"}

    def test_required_flags(self, plugin: CalibreWebPlugin) -> None:
        fields = {f.name: f for f in plugin.get_config_schema()}
        assert fields["url"].required is True
        assert fields["username"].required is True
        assert fields["password"].required is True
        assert fields["verify_ssl"].required is False

    def test_password_is_sensitive(self, plugin: CalibreWebPlugin) -> None:
        fields = {f.name: f for f in plugin.get_config_schema()}
        assert fields["password"].sensitive is True
        assert fields["url"].sensitive is False

    def test_verify_ssl_defaults_true(self, plugin: CalibreWebPlugin) -> None:
        fields = {f.name: f for f in plugin.get_config_schema()}
        assert fields["verify_ssl"].default is True


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

    def test_verify_ssl_defaults_true(self) -> None:
        result = CalibreWebPlugin.transform_config({"url": "http://host"})
        assert result["verify_ssl"] is True

    def test_verify_ssl_preserved_when_false(self) -> None:
        result = CalibreWebPlugin.transform_config(
            {"url": "http://host", "verify_ssl": False}
        )
        assert result["verify_ssl"] is False


class TestCalibreWebValidateConfig:
    """Tests for validate_config."""

    def test_valid_config(self, plugin: CalibreWebPlugin) -> None:
        errors = plugin.validate_config(
            {"url": "http://host", "username": "u", "password": "p"}
        )
        assert errors == []

    def test_missing_url(self, plugin: CalibreWebPlugin) -> None:
        errors = plugin.validate_config({"username": "u", "password": "p"})
        assert "'url' is required" in errors

    def test_missing_username(self, plugin: CalibreWebPlugin) -> None:
        errors = plugin.validate_config({"url": "http://host", "password": "p"})
        assert "'username' is required" in errors

    def test_missing_password(self, plugin: CalibreWebPlugin) -> None:
        errors = plugin.validate_config({"url": "http://host", "username": "u"})
        assert "'password' is required" in errors

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
        mock_storage.get_credentials_for_source.return_value = {"password": "db_secret"}
        errors = plugin.validate_config(
            {"url": "http://host", "username": "u", "_source_id": "my_calibre"},
            storage=mock_storage,
            user_id=1,
        )
        assert errors == []
        mock_storage.get_credentials_for_source.assert_called_once_with(1, "my_calibre")

    def test_password_missing_from_config_and_db_fails(
        self, plugin: CalibreWebPlugin
    ) -> None:
        mock_storage = Mock(spec=StorageManager)
        mock_storage.get_credentials_for_source.return_value = {}
        errors = plugin.validate_config(
            {"url": "http://host", "username": "u"},
            storage=mock_storage,
            user_id=1,
        )
        assert "'password' is required" in errors

    def test_credential_store_returning_none_does_not_crash(
        self, plugin: CalibreWebPlugin
    ) -> None:
        """A credential store returning None must not AttributeError.

        validate_config guards None from get_credentials_for_source so a stub or
        alternate store implementation cannot crash; the missing password is
        still reported as required.
        """
        mock_storage = Mock(spec=StorageManager)
        mock_storage.get_credentials_for_source.return_value = None
        errors = plugin.validate_config(
            {"url": "http://host", "username": "u"},
            storage=mock_storage,
            user_id=1,
        )
        assert "'password' is required" in errors


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

    @pytest.mark.parametrize(
        ("rating", "expected"),
        [
            ("0", None),
            ("6", 3),
            ("10", 5),
            ("8", 4),
            ("5", 5),
            ("4", 4),
            ("12", 5),
        ],
    )
    def test_rating_mapping(
        self,
        plugin: CalibreWebPlugin,
        config: dict[str, object],
        rating: str,
        expected: int | None,
    ) -> None:
        catalog = _feed(_entry(rating=rating))
        responses = [_empty_read_feed(), _xml_response(catalog)]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].rating == expected

    def test_rating_from_rating_scheme_category(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """A <category> whose scheme identifies it as a rating is read as one."""
        entry = _entry(
            extra=(
                '<category scheme="http://opds-spec.org/2010/catalog/ratings" '
                'term="8" label="8"/>'
            )
        )
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].rating == 4

    def test_rating_scheme_category_not_emitted_as_tag(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """A rating category's star label must not leak into the tag list."""
        entry = _entry(
            extra=(
                '<category scheme="http://opds-spec.org/2010/catalog/ratings" '
                'term="8" label="8"/>'
            )
        )
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert "tags" not in items[0].metadata

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

    def test_empty_library(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        responses = [_empty_read_feed(), _xml_response(_feed(""))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items == []

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

    def test_server_error_raises_source_error(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """A non-401 HTTP error on the main catalog feed surfaces as SourceError."""
        server_error = _xml_response("<html>boom</html>", status_code=500)
        responses = [_empty_read_feed(), server_error]
        with patch("requests.get", side_effect=responses):
            with pytest.raises(SourceError, match="Calibre-Web returned an error"):
                list(plugin.fetch(config))

    def test_network_error_raises_source_error(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        with patch(
            "requests.get",
            side_effect=requests.ConnectionError("refused"),
        ):
            with pytest.raises(SourceError, match="Failed to connect"):
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

    def test_progress_callback_invoked(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        catalog = _feed(
            _entry(entry_id="urn:uuid:p1", title="One")
            + _entry(entry_id="urn:uuid:p2", title="Two")
        )
        responses = [_empty_read_feed(), _xml_response(catalog)]
        callback = Mock()
        with patch("requests.get", side_effect=responses):
            list(plugin.fetch(config, progress_callback=callback))

        assert callback.call_count == 2
        assert callback.call_args_list[0].args == (1, None, "One")
        assert callback.call_args_list[1].args == (2, None, "Two")


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

    def test_schema_org_series_position_as_child_element(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """The position may appear as a child element instead of an attribute."""
        entry = _entry(
            entry_id="urn:uuid:series-child",
            title="The Two Towers",
            extra=(
                '<schema:Series schema:name="The Lord of the Rings">'
                "<schema:position>2</schema:position>"
                "</schema:Series>"
            ),
        )
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].metadata["series"] == "The Lord of the Rings"
        assert items[0].metadata["series_index"] == 2.0

    def test_schema_org_series_without_position(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """A series with no position yields a name and no index."""
        entry = _entry(
            entry_id="urn:uuid:series-noindex",
            title="Standalone In A Series",
            extra='<schema:Series schema:name="Some Series"/>',
        )
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].metadata["series"] == "Some Series"
        assert "series_index" not in items[0].metadata

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


class TestCalibreWebAutoDiscovery:
    """The plugin must be picked up by the registry with no manual wiring."""

    def test_registry_discovers_calibre_web(self) -> None:
        from src.ingestion.registry import PluginRegistry

        PluginRegistry.reset_instance()
        registry = PluginRegistry.get_instance()
        registry.discover_plugins(force=True)
        try:
            plugin = registry.get_plugin("calibre_web")
            assert plugin is not None
            assert isinstance(plugin, CalibreWebPlugin)
            assert plugin.display_name == "Calibre-Web"
            assert plugin.content_types == [ContentType.BOOK]
            assert plugin.requires_api_key is True
            assert plugin.requires_network is True
        finally:
            PluginRegistry.reset_instance()


class TestCalibreWebEdgeCases:
    """QA edge-case probes added during issue #32 verification."""

    def test_non_ascii_title_and_author(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """Unicode titles/authors must round-trip unmangled."""
        entry = _entry(
            entry_id="urn:uuid:uni",
            title="Les Misérables — 第一卷",
            author="Émile Zola",
        )
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].title == "Les Misérables — 第一卷"
        assert items[0].author == "Émile Zola"

    def test_duplicate_entries_yielded_as_is(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """Two entries with the same id are both yielded (DB upsert dedupes)."""
        dup = _entry(entry_id="urn:uuid:dup", title="Same Book")
        catalog = _feed(dup + dup)
        responses = [_empty_read_feed(), _xml_response(catalog)]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert [i.id for i in items] == ["calibre:dup", "calibre:dup"]

    def test_read_shelf_pagination_collects_all_ids(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """Read-books shelf spanning multiple pages marks every read book."""
        read_page1 = _feed(
            _entry(entry_id="urn:uuid:r1", title="Read One"),
            next_href="/opds/readbooks?offset=1",
        )
        read_page2 = _feed(_entry(entry_id="urn:uuid:r2", title="Read Two"))
        catalog = _feed(
            _entry(entry_id="urn:uuid:r1", title="Read One")
            + _entry(entry_id="urn:uuid:r2", title="Read Two")
            + _entry(entry_id="urn:uuid:u1", title="Unread")
        )
        responses = [
            _xml_response(read_page1),
            _xml_response(read_page2),
            _xml_response(catalog),
        ]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        by_id = {i.id: i.status for i in items}
        assert by_id["calibre:r1"] == ConsumptionStatus.COMPLETED
        assert by_id["calibre:r2"] == ConsumptionStatus.COMPLETED
        assert by_id["calibre:u1"] == ConsumptionStatus.UNREAD

    def test_absolute_next_href_followed(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """A fully-qualified rel=next href must be requested verbatim."""
        page1 = _feed(
            _entry(entry_id="urn:uuid:a1", title="One"),
            next_href="http://localhost:8083/opds/new?offset=1",
        )
        page2 = _feed(_entry(entry_id="urn:uuid:a2", title="Two"))
        responses = [_empty_read_feed(), _xml_response(page1), _xml_response(page2)]
        with patch("requests.get", side_effect=responses) as mock_get:
            items = list(plugin.fetch(config))

        assert [i.title for i in items] == ["One", "Two"]
        assert (
            mock_get.call_args_list[2].args[0]
            == "http://localhost:8083/opds/new?offset=1"
        )

    def test_empty_response_body_raises_source_error(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """An empty (zero-byte) feed body is malformed XML, not a crash."""
        responses = [_empty_read_feed(), _xml_response("")]
        with patch("requests.get", side_effect=responses):
            with pytest.raises(SourceError, match="Failed to parse OPDS feed"):
                list(plugin.fetch(config))

    def test_rating_element_takes_precedence_over_category(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """When both a <rating> element and a numeric category exist, the
        explicit <rating> wins."""
        entry = _entry(rating="10", extra='<category label="2"/>')
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].rating == 5

    def test_non_numeric_rating_element_is_none(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """A <rating> with a non-numeric value yields no rating (ValueError)."""
        entry = _entry(rating="not-a-number")
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].rating is None

    def test_odd_numeric_rating_halved_and_rounded(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """An odd 0-10 rating (7) halves to 3.5 and rounds to 4 stars."""
        entry = _entry(rating="7")
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].rating == 4

    def test_source_id_override_applied_to_items(
        self, plugin: CalibreWebPlugin
    ) -> None:
        """A custom _source_id (multi-instance) is the source of every item."""
        multi_config = {
            "url": "http://localhost:8083",
            "username": "reader",
            "password": "secret",
            "verify_ssl": True,
            "_source_id": "home_library",
        }
        responses = [_empty_read_feed(), _xml_response(_feed(_entry()))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(multi_config))

        assert items[0].source == "home_library"

    def test_isbn_absent_when_only_non_isbn_identifier(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """A dc:identifier that is not an ISBN (e.g. a DOI) sets no isbn key."""
        entry = _entry(
            entry_id="urn:uuid:doi",
            title="DOI Only",
            extra="<dc:identifier>doi:10.1000/xyz123</dc:identifier>",
        )
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert "isbn" not in items[0].metadata

    def test_entry_without_id_yields_item_with_none_id(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """An entry with no <id> element yields an item with id=None, not a crash."""
        no_id = "<entry><title>No Id Book</title></entry>"
        responses = [_empty_read_feed(), _xml_response(_feed(no_id))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert len(items) == 1
        assert items[0].title == "No Id Book"
        assert items[0].id is None

    def test_entry_id_only_urn_prefix_yields_none_id(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """An id that is empty after stripping the urn: prefix yields id=None."""
        entry = _entry(entry_id="urn:", title="Empty Id Book")
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert len(items) == 1
        assert items[0].title == "Empty Id Book"
        assert items[0].id is None

    def test_non_numeric_series_position_omits_index(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """A non-numeric series position keeps the name but drops the index."""
        entry = _entry(
            entry_id="urn:uuid:series-tbd",
            title="Series With Bad Index",
            extra='<schema:Series schema:name="Some Series" schema:position="TBD"/>',
        )
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].metadata["series"] == "Some Series"
        assert "series_index" not in items[0].metadata

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

    def test_off_host_read_shelf_next_link_not_followed(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """SSRF guard also covers the read-books shelf pagination chain.

        A rel=next on the first read-books page pointing at a foreign host must
        be refused exactly as on the main catalog feed: the hostile host is
        never requested.
        """
        read_page1 = _feed(
            _entry(entry_id="urn:uuid:r1", title="Read One"),
            next_href="http://169.254.169.254/latest/meta-data/",
        )
        catalog = _feed(
            _entry(entry_id="urn:uuid:r1", title="Read One")
            + _entry(entry_id="urn:uuid:u1", title="Unread")
        )
        responses = [_xml_response(read_page1), _xml_response(catalog)]
        with patch("requests.get", side_effect=responses) as mock_get:
            items = list(plugin.fetch(config))

        by_id = {i.id: i.status for i in items}
        assert by_id["calibre:r1"] == ConsumptionStatus.COMPLETED
        assert by_id["calibre:u1"] == ConsumptionStatus.UNREAD
        # read page 1 + catalog only; the off-host read-shelf next link is not fetched.
        assert mock_get.call_count == 2
        requested_urls = [call.args[0] for call in mock_get.call_args_list]
        assert all("169.254.169.254" not in url for url in requested_urls)


class TestCalibreWebXmlHardening:
    """Tests that OPDS parsing rejects XXE / billion-laughs vectors."""

    def test_valid_feed_parses(self) -> None:
        root = _parse_opds_xml(f"{_FEED_HEADER}{_entry()}</feed>".encode())
        assert root.tag.endswith("feed")

    def test_doctype_rejected(self) -> None:
        """A DOCTYPE (entity-definition vector) must be refused."""
        payload = (
            b'<?xml version="1.0"?>' b'<!DOCTYPE feed [<!ENTITY lol "lol">]>' b"<feed/>"
        )
        with pytest.raises(ElementTree.ParseError):
            _parse_opds_xml(payload)


class TestCalibreWebRegression:
    """Regression tests for fixed Calibre-Web plugin bugs (issue #32)."""

    def test_numeric_tag_not_misread_as_rating_regression(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """Bug: a non-rating numeric category label was parsed as a star rating.

        Bug: Calibre-Web books carry numeric facets such as a publication year
        ("2008") as ordinary ``<category>`` elements with no rating scheme. An
        earlier implementation read any numeric category label as a star count,
        so a year-tagged book got a fabricated rating and the year was dropped
        from the tag list.

        Root cause: rating extraction accepted any numeric ``<category>`` label
        rather than requiring a category whose ``scheme`` marks it as a rating.

        Fix: only derive a rating from a ``<rating>`` element or a category
        whose scheme contains ``"rating"``; bare numeric labels are preserved as
        tags.
        """
        entry = _entry(
            entry_id="urn:uuid:year",
            title="Year Tagged",
            extra='<category label="2008"/>',
        )
        responses = [_empty_read_feed(), _xml_response(_feed(entry))]
        with patch("requests.get", side_effect=responses):
            items = list(plugin.fetch(config))

        assert items[0].rating is None
        assert items[0].metadata.get("tags") == ["2008"]

    def test_unread_book_yields_unread_status_regression(
        self, plugin: CalibreWebPlugin, config: dict[str, object]
    ) -> None:
        """Bug: a re-import could regress a completed book back to unread.

        Bug: status is resolved forward-only in src/storage/sqlite_db.py
        (_resolve_status_forward) so a re-sync never reverts a COMPLETED item.
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
