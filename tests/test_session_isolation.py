"""Tests for the repository-wide isolation fixtures in the repository-root conftest.

The fixtures are autouse and ordinary function-scoped — every test gets its own
setup and teardown. "Repository-wide" is about where they are *defined*, not
pytest's ``scope=`` argument: one definition, at the repository root, above every
tree pytest collects from (``tests/``, ``src/``, and ``private/`` on demand).

That the fixtures work is proved where it matters — a plugin-local test under
``src/ingestion/sources/_isolation/`` builds a real ``StorageManager`` and calls
``configure_logging`` from a tree that has no conftest of its own. What is
pinned here is that single point of definition, which that test cannot see. A
second copy of any of the fixtures lower down is the drift this guards against —
it would shadow the root one for its own subtree and could stop matching it.

Drift can arrive from either kind of file pytest reads fixtures out of — a
lower ``conftest.py``, or a ``test_*.py`` module redefining the fixture for
itself — so the scan below covers both. A module-level copy shadows the root
one just as completely as a conftest-level one, and is easier to miss.

The handler-stripping helper is exercised directly because it is the fixture's
fall-back for a code path that opens the production log without going through
``src.web.app.configure_logging``, and nothing else reaches it.

The credential-key tests establish that the redirect is load-bearing rather than
decorative: with the environment variable dropped, a real ``StorageManager``
writes the encryption key next to its database — which, for a plugin test built
on the default database path, is the developer's own ``data/`` directory.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

import conftest
from src.ingestion import registry
from src.storage.manager import StorageManager

# parents[1] resolves /tests/test_session_isolation.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_ROOT_CONFTEST = _REPO_ROOT / "conftest.py"

_ISOLATION_FIXTURES = (
    "_isolate_production_log_handlers",
    "host_timezone",
    "_isolate_credential_key",
)

# The two kinds of file pytest reads fixture definitions out of. Conftests alone
# would leave the module-level redefinition uncovered, which is the shape this
# guard was shipped already carrying.
_FIXTURE_SOURCE_PATHSPECS = ("*conftest.py", "*test_*.py")


def _shipped_fixture_sources() -> list[Path]:
    """Return every conftest and test module git would commit: tracked, plus untracked.

    Gitignored trees are out of view by construction, which is the same blind
    spot ``tests/test_repository_self_contained.py`` accepts: ``private/`` is
    not in this repository, so a fixture defined inside it cannot be asserted
    about here. A tracked file deleted in the working tree is still in the
    index, so the listing is filtered to what is actually on disk.
    """
    listing = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *_FIXTURE_SOURCE_PATHSPECS,
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    candidates = {_REPO_ROOT / name for name in listing.split("\0") if name}
    return sorted(path for path in candidates if path.is_file())


class TestSessionIsolationFixtureScope:
    """The isolation fixtures are defined once, at the root, above every tree."""

    def test_each_fixture_is_defined_only_by_the_root_conftest(self) -> None:
        """No conftest and no test module redefines a fixture the root provides."""
        sources = _shipped_fixture_sources()
        assert _ROOT_CONFTEST in sources
        # Named so a pathspec that stops matching test modules fails here rather
        # than making the scan below vacuously clean.
        assert Path(__file__).resolve() in sources

        for fixture_name in _ISOLATION_FIXTURES:
            definers = [
                path.relative_to(_REPO_ROOT)
                for path in sources
                if f"def {fixture_name}(" in path.read_text(encoding="utf-8")
            ]
            assert definers == [Path("conftest.py")], (
                f"{fixture_name} must be defined only by the repository-root "
                f"conftest; found it in {definers}"
            )

    def test_the_loaded_conftest_module_is_the_repository_root_file(self) -> None:
        """The fixtures pytest is using come from the file this suite asserts on."""
        assert conftest.__file__ is not None
        assert Path(conftest.__file__).resolve() == _ROOT_CONFTEST
        for fixture_name in _ISOLATION_FIXTURES:
            assert hasattr(conftest, fixture_name)

    def test_pytest_roots_the_session_where_the_conftest_lives(
        self, pytestconfig: pytest.Config
    ) -> None:
        """rootdir is the conftest's directory, so it is loaded for any collected path.

        pytest gathers conftests from the cut-off directory (rootdir, absent an
        explicit ``--confcutdir``) down to each collected file. Root and rootdir
        being the same directory is what makes the fixtures apply to a test at
        any depth, including one run on demand out of ``private/``.
        """
        assert pytestconfig.rootpath == _REPO_ROOT
        assert pytestconfig.inipath == _REPO_ROOT / "pyproject.toml"

    def test_the_conftest_sits_above_every_tree_tests_are_collected_from(
        self, pytestconfig: pytest.Config
    ) -> None:
        """``tests/``, ``src/`` and the registry's ``private/`` all sit under the root.

        The private path is derived the way ``PluginRegistry`` derives it, so
        this fails if private plugins ever move out from under the conftest.
        """
        assert list(pytestconfig.getini("testpaths")) == ["tests", "src"]

        registry_file = Path(registry.__file__).resolve()
        private_plugins = registry_file.parents[2] / "private" / "plugins"

        for tree in (_REPO_ROOT / "tests", _REPO_ROOT / "src", private_plugins):
            assert tree.is_relative_to(_ROOT_CONFTEST.parent)


class TestCredentialKeyRedirectIsLoadBearing:
    """What the redirect prevents, shown by dropping it for one test.

    Dropping the variable the autouse fixture sets is the closest a test can get
    to running without the fixture, and it is what makes the plugin-local
    assertion discriminating: the key really does follow the database directory
    when nothing redirects it.
    """

    def test_without_the_redirect_the_key_lands_beside_the_database(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unredirected StorageManager writes its key into the database directory."""
        monkeypatch.delenv("RECOMMENDINATOR_KEY_PATH")
        database_dir = tmp_path / "data"

        storage = StorageManager(sqlite_path=database_dir / "library.db")
        storage.save_credential(1, "steam", "api_key", "unredirected-secret")

        assert (database_dir / ".credential_key").exists()
        assert not (tmp_path / ".credential_key").exists()

    def test_with_the_redirect_the_key_never_reaches_the_database_directory(
        self, tmp_path: Path
    ) -> None:
        """With the fixture in force the same call writes only to the redirect path."""
        database_dir = tmp_path / "data"

        storage = StorageManager(sqlite_path=database_dir / "library.db")
        storage.save_credential(1, "steam", "api_key", "redirected-secret")

        assert (tmp_path / ".credential_key").exists()
        assert not (database_dir / ".credential_key").exists()


class TestProductionLogHandlerStripping:
    """The fall-back that detaches a production log handler the patch missed."""

    def test_removes_and_closes_a_recommendations_log_handler(
        self, tmp_path: Path
    ) -> None:
        """A stripped handler is detached *and* closed, so it holds no open file."""
        handler = logging.FileHandler(tmp_path / "recommendations.log")
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            conftest._remove_production_log_handlers()

            assert handler not in root_logger.handlers
            assert handler.stream is None
        finally:
            root_logger.removeHandler(handler)
            handler.close()

    def test_leaves_handlers_for_other_files_attached(self, tmp_path: Path) -> None:
        """Only the production log is stripped — pytest's own handlers must survive."""
        unrelated = logging.FileHandler(tmp_path / "tests.log")
        root_logger = logging.getLogger()
        root_logger.addHandler(unrelated)
        try:
            conftest._remove_production_log_handlers()

            assert unrelated in root_logger.handlers
            assert unrelated.stream is not None
        finally:
            root_logger.removeHandler(unrelated)
            unrelated.close()
