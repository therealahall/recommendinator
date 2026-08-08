"""Booting the web app in a test must not reach the developer's own files.

``src.web.app.app`` is a module attribute whose ``__getattr__`` boots the whole
application: ``get_app()`` -> ``create_app(None)`` -> ``load_config(None)``,
which resolves whatever config file the machine has, opens the database that
file names and runs the settings, credential and secret migrations against it.

Reaching that at module scope makes it happen during collection, before any
fixture in ``conftest.py`` has run — so the migrations write to the developer's
real database under the real credential key rather than the throwaway one. The
damage is done by the file existing, which is why this is a guard on the source
rather than an assertion inside any one test.

``tests.factories.booted_web_app`` is the supported way to get the app, and the
isolation it is trusted for is pinned here too.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import fields
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.cli.config import resolve_config_path
from src.storage.manager import StorageManager
from src.web.state import app_state
from tests.factories import booted_web_app

# parents[1] resolves /tests/test_web_app_import.py -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]

_BOOTING_MODULE = "src.web.app"
# Reading this attribute boots on the spot, through the module __getattr__.
_BOOTING_ATTRIBUTE = "app"
# These two boot when called, so importing one is only half of a boot.
_BOOTING_CALLABLES = ("get_app", "create_app")
_REPLACEMENT = "tests.factories.booted_web_app"


def _collected_modules() -> list[Path]:
    """Every Python file pytest imports, in all three trees that hold tests."""
    modules = [_REPO_ROOT / "conftest.py"]
    modules += (_REPO_ROOT / "tests").rglob("*.py")
    # Plugin-local tests live beside the plugin, so `src/` holds test modules too.
    modules += (
        path
        for path in (_REPO_ROOT / "src").rglob("*.py")
        if path.name.startswith("test_") or path.name == "conftest.py"
    )
    # Gitignored, so a plain clone has no such directory — but a private
    # plugin's tests are collected on demand and boot the app just as hard.
    private_plugins = _REPO_ROOT / "private"
    if private_plugins.exists():
        modules += private_plugins.rglob("*.py")
    return sorted(path for path in modules if "__pycache__" not in path.parts)


def _import_time_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Yield the nodes under ``node`` that are evaluated when it is imported.

    Class bodies count: they execute during collection like any other
    module-level statement. A function *body* does not — a call there runs when
    a test calls it, with the conftest fixtures already in place — but its
    decorators and its argument defaults do: those are evaluated by the ``def``
    statement itself, so a boot in one happens during collection like any
    other. Only the call half of ``_boot_line`` narrows to these; reading the
    booting attribute is flagged in any scope.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for evaluated in (
                *getattr(child, "decorator_list", []),
                *child.args.defaults,
                *(default for default in child.args.kw_defaults if default is not None),
            ):
                yield evaluated
                yield from _import_time_nodes(evaluated)
            continue
        yield child
        yield from _import_time_nodes(child)


def _attribute_path(node: ast.Attribute) -> str:
    """Render ``a.b.c`` from a chain of attribute accesses, ``""`` if dynamic."""
    parts = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return ""
    parts.append(current.id)
    return ".".join(reversed(parts))


def _called_name(node: ast.Call) -> str:
    """Render the dotted name ``node`` calls, ``""`` when it is an expression."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return _attribute_path(node.func)
    return ""


def _module_aliases(tree: ast.AST) -> set[str]:
    """Every name bound to the booting module, in each spelling that binds one.

    ``import src.web.app`` binds the dotted path itself, ``as`` binds a bare
    name, and ``from src.web import app`` binds the module under its own name —
    that last one is already how ``src/ingestion/sources/_isolation`` reaches
    the module. All three then read the booting attribute off a name that is
    not the literal import path.
    """
    package, module_name = _BOOTING_MODULE.rsplit(".", 1)
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bound = [alias for alias in node.names if alias.name == _BOOTING_MODULE]
        elif isinstance(node, ast.ImportFrom) and node.module == package:
            bound = [alias for alias in node.names if alias.name == module_name]
        else:
            continue
        aliases.update(alias.asname or alias.name for alias in bound)
    return aliases


def _boot_line(source: str) -> int | None:
    """Return the first line of *source* that boots the app, or None.

    Reading the ``app`` attribute boots on the spot with no seam to patch, so
    it is flagged in any scope. ``create_app``/``get_app`` are flagged only
    where they are *called* at import time: ``tests/factories.py`` calls
    ``create_app`` from inside its helper, over patched I/O, and that is the
    supported way in.

    Parsed rather than searched: this module and ``tests/factories.py`` both
    name the spellings in prose, and a text match cannot tell those from the
    real thing.
    """
    tree = ast.parse(source)
    aliases = _module_aliases(tree)
    booting_attributes = {f"{alias}.{_BOOTING_ATTRIBUTE}" for alias in aliases}
    boot_calls = {f"{alias}.{name}" for alias in aliases for name in _BOOTING_CALLABLES}

    boots: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _BOOTING_MODULE:
            for alias in node.names:
                if alias.name == _BOOTING_ATTRIBUTE:
                    boots.append(node.lineno)
                elif alias.name in _BOOTING_CALLABLES:
                    boot_calls.add(alias.asname or alias.name)
        elif isinstance(node, ast.Attribute):
            if _attribute_path(node) in booting_attributes:
                boots.append(node.lineno)

    boots += [
        node.lineno
        for node in _import_time_nodes(tree)
        if isinstance(node, ast.Call) and _called_name(node) in boot_calls
    ]
    return min(boots, default=None)


def test_no_collected_module_boots_the_app_at_import_time() -> None:
    """A module that boots the app boots it during collection, for everyone."""
    offenders = {
        str(path.relative_to(_REPO_ROOT)): line
        for path in _collected_modules()
        if (line := _boot_line(path.read_text(encoding="utf-8"))) is not None
    }

    assert offenders == {}, (
        f"These modules boot the app at import time; use {_REPLACEMENT} instead: "
        f"{offenders}"
    )


def test_the_scan_reaches_every_tree_pytest_collects() -> None:
    """The other half of the guard above: an empty corpus finds no offender.

    A typo in either ``rglob`` pattern, or this file moving up or down a
    directory, stops the scan reaching a whole tree with the suite still green.
    One real file per tree is what fails on that rather than on a constant.
    """
    scanned = {path.relative_to(_REPO_ROOT).as_posix() for path in _collected_modules()}

    assert "conftest.py" in scanned
    assert "tests/test_web_app_import.py" in scanned
    # Plugin-local tests are the tree a ``tests/``-only scan drops silently.
    assert "src/ingestion/sources/_isolation/test_isolation.py" in scanned
    assert not [path for path in scanned if "__pycache__" in path]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("from src.web.app import app\n", 1, id="attribute-import"),
        # Both spellings the repository actually had before the guard existed.
        pytest.param(
            "from src.web.app import app as web_app\n", 1, id="aliased-import"
        ),
        pytest.param(
            "from src.web.app import create_app\napp = create_app()\n",
            2,
            id="create_app-called",
        ),
        pytest.param(
            "from src.web.app import get_app\napp = get_app()\n",
            2,
            id="get_app-called",
        ),
        pytest.param(
            "from src.web.app import create_app as boot\napp = boot()\n",
            2,
            id="aliased-call",
        ),
        pytest.param(
            "import src.web.app\napp = src.web.app.app\n",
            2,
            id="dotted-attribute",
        ),
        # Every other spelling that binds the module and reads the same
        # attribute off it. The first is the one a collected module already
        # uses to reach src.web.app for an unrelated function.
        pytest.param(
            "from src.web import app\nweb_app = app.app\n",
            2,
            id="module-imported-by-name",
        ),
        pytest.param(
            "import src.web.app as web\napp = web.app\n",
            2,
            id="module-aliased",
        ),
        pytest.param(
            "import src.web.app\napp = src.web.app.get_app()\n",
            2,
            id="dotted-call",
        ),
        pytest.param(
            "import src.web.app as web\napp = web.create_app()\n",
            2,
            id="aliased-module-call",
        ),
        # Scope: the attribute boots wherever it is read, a call does not.
        pytest.param(
            "def boot():\n    from src.web.app import app\n    return app\n",
            2,
            id="attribute-imported-in-a-function",
        ),
        pytest.param(
            "import src.web.app\n\n\ndef boot():\n    return src.web.app.app\n",
            5,
            id="attribute-read-in-a-function",
        ),
        pytest.param(
            "from src.web.app import create_app\n\n\nclass T:\n    app = create_app()\n",
            5,
            id="class-body-call",
        ),
        # The three positions a `def` evaluates itself, so the deferral its
        # body gets does not reach them.
        pytest.param(
            "from src.web.app import create_app\n\n\n"
            "@pytest.fixture(params=[create_app()])\ndef app():\n    return None\n",
            4,
            id="call-in-a-decorator",
        ),
        pytest.param(
            "from src.web.app import create_app\n\n\n"
            "def boot(app=create_app()):\n    return app\n",
            4,
            id="call-in-an-argument-default",
        ),
        pytest.param(
            "from src.web.app import create_app\n\n\n"
            "def boot(*, app=create_app()):\n    return app\n",
            4,
            id="call-in-a-keyword-only-default",
        ),
        # The sanctioned spelling: tests/factories.py imports create_app and
        # calls it from inside the helper, where the fixtures are already up.
        pytest.param(
            "from src.web.app import create_app\n\n\ndef boot():\n"
            "    return create_app()\n",
            None,
            id="called-in-a-function",
        ),
        pytest.param(
            "from src.web.app import create_app\n", None, id="imported-not-called"
        ),
        # Binding the module is not the offence; reading ``app`` off it is.
        pytest.param(
            "from src.web import app\napp.configure_logging({})\n",
            None,
            id="another-attribute-off-the-module",
        ),
        pytest.param("from src.web.state import app_state\n", None, id="other-module"),
        pytest.param('"""from src.web.app import app"""\n', None, id="in-prose"),
    ],
)
def test_the_detector_matches_every_boot_and_nothing_else(
    source: str, expected: int | None
) -> None:
    """Without this the guard above would pass just as well detecting nothing."""
    assert _boot_line(source) == expected


def test_the_supported_helper_ignores_the_config_file_the_process_would_find(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``booted_web_app`` records the example config, whatever is on disk.

    ``create_app`` resolves the config path independently of ``load_config``,
    so patching the loader alone still records the file the process finds — on
    a developer's machine the one holding real API keys, which the config
    watcher and ``reload_config`` then go on to read. The decoy stands in for
    it, so this holds on a machine that has no config of its own.
    """
    decoy = tmp_path / "config" / "config.yaml"
    decoy.parent.mkdir()
    decoy.touch()
    monkeypatch.chdir(tmp_path)

    # The negative control: this is the path an unpatched boot would record,
    # and without it the assertion below passes on the fallback either way.
    assert resolve_config_path().resolve() == decoy.resolve()

    with booted_web_app(MagicMock(spec=StorageManager), {}):
        assert app_state.config_path == str(Path("config/example.yaml").resolve())


def test_the_supported_helper_restores_app_state_when_the_boot_raises() -> None:
    """A boot that dies part-way leaves ``app_state`` as the helper found it.

    ``app_state`` is a module-level singleton, so a half-populated one outlives
    the test that caused it and fails somewhere unrelated later in the session.
    ``create_conversation_engine`` is the raise point because it is one of the
    few steps that run *after* ``create_app`` starts assigning to ``app_state``
    — a raise from any earlier step restores a state nothing had changed yet.
    """
    saved = {f.name: getattr(app_state, f.name) for f in fields(app_state)}
    mid_boot = {}

    def explode(**_: object) -> None:
        mid_boot.update(config=app_state.config, storage=app_state.storage)
        raise RuntimeError

    with (
        patch("src.web.app.create_conversation_engine", side_effect=explode),
        pytest.raises(RuntimeError),
        # A truthy client is what makes create_app take the branch that raises.
        booted_web_app(MagicMock(spec=StorageManager), {}, (MagicMock(), None, None)),
    ):
        pass

    # Without this the assertion below would hold on a boot that had assigned
    # nothing yet, and pass with the restore deleted.
    assert mid_boot["config"] is not saved["config"]
    assert mid_boot["storage"] is not saved["storage"]
    assert {f.name: getattr(app_state, f.name) for f in fields(app_state)} == saved
