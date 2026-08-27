"""Tests for sync source resolution."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from src.ingestion.plugin_base import ConfigField, SourcePlugin
from src.ingestion.registry import PluginRegistry
from src.ingestion.sync import execute_sync
from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.sources.service import (
    ResolvedInput,
    SourceConfigError,
    create_source,
    delete_source,
    get_available_sync_sources,
    redact_credentials,
    resolve_inputs,
    update_source_config_values,
)
from src.storage.manager import StorageManager


def _resolve_one(
    source_id: str, config: dict[str, Any], storage: StorageManager
) -> ResolvedInput | None:
    """One source resolved the way both interfaces do it: resolve, then filter."""
    return next(
        (
            entry
            for entry in resolve_inputs(config, storage=storage)
            if entry.source_id == source_id
        ),
        None,
    )


class FakeBookPlugin(SourcePlugin):
    """Fake book plugin for resolve_inputs testing."""

    @property
    def name(self) -> str:
        return "fake_books"

    @property
    def display_name(self) -> str:
        return "Fake Books"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.BOOK]

    @property
    def requires_api_key(self) -> bool:
        return False

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(name="path", field_type=str, required=True),
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        errors = []
        if not config.get("path"):
            errors.append("'path' is required")
        return errors

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        yield ContentItem(
            id="book_1",
            title="Fake Book",
            content_type=ContentType.BOOK,
            status=ConsumptionStatus.COMPLETED,
            source=self.get_source_identifier(config),
        )


class FakeGamePlugin(SourcePlugin):
    """Fake game plugin for resolve_inputs testing."""

    @property
    def name(self) -> str:
        return "fake_games"

    @property
    def display_name(self) -> str:
        return "Fake Games"

    @property
    def content_types(self) -> list[ContentType]:
        return [ContentType.VIDEO_GAME]

    @property
    def requires_api_key(self) -> bool:
        return True

    def get_config_schema(self) -> list[ConfigField]:
        return [
            ConfigField(name="api_key", field_type=str, required=True, sensitive=True),
            ConfigField(
                name="url",
                field_type=str,
                required=False,
                default="http://localhost:7878",
                credential_bound=True,
            ),
            ConfigField(name="label", field_type=str, required=False),
        ]

    def validate_config(self, config: dict[str, Any], **kwargs: Any) -> list[str]:
        errors = []
        if not config.get("api_key"):
            errors.append("'api_key' is required")
        return errors

    def fetch(self, config: dict[str, Any]) -> Iterator[ContentItem]:
        yield ContentItem(
            id="game_1",
            title="Fake Game",
            content_type=ContentType.VIDEO_GAME,
            status=ConsumptionStatus.UNREAD,
            source=self.get_source_identifier(config),
        )


class RecordingGamePlugin(FakeGamePlugin):
    """Keeps every config and storage it was asked to validate against."""

    def __init__(self) -> None:
        self.validated: list[tuple[dict[str, Any], StorageManager | None]] = []

    def validate_config(
        self,
        config: dict[str, Any],
        storage: StorageManager | None = None,
        user_id: int = 1,
    ) -> list[str]:
        self.validated.append((dict(config), storage))
        return super().validate_config(config)


@pytest.fixture()
def _registry_with_fakes() -> Iterator[None]:
    """Set up a registry with fake plugins for testing."""
    registry = PluginRegistry.get_instance()
    registry._discovered = True
    registry._plugins.clear()
    registry._import_errors.clear()
    registry.register(FakeBookPlugin())
    registry.register(FakeGamePlugin())
    yield
    PluginRegistry.reset_instance()


@pytest.mark.usefixtures("_registry_with_fakes")
class TestResolveInputs:
    """Tests for resolve_inputs function."""

    def test_basic_resolution(self) -> None:
        """Test resolving a single enabled input."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/books.csv",
                },
            }
        }

        resolved = resolve_inputs(config)

        assert len(resolved) == 1
        assert resolved[0].source_id == "my_books"
        assert resolved[0].plugin.name == "fake_books"
        assert resolved[0].config["path"] == "/data/books.csv"

    def test_mixed_enabled_disabled(self) -> None:
        """Test with a mix of enabled and disabled entries."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/books.csv",
                },
                "my_games": {
                    "plugin": "fake_games",
                    "enabled": False,
                    "api_key": "test",
                },
                "more_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/more.csv",
                },
            }
        }

        resolved = resolve_inputs(config)

        assert len(resolved) == 2
        source_ids = {entry.source_id for entry in resolved}
        assert source_ids == {"my_books", "more_books"}


@pytest.mark.usefixtures("_registry_with_fakes")
class TestSourceIdPropagation:
    """Tests for _source_id propagation to ContentItem.source."""

    def test_source_id_in_fetched_items(self) -> None:
        """Test that _source_id propagates to ContentItem.source via fetch."""
        config = {
            "inputs": {
                "fiction_shelf": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/fiction.csv",
                },
            }
        }

        resolved = resolve_inputs(config)
        items = list(resolved[0].plugin.fetch(resolved[0].config))

        assert len(items) == 1
        assert items[0].source == "fiction_shelf"


@pytest.mark.usefixtures("_registry_with_fakes")
class TestGetAvailableSyncSources:
    """Tests for get_available_sync_sources function.

    The listing surface includes BOTH enabled and disabled sources so the
    UI can render disabled accordions in a muted state. ``resolve_inputs``
    is the gate that filters to enabled-only for sync execution.
    """

    def test_returns_all_sources_with_enabled_flag(self) -> None:
        """Both enabled and disabled sources are listed; enabled flag exposed."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/data/books.csv",
                },
                "my_games": {
                    "plugin": "fake_games",
                    "enabled": False,
                    "api_key": "test",
                },
            }
        }

        sources = get_available_sync_sources(config)
        by_id = {s.id: s for s in sources}

        assert by_id["my_books"].enabled is True
        assert by_id["my_books"].display_name == "My Books"
        assert by_id["my_books"].plugin_display_name == "Fake Books"
        assert by_id["my_games"].enabled is False


@pytest.mark.usefixtures("_registry_with_fakes")
class TestResolveInputsWithStorage:
    """Tests for resolve_inputs with DB credential injection."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_db_credential_injected_into_config(self, storage: StorageManager) -> None:
        """DB credentials override config-file values for sensitive fields."""
        config = {
            "inputs": {
                "my_games": {
                    "plugin": "fake_games",
                    "enabled": True,
                    "api_key": "config_key",
                }
            }
        }
        storage.credentials.save(1, "my_games", "api_key", "db_key")

        resolved = resolve_inputs(config, storage=storage)

        assert len(resolved) == 1
        assert resolved[0].config["api_key"] == "db_key"


@pytest.mark.usefixtures("_registry_with_fakes")
class TestResolveInputsWithDbSourceConfig:
    """Tests for resolve_inputs DB-backed source_configs precedence."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        """Create a StorageManager with a temp DB."""
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_db_config_overrides_yaml_when_migrated(
        self, storage: StorageManager
    ) -> None:
        """When source_configs has a row, DB values authoritative; yaml ignored."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/yaml/books.csv",
                },
            }
        }
        storage.sources.upsert(
            1, "my_books", "fake_books", {"path": "/db/books.csv"}, enabled=True
        )

        resolved = resolve_inputs(config, storage=storage)

        assert len(resolved) == 1
        assert resolved[0].config["path"] == "/db/books.csv"
        assert resolved[0].source_id == "my_books"

    def test_db_config_disabled_excludes_source(self, storage: StorageManager) -> None:
        """A migrated source with enabled=False is excluded even when yaml is enabled."""
        config = {
            "inputs": {
                "my_books": {
                    "plugin": "fake_books",
                    "enabled": True,
                    "path": "/yaml/books.csv",
                },
            }
        }
        storage.sources.upsert(
            1, "my_books", "fake_books", {"path": "/db/books.csv"}, enabled=False
        )

        resolved = resolve_inputs(config, storage=storage)

        assert resolved == []

    def test_db_only_source_resolves(self, storage: StorageManager) -> None:
        """A source that exists in DB but not yaml still resolves."""
        config: dict[str, Any] = {"inputs": {}}
        storage.sources.upsert(
            1, "books_only_in_db", "fake_books", {"path": "/db/x.csv"}, enabled=True
        )

        resolved = resolve_inputs(config, storage=storage)

        assert len(resolved) == 1
        assert resolved[0].source_id == "books_only_in_db"
        assert resolved[0].config["path"] == "/db/x.csv"

    def test_db_config_merges_with_credentials(self, storage: StorageManager) -> None:
        """Sensitive creds from credentials table merge over DB config dict."""
        config: dict[str, Any] = {"inputs": {}}
        storage.sources.upsert(1, "my_games", "fake_games", {}, enabled=True)
        storage.credentials.save(1, "my_games", "api_key", "secret_from_creds")

        resolved = resolve_inputs(config, storage=storage)

        assert resolved[0].config["api_key"] == "secret_from_creds"

    def test_sources_resolve_in_id_order_whatever_the_hash_seed(
        self, storage: StorageManager
    ) -> None:
        """Regression: the resolve order came off a set, so it varied per process.

        The scheduler walks this list to decide what is due, and both interfaces
        print it, so YAML-only and DB-only ids must interleave by id.
        """
        config = {
            "inputs": {
                "zulu": {"plugin": "fake_books", "enabled": True, "path": "/z.csv"},
                "bravo": {"plugin": "fake_books", "enabled": True, "path": "/b.csv"},
            }
        }
        storage.sources.upsert(
            1, "yankee", "fake_books", {"path": "/y.csv"}, enabled=True
        )
        storage.sources.upsert(
            1, "alpha", "fake_books", {"path": "/a.csv"}, enabled=True
        )

        resolved = resolve_inputs(config, storage=storage)

        assert [entry.source_id for entry in resolved] == [
            "alpha",
            "bravo",
            "yankee",
            "zulu",
        ]

    def test_db_config_with_unregistered_plugin_is_skipped(
        self, storage: StorageManager
    ) -> None:
        """DB row referencing an unknown plugin is silently skipped (not crashed).

        Regression scenario: a plugin gets renamed or removed after the user
        migrated their source. The DB row still references the old plugin
        name. ``resolve_inputs`` must log + skip rather than raise.
        """
        config: dict[str, Any] = {"inputs": {}}
        storage.sources.upsert(
            1, "ghost_source", "this_plugin_no_longer_exists", {}, enabled=True
        )

        resolved = resolve_inputs(config, storage=storage)

        assert resolved == []


@pytest.mark.usefixtures("_registry_with_fakes")
class TestCredentialBoundUpdates:
    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @pytest.fixture()
    def migrated(self, storage: StorageManager) -> StorageManager:
        storage.sources.upsert(
            1, "my_games", "fake_games", {"url": "http://localhost:7878"}, enabled=True
        )
        storage.credentials.save(1, "my_games", "api_key", "issued-for-localhost")
        return storage

    @staticmethod
    def _update(storage: StorageManager, values: dict[str, Any]) -> None:
        update_source_config_values("my_games", FakeGamePlugin(), storage, values)

    def test_repointing_the_url_is_refused_and_changes_nothing(
        self, migrated: StorageManager
    ) -> None:
        with pytest.raises(SourceConfigError) as refusal:
            self._update(migrated, {"url": "http://attacker.example"})

        assert refusal.value.kind == "credential_move"
        assert refusal.value.message == (
            "Changing 'url' points this source at a different host. Clear its "
            "stored 'api_key' first, then save this change and enter the "
            "credential the new host expects."
        )
        assert migrated.credentials.get(1, "my_games", "api_key") == (
            "issued-for-localhost"
        )
        row = migrated.sources.get(1, "my_games")
        assert row is not None
        assert row["config"]["url"] == "http://localhost:7878"

    @pytest.mark.parametrize(
        "url",
        [
            "https://localhost:7878",
            "http://localhost:7878/radarr",
        ],
    )
    def test_a_rewrite_that_keeps_the_host_keeps_the_secret(
        self, migrated: StorageManager, url: str
    ) -> None:
        """Scheme, trailing slash and path do not decide who receives it."""
        self._update(migrated, {"url": url})

        assert migrated.credentials.get(1, "my_games", "api_key") == (
            "issued-for-localhost"
        )
        row = migrated.sources.get(1, "my_games")
        assert row is not None and row["config"]["url"] == url

    @pytest.mark.parametrize(
        "url", ["http://other.example:7878", "http://localhost:9999"]
    )
    def test_a_new_host_or_port_is_a_move(
        self, migrated: StorageManager, url: str
    ) -> None:
        with pytest.raises(SourceConfigError, match="different host"):
            self._update(migrated, {"url": url})

    def test_the_move_is_allowed_once_the_secret_is_gone(
        self, migrated: StorageManager
    ) -> None:
        migrated.credentials.delete(1, "my_games", "api_key")

        self._update(migrated, {"url": "http://attacker.example"})

        row = migrated.sources.get(1, "my_games")
        assert row is not None
        assert row["config"]["url"] == "http://attacker.example"

    def test_an_unset_bound_field_is_measured_from_the_plugin_default(
        self, storage: StorageManager
    ) -> None:
        storage.sources.upsert(1, "my_games", "fake_games", {}, enabled=True)
        storage.credentials.save(1, "my_games", "api_key", "issued-for-the-default")

        with pytest.raises(SourceConfigError, match="different host"):
            self._update(storage, {"url": "http://attacker.example"})

        self._update(storage, {"url": "https://localhost:7878"})

        assert storage.credentials.get(1, "my_games", "api_key") == (
            "issued-for-the-default"
        )

    def test_a_non_binding_field_leaves_the_secret_alone(
        self, migrated: StorageManager
    ) -> None:
        self._update(migrated, {"label": "Games"})

        assert migrated.credentials.get(1, "my_games", "api_key") == (
            "issued-for-localhost"
        )

    def test_a_new_source_does_not_inherit_an_orphaned_secret(
        self, storage: StorageManager
    ) -> None:
        """Delete-then-recreate must not resurrect a secret under a new url."""
        storage.credentials.save(1, "my_games", "api_key", "issued-for-localhost")

        create_source(
            "my_games",
            "fake_games",
            {"url": "http://attacker.example"},
            storage,
        )

        assert storage.credentials.get(1, "my_games", "api_key") is None


@pytest.mark.usefixtures("_registry_with_fakes")
class TestUnreadableUrlWalksTheCredentialRegression:
    """Regression: two accepted writes walked the api key to another host.

    An unparseable port read as "addresses nobody", so neither the write onto
    it nor the write off it was a move. Unreadable is its own answer now.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @staticmethod
    def _update(storage: StorageManager, values: dict[str, Any]) -> None:
        update_source_config_values("my_games", FakeGamePlugin(), storage, values)

    def test_neither_step_of_the_walk_is_accepted(
        self, storage: StorageManager
    ) -> None:
        storage.sources.upsert(
            1, "my_games", "fake_games", {"url": "http://localhost:7878"}, enabled=True
        )
        storage.credentials.save(1, "my_games", "api_key", "issued-for-localhost")

        with pytest.raises(SourceConfigError) as onto:
            self._update(storage, {"url": "http://attacker.example:99999"})

        # A config.yaml entry reaches the same state without a write: migration
        # copies the url in unvalidated.
        storage.sources.upsert(
            1,
            "my_games",
            "fake_games",
            {"url": "http://attacker.example:99999"},
            enabled=True,
        )

        with pytest.raises(SourceConfigError) as off:
            self._update(storage, {"url": "http://attacker.example"})

        assert onto.value.kind == "credential_move"
        assert off.value.kind == "credential_move"
        assert storage.credentials.get(1, "my_games", "api_key") == (
            "issued-for-localhost"
        )


@pytest.mark.usefixtures("_registry_with_fakes")
class TestDeleteSourceOrphanedCredentialsRegression:
    """Regression: removing a source left encrypted rows behind.

    Bug: cleanup iterated the registered plugin's sensitive fields, so an
    unregistered plugin skipped it entirely and a field that stopped being
    sensitive was missed. Fix: delete every row keyed by source id.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_unregistered_plugin_leaves_no_credential_row(
        self, storage: StorageManager
    ) -> None:
        storage.sources.upsert(
            1, "ghost", "this_plugin_no_longer_exists", {}, enabled=True
        )
        storage.credentials.save(1, "ghost", "api_key", "still-valid-upstream")

        delete_source("ghost", storage, {})

        assert storage.credentials.get_for_source(1, "ghost") == {}
        assert storage.sources.get(1, "ghost") is None

    def test_a_field_no_longer_marked_sensitive_is_removed_too(
        self, storage: StorageManager
    ) -> None:
        """``fake_games`` has no ``legacy_token`` field; the row exists anyway."""
        storage.sources.upsert(1, "my_games", "fake_games", {}, enabled=True)
        storage.credentials.save(1, "my_games", "api_key", "secret")
        storage.credentials.save(1, "my_games", "legacy_token", "was-sensitive-once")

        delete_source("my_games", storage, {})

        assert storage.credentials.get_for_source(1, "my_games") == {}

    def test_another_sources_credentials_survive(self, storage: StorageManager) -> None:
        storage.sources.upsert(1, "my_games", "fake_games", {}, enabled=True)
        storage.credentials.save(1, "my_games", "api_key", "secret")
        storage.credentials.save(1, "other", "api_key", "untouched")

        delete_source("my_games", storage, {})

        assert storage.credentials.get(1, "other", "api_key") == "untouched"


class TestWriteValidationNeverSeesTheDecryptedSecretRegression:
    """Reported: the write door validated a config carrying the decrypted
    credential, and those messages reach the wire now. A plugin quoting a
    value it was handed would answer an HTTP body holding the secret.
    """

    def test_neither_validated_config_carries_the_stored_credential(
        self, tmp_path: Path
    ) -> None:
        """``storage`` still goes through, so a plugin can ask whether it is set."""
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        storage.sources.upsert(
            1, "my_games", "fake_games", {"label": "Games"}, enabled=True
        )
        storage.credentials.save(1, "my_games", "api_key", "issued-for-localhost")
        plugin = RecordingGamePlugin()

        update_source_config_values("my_games", plugin, storage, {"label": "Shelf"})

        assert len(plugin.validated) == 2
        assert all(config.get("api_key") is None for config, _ in plugin.validated)
        assert all(seen is storage for _, seen in plugin.validated)


ROTATING_PLUGINS = ("gog", "epic_games", "trakt")
ROTATED_TOKEN = "rotated-during-this-sync"


def _rotates_on_fetch(real_plugin: SourcePlugin) -> SourcePlugin:
    """The real plugin class with only ``fetch`` replaced.

    Subclassed rather than stubbed so the config under test is built by the
    plugin's own ``transform_fields``, the step the source id was lost in.
    """

    class RotatesOnFetch(type(real_plugin)):  # type: ignore[misc,valid-type]
        def fetch(
            self, config: dict[str, Any], progress_callback: Any = None
        ) -> Iterator[ContentItem]:
            config["_on_credential_rotated"]("refresh_token", ROTATED_TOKEN)
            return iter(())

    return RotatesOnFetch()


@pytest.fixture()
def _real_registry() -> Iterator[None]:
    """The real plugins, whatever fakes an earlier test left registered."""
    PluginRegistry.reset_instance()
    PluginRegistry.get_instance().discover_plugins()
    yield
    PluginRegistry.reset_instance()


def _discovered_plugins() -> dict[str, SourcePlugin]:
    """Every plugin ``_real_registry`` discovered, never an empty mapping.

    A sweep over an undiscovered registry iterates nothing and passes, so the
    guard belongs here rather than in each caller that keeps forgetting it.
    """
    plugins = PluginRegistry.get_instance().get_all_plugins()
    assert plugins, "discovery found nothing, so the sweep proves nothing"
    return plugins


@pytest.fixture()
def _registry_with_rotating_doubles(_real_registry: None) -> Iterator[None]:
    """The real token-rotating plugins, with fetching stubbed out."""
    registry = PluginRegistry.get_instance()
    for plugin_name in ROTATING_PLUGINS:
        real_plugin = registry.get_plugin(plugin_name)
        assert real_plugin is not None
        # Swapped in place because ``register`` refuses a name already taken.
        # ``_real_registry`` discards this registry when the test ends.
        registry._plugins[plugin_name] = _rotates_on_fetch(real_plugin)
    yield


@pytest.mark.usefixtures("_real_registry")
class TestCreateSourceRefusesUncontainedPaths:
    """``roms`` scans a list of directories, and every entry must be contained.

    Its multi-entry ``paths`` was checked at plugin level only, never through
    the boundary an HTTP caller reaches it by.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def test_a_path_outside_the_allowed_roots_is_refused(
        self, storage: StorageManager
    ) -> None:
        with pytest.raises(SourceConfigError) as raised:
            create_source("leaky", "roms", {"paths": ["/etc"]}, storage)

        assert raised.value.kind == "invalid_values"
        assert "outside the allowed source roots" in raised.value.message
        assert storage.sources.get(1, "leaky") is None

    def test_a_path_under_an_allowed_root_is_accepted(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        games = tmp_path / "games"
        games.mkdir()

        create_source("my_roms", "roms", {"paths": [str(games)]}, storage)

        row = storage.sources.get(1, "my_roms")
        assert row is not None
        assert row["config"]["paths"] == [str(games)]

    def test_a_symlink_planted_in_the_root_cannot_reach_out_of_it(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        escape = tmp_path / "games"
        escape.symlink_to("/etc")

        with pytest.raises(SourceConfigError) as raised:
            create_source("linked", "roms", {"paths": [str(escape)]}, storage)

        assert "outside the allowed source roots" in raised.value.message

    def test_a_traversal_is_refused_before_the_path_is_probed_for_existence(
        self, storage: StorageManager, tmp_path: Path
    ) -> None:
        """The "not found" message is an oracle for anything the caller names."""
        with pytest.raises(SourceConfigError) as raised:
            create_source(
                "traversing", "roms", {"paths": [f"{tmp_path}/../secrets"]}, storage
            )

        assert "outside the allowed source roots" in raised.value.message
        assert "not found" not in raised.value.message


class TwoSecretGamePlugin(FakeGamePlugin):
    """``fake_games`` plus a second sensitive field."""

    def get_config_schema(self) -> list[ConfigField]:
        return [
            *super().get_config_schema(),
            ConfigField(
                name="password", field_type=str, required=False, sensitive=True
            ),
        ]


class TestRedactCredentials:
    """Edge cases for the redaction the sync door's 400 log line depends on.

    That door validates with the secret layered on, so scrubbing the plugin's
    message is the only thing between a stored key and the log file.
    """

    def test_every_occurrence_of_every_secret_goes(self) -> None:
        """One pass has to cover both fields and repeated mentions."""
        redacted = redact_credentials(
            "GET ?key=abc123 rejected; retry with abc123 or pw-9",
            TwoSecretGamePlugin(),
            {"api_key": "abc123", "password": "pw-9", "label": "Games"},
        )

        assert redacted == (
            "GET ?key=[redacted] rejected; retry with [redacted] or [redacted]"
        )

    def test_an_empty_secret_is_not_a_match(self) -> None:
        """``str.replace("", ...)`` would splice the marker between every char."""
        message = "'api_key' is required"

        assert redact_credentials(message, FakeGamePlugin(), {"api_key": ""}) == message

    def test_a_credential_bound_field_is_left_alone(self) -> None:
        """``url`` is bound to the credential, not one — the log wants it."""
        redacted = redact_credentials(
            "host http://sonarr.internal:9999 refused",
            FakeGamePlugin(),
            {"url": "http://sonarr.internal:9999"},
        )

        assert "http://sonarr.internal:9999" in redacted


@pytest.mark.usefixtures("_registry_with_rotating_doubles")
class TestRotatedCredentialSurvivesTheRealConfigAssemblyRegression:
    """Reported: a sync saved a rotated OAuth token under the plugin name.

    Bug: each rotating plugin's ``transform_config`` returned a fresh dict,
    dropping the injected ``_source_id``. The orphan survived deleting the
    source. Fix: ``transform_config`` is framework owned and restores it.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    def _sync_a_rotating_source(
        self, plugin_name: str, storage: StorageManager
    ) -> None:
        """Sync a DB-backed source through the production resolution path."""
        storage.sources.upsert(1, "work_games", plugin_name, {}, enabled=True)
        resolved = _resolve_one("work_games", {}, storage)
        assert resolved is not None

        execute_sync(
            plugin=resolved.plugin,
            plugin_config=resolved.config,
            storage_manager=storage,
        )

    @pytest.mark.parametrize("plugin_name", ROTATING_PLUGINS)
    def test_the_token_lands_under_the_source_id(
        self, plugin_name: str, storage: StorageManager
    ) -> None:
        self._sync_a_rotating_source(plugin_name, storage)

        assert (
            storage.credentials.get(1, "work_games", "refresh_token") == ROTATED_TOKEN
        )
        assert storage.credentials.get(1, plugin_name, "refresh_token") is None

    @pytest.mark.parametrize("plugin_name", ROTATING_PLUGINS)
    def test_the_next_sync_resolves_the_rotated_token(
        self, plugin_name: str, storage: StorageManager
    ) -> None:
        """SECURITY.md's promise: the operator never reconnects by hand."""
        self._sync_a_rotating_source(plugin_name, storage)

        resolved = _resolve_one("work_games", {}, storage)

        assert resolved is not None
        assert resolved.config["refresh_token"] == ROTATED_TOKEN

    def test_a_token_an_earlier_release_orphaned_stays_where_it_is(
        self, storage: StorageManager
    ) -> None:
        """Nothing migrates them, which is what SECURITY.md promises."""
        storage.credentials.save(1, "gog", "refresh_token", "orphaned-by-an-old-sync")

        self._sync_a_rotating_source("gog", storage)

        assert storage.credentials.get(1, "gog", "refresh_token") == (
            "orphaned-by-an-old-sync"
        )


@pytest.mark.usefixtures("_registry_with_fakes")
class TestRemovingASourceTakesItsStrandedTokenWithItRegression:
    """Reported: a token stranded under a plugin name outlived every source.

    Cause: deletion is keyed on the source id, and that row is not under one.
    Fix: the last source on a plugin takes it; a shared plugin keeps it.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        storage = StorageManager(sqlite_path=tmp_path / "test.db")
        storage.sources.upsert(1, "work_games", "fake_games", {}, enabled=True)
        storage.credentials.save(1, "fake_games", "api_key", "stranded-by-an-upgrade")
        return storage

    def test_the_last_source_on_the_plugin_takes_the_row_with_it(
        self, storage: StorageManager
    ) -> None:
        delete_source("work_games", storage, {"inputs": {}})

        assert storage.credentials.get(1, "fake_games", "api_key") is None

    def test_a_sibling_on_the_same_plugin_keeps_the_row(
        self, storage: StorageManager
    ) -> None:
        storage.sources.upsert(1, "home_games", "fake_games", {}, enabled=True)

        delete_source("work_games", storage, {"inputs": {}})

        assert storage.credentials.get(1, "fake_games", "api_key") == (
            "stranded-by-an-upgrade"
        )

    def test_a_yaml_sibling_keeps_the_row(self, storage: StorageManager) -> None:
        """The database is half the source list, so a sweep reading it alone lies."""
        config = {"inputs": {"home_games": {"plugin": "fake_games", "enabled": True}}}

        delete_source("work_games", storage, config)

        assert storage.credentials.get(1, "fake_games", "api_key") == (
            "stranded-by-an-upgrade"
        )

    def test_a_source_named_after_the_plugin_keeps_its_own_credential(
        self, storage: StorageManager
    ) -> None:
        """That row is not stranded at all — it is that source's, live."""
        config = {"inputs": {"fake_games": {"plugin": "fake_books", "enabled": True}}}

        delete_source("work_games", storage, config)

        assert storage.credentials.get(1, "fake_games", "api_key") == (
            "stranded-by-an-upgrade"
        )

    def test_the_deleted_sources_own_credentials_still_go(
        self, storage: StorageManager
    ) -> None:
        storage.credentials.save(1, "work_games", "api_key", "its-own")

        delete_source("work_games", storage, {"inputs": {}})

        assert storage.credentials.get_for_source(1, "work_games") == {}


class TestAPluginCannotDropAFrameworkConfigKey:
    """The guarantee the seven rotating plugins each broke independently."""

    def test_overriding_transform_config_is_refused_at_class_creation(self) -> None:
        with pytest.raises(TypeError, match="transform_fields"):

            class EighthPlugin(FakeBookPlugin):
                @classmethod
                def transform_config(cls, raw_config: dict[str, Any]) -> dict[str, Any]:
                    return {"path": raw_config.get("path")}

    @pytest.mark.usefixtures("_real_registry")
    def test_every_registered_plugin_keeps_the_source_id(self) -> None:
        for plugin in _discovered_plugins().values():
            transformed = type(plugin).transform_config({"_source_id": "my_source"})
            assert transformed["_source_id"] == "my_source"


class TestAPluginCannotRenameItsOwnSource:
    """The same guarantee on the other method that answers "what is this?"."""

    def test_overriding_get_source_identifier_is_refused_at_class_creation(
        self,
    ) -> None:
        with pytest.raises(TypeError, match="name property"):

            class RenamingPlugin(FakeBookPlugin):
                def get_source_identifier(
                    self, config: dict[str, Any] | None = None
                ) -> str:
                    return "somebody_elses_source"

    @pytest.mark.usefixtures("_real_registry")
    def test_every_registered_plugin_answers_with_the_source_id(self) -> None:
        for plugin in _discovered_plugins().values():
            assert plugin.get_source_identifier({"_source_id": "my_source"}) == (
                "my_source"
            )


@pytest.mark.usefixtures("_real_registry")
class TestARealPluginsItemsCarryTheSourceIdRegression:
    """Reported: a renamed Steam source's items were attributed to "steam".

    Bug: ``SteamPlugin.fetch`` re-transforms the config it was handed, and the
    plugin's transform dropped the injected ``_source_id``. Fix:
    ``transform_config`` is framework owned and restores it.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @patch("src.ingestion.sources.steam.steam.get_owned_games")
    def test_items_are_attributed_to_the_source_not_the_plugin(
        self, owned_games: Mock, storage: StorageManager
    ) -> None:
        owned_games.return_value = [
            {"appid": 7, "name": "A Game", "playtime_forever": 120}
        ]
        storage.sources.upsert(
            1,
            "work_games",
            "steam",
            {"api_key": "key", "steam_id": "76561198000000000"},
            enabled=True,
        )
        resolved = _resolve_one("work_games", {}, storage)
        assert resolved is not None

        execute_sync(
            plugin=resolved.plugin,
            plugin_config=resolved.config,
            storage_manager=storage,
        )

        assert {item.source for item in storage.get_content_items()} == {"work_games"}


@pytest.mark.usefixtures("_registry_with_rotating_doubles")
class TestTheCredentialOwnerIsWhateverTheSourceIsCalled:
    """Source ids from YAML skip the create-source charset check."""

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "test.db")

    @staticmethod
    def _sync_yaml_source(source_id: str, storage: StorageManager) -> None:
        config = {"inputs": {source_id: {"plugin": "gog", "enabled": True}}}
        resolved = _resolve_one(source_id, config, storage)
        assert resolved is not None

        execute_sync(
            plugin=resolved.plugin,
            plugin_config=resolved.config,
            storage_manager=storage,
        )

    @pytest.mark.parametrize(
        "source_id", ["my-gog", "Wörk Games 📚", "gog_2", "x" * 200]
    )
    def test_the_token_lands_under_the_id_verbatim(
        self, source_id: str, storage: StorageManager
    ) -> None:
        self._sync_yaml_source(source_id, storage)

        assert storage.credentials.get(1, source_id, "refresh_token") == ROTATED_TOKEN
        assert storage.credentials.get(1, "gog", "refresh_token") is None

    def test_ids_differing_only_in_case_do_not_share_a_token(
        self, storage: StorageManager
    ) -> None:
        """A NOCASE collation here would hand one source another's secret."""
        self._sync_yaml_source("Work_Games", storage)

        assert storage.credentials.get(1, "work_games", "refresh_token") is None
