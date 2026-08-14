"""Neither interface package may import the other, and shared work is shared.

Shared work inside one of them forces the other to import an interface to
reach it. Source CRUD, OAuth and export sat in ``src/web/`` for that reason.
"""

from __future__ import annotations

import ast
import importlib
import re
from collections.abc import Iterable, Mapping
from fnmatch import fnmatch
from itertools import dropwhile, takewhile
from pathlib import Path

import pytest

import src as src_package

_SRC_ROOT = Path(src_package.__file__).parent
_REPO_ROOT = _SRC_ROOT.parent
_PARITY_AGENT = _REPO_ROOT / ".claude" / "agents" / "parity-review.md"
_STEP_ZERO_OPENING = "If every changed file is under"

_CLI_PACKAGE = "src.cli"
_WEB_PACKAGE = "src.web"

#: Confined to the package that answers on it. ``pydantic`` is not here: the
#: content models use it, so it is not a web framework in this codebase.
_FRAMEWORKS = {
    "fastapi": _WEB_PACKAGE,
    "starlette": _WEB_PACKAGE,
    "click": _CLI_PACKAGE,
}

#: Each extracted name and its one home. A second definition would let the
#: import and the export drift while both still resolve. ``STATUS_MAP`` is
#: absent — ``storygraph_csv`` declares a different table — and pinned below.
_ONE_HOME = {
    "COMMON_COLUMNS": "src.models.templates",
    "CONTENT_TYPE_COLUMNS": "src.models.templates",
    "CREATOR_COLUMNS": "src.models.templates",
    "CREATOR_FIELD": "src.models.templates",
    "LIST_VALUED_COLUMNS": "src.models.templates",
    "STATUS_DISPLAY": "src.models.templates",
    "guard_csv_formula": "src.utils.csv_formula",
    "strip_csv_formula_guard": "src.utils.csv_formula",
}

_TABLE_HOMES = ("src.models.templates", "src.utils.csv_formula")

#: ``STATUS_MAP``'s two definers. StoryGraph's read/to-read vocabulary is a
#: genuinely different table, so it cannot fold into the home above — but a
#: third declaration is the drift the enrolment above exists to catch.
_STATUS_MAP_DEFINERS = {
    "src.ingestion.sources.storygraph_csv.storygraph_csv",
    "src.models.templates",
}


def _module_name(path: Path) -> str:
    parts = path.relative_to(_SRC_ROOT).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("src", *parts))


def _discover() -> tuple[dict[str, ast.Module], dict[str, str]]:
    """The package is what a relative import resolves against, and a module's
    name does not say whether it is one.
    """
    trees: dict[str, ast.Module] = {}
    packages: dict[str, str] = {}
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        if path.name.startswith("test_"):
            continue
        module = _module_name(path)
        trees[module] = ast.parse(path.read_text(encoding="utf-8"))
        packages[module] = (
            module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
        )
    return trees, packages


_TREES, _PACKAGE_OF = _discover()


def _absolute_module(node: ast.ImportFrom, package: str) -> str:
    """``node.module`` is ``None`` for ``from . import x``, and level 1
    resolves to *package* itself.
    """
    base = package.rsplit(".", node.level - 1)[0] if node.level else ""
    return ".".join(part for part in (base, node.module) if part)


def _import_statements(tree: ast.AST, package: str) -> list[tuple[list[str], int]]:
    """``from src import web`` reaches the module ``import src.web`` does, so
    the imported names are candidates too, least specific first.
    """
    statements: list[tuple[list[str], int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            statements.extend(([alias.name], node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported = _absolute_module(node, package)
            statements.append(
                (
                    [imported, *(f"{imported}.{alias.name}" for alias in node.names)],
                    node.lineno,
                )
            )
    return statements


def _is_within(imported: str, package: str) -> bool:
    return imported == package or imported.startswith(f"{package}.")


def _first_within(names: list[str], package: str) -> str | None:
    return next((name for name in names if _is_within(name, package)), None)


def _star_re_exports(
    trees: Mapping[str, ast.Module], packages: Mapping[str, str]
) -> list[tuple[str, str]]:
    """Pairs, not a mapping keyed by importer: an ``__init__`` with two star
    imports would keep only the last, dropping the first from every sweep.
    """
    return sorted(
        (module, _absolute_module(node, packages[module]))
        for module, tree in trees.items()
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "*" for alias in node.names)
    )


#: A home package re-exporting its own tables is not the drift: no second
#: definition, no path into a plugin.
_PLUGIN_TREES = ("src.enrichment.providers", "src.ingestion.sources")

_PLUGIN_STAR_RE_EXPORTS = [
    (package, source)
    for package, source in _star_re_exports(_TREES, _PACKAGE_OF)
    if any(_is_within(package, tree) for tree in _PLUGIN_TREES)
]

#: ``STATUS_MAP`` is swept everywhere but its second definer's own package,
#: which re-exports the table it declares itself.
_STORYGRAPH_PACKAGE = "src.ingestion.sources.storygraph_csv"

_STAR_RE_EXPORT_CASES = sorted(
    (package, name)
    for package, _ in _PLUGIN_STAR_RE_EXPORTS
    for name in (*_ONE_HOME, "STATUS_MAP")
    if not (name == "STATUS_MAP" and package == _STORYGRAPH_PACKAGE)
)


def _auto_approved_patterns(agent_text: str) -> frozenset[str]:
    """Step 0's whole rule, read off the agent the reviewer loads so the pin
    cannot drift. Read to the blank line, so a reflow drops nothing.
    """
    from_opening = dropwhile(
        lambda line: not line.startswith(_STEP_ZERO_OPENING),
        agent_text.splitlines(),
    )
    step_zero = " ".join(takewhile(str.strip, from_opening))
    assert step_zero, (
        f"No line of {_PARITY_AGENT} opens {_STEP_ZERO_OPENING!r}, so Step 0's "
        "auto-approve rule cannot be read off the agent"
    )
    return frozenset(re.findall(r"`([^`]+)`", step_zero))


_AUTO_APPROVED_PATTERNS = _auto_approved_patterns(
    _PARITY_AGENT.read_text(encoding="utf-8")
)


def _auto_approved(path: str) -> bool:
    """A trailing slash is a tree; anything else is a root-level file."""
    return any(
        (
            path.startswith(pattern)
            if pattern.endswith("/")
            else "/" not in path and fnmatch(path, pattern)
        )
        for pattern in _AUTO_APPROVED_PATTERNS
    )


#: ``templates/`` ships the per-content-type export templates.
_CAPABILITY_TREES = ("src/", "resources/", "templates/")

#: The SPA entry point: the whole surface outside those trees.
_ROOT_SURFACE_FILE = "index.html"

_SURFACE_CLAIM = "capability surface"
_CLAIMED_TREE = re.compile(r"\b[a-z][\w.-]*/")

_PARITY_GATE_CLASS = "TestTheParityGateReadsTheWholeCapabilitySurface"


def _denied_capability_trees(patterns: Iterable[str]) -> set[str]:
    return {
        pattern
        for pattern in patterns
        if pattern.endswith("/") and pattern.startswith(_CAPABILITY_TREES)
    }


def _surface_claim(agent_text: str) -> str:
    """The description sentence deciding whether the reviewer runs at all."""
    claim = next(
        (line for line in agent_text.splitlines() if _SURFACE_CLAIM in line), ""
    )
    assert claim, f"{_PARITY_AGENT} no longer describes its {_SURFACE_CLAIM}"
    return claim


def _is_path_pattern(token: str) -> bool:
    """A bare ``/`` is the separator, quoted in prose about ``fnmatch``."""
    return token != "/" and ("*" in token or token.endswith("/"))


def _docstring_patterns(class_name: str) -> set[str]:
    """Every tree or glob the docstrings of *class_name* name."""
    module = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    scope = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return {
        token
        for node in ast.walk(scope)
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        for token in re.findall(r"``([^`]+)``", ast.get_docstring(node) or "")
        if _is_path_pattern(token)
    }


def _imports_of(tree: ast.AST, package: str, imported_package: str) -> set[str]:
    return {
        f"{matched} (line {line})"
        for names, line in _import_statements(tree, package)
        if (matched := _first_within(names, imported_package)) is not None
    }


def _misplaced_framework_imports(tree: ast.AST, package: str) -> set[str]:
    return {
        f"{matched} (line {line})"
        for names, line in _import_statements(tree, package)
        for framework, served in _FRAMEWORKS.items()
        if not _is_within(package, served)
        and (matched := _first_within(names, framework)) is not None
    }


def _modules_under(package: str) -> set[str]:
    return {module for module in _TREES if _is_within(module, package)}


def _shared_modules() -> set[str]:
    return set(_TREES) - _modules_under(_CLI_PACKAGE) - _modules_under(_WEB_PACKAGE)


def _module_level_bindings(tree: ast.Module) -> set[str]:
    """An imported name is somebody else's."""
    return _bindings_in(tree.body)


def _modules_declaring(trees: Mapping[str, ast.Module], name: str) -> set[str]:
    """Takes *trees* rather than reading ``_TREES``, so the assertions below and
    the proof that one more definer changes their answer run one sweep.
    """
    return {
        module for module, tree in trees.items() if name in _module_level_bindings(tree)
    }


def _bindings_in(body: Iterable[ast.AST]) -> set[str]:
    """``def`` and ``class`` bodies excepted, every compound statement is
    descended into: enumerating them instead left ``TryStar``, ``with``,
    ``for``, ``while`` and ``match`` unswept.
    """
    names: set[str] = set()
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.stmt | ast.excepthandler | ast.match_case):
            names |= _bindings_in(ast.iter_child_nodes(node))
    return names


class TestNeitherInterfaceImportsTheOther:
    @pytest.mark.parametrize("module", sorted(_modules_under(_CLI_PACKAGE)))
    def test_no_cli_module_imports_the_web_package(self, module: str) -> None:
        assert _imports_of(_TREES[module], _PACKAGE_OF[module], _WEB_PACKAGE) == set()

    @pytest.mark.parametrize("module", sorted(_modules_under(_WEB_PACKAGE)))
    def test_no_web_module_imports_the_cli_package(self, module: str) -> None:
        assert _imports_of(_TREES[module], _PACKAGE_OF[module], _CLI_PACKAGE) == set()

    @pytest.mark.parametrize("module", sorted(_TREES))
    def test_each_framework_stays_in_the_package_it_serves(self, module: str) -> None:
        """Shared code importing ``click`` is an interface in the wrong place."""
        assert (
            _misplaced_framework_imports(_TREES[module], _PACKAGE_OF[module]) == set()
        )


class TestNothingSharedImportsAnInterface:
    """The sweep above leaves ``src/auth`` free to import ``src.web.state``,
    re-creating the coupling the move was for: the other interface imports it
    and gets an interface.
    """

    def test_no_module_outside_an_interface_imports_one(self) -> None:
        assert {
            module: crossings
            for module in _shared_modules()
            for interface in (_CLI_PACKAGE, _WEB_PACKAGE)
            if (
                crossings := _imports_of(_TREES[module], _PACKAGE_OF[module], interface)
            )
        } == {}


class TestTheExtractedTablesHaveOneHome:
    """Regression: the export reaching into the CSV plugin for these tables.

    Root cause: they were declared inside the plugin. Fix: one home each,
    ``src/models/templates.py`` and ``src/utils/csv_formula.py``.
    """

    @pytest.mark.parametrize(("name", "home"), sorted(_ONE_HOME.items()))
    def test_exactly_one_module_defines_it(self, name: str, home: str) -> None:
        assert _modules_declaring(_TREES, name) == {home}

    def test_every_table_the_two_homes_declare_is_enrolled(self) -> None:
        """The parametrization above shrinks in silence when an entry goes.

        Derived rather than pinned, so a new table added to either home
        without being enrolled fails here too.
        """
        declared = {
            name
            for home in _TABLE_HOMES
            for name in _module_level_bindings(_TREES[home])
            if not name.startswith("_")
        }

        assert declared - {"STATUS_MAP"} == set(_ONE_HOME)
        assert set(_ONE_HOME.values()) == set(_TABLE_HOMES)

    def test_status_map_is_declared_by_its_two_known_definers_only(self) -> None:
        assert _modules_declaring(_TREES, "STATUS_MAP") == _STATUS_MAP_DEFINERS

    @pytest.mark.parametrize("name", ["COMMON_COLUMNS", "STATUS_MAP"])
    def test_one_more_definer_is_reported(self, name: str) -> None:
        """Both sweeps above are set equalities, which a stalled sweep passes.

        A second home for an enrolled table and a third for ``STATUS_MAP`` are
        the same drift, so one case each is what the pins are worth.
        """
        trees = {**_TREES, "src.thief": ast.parse(f"{name} = {{}}")}

        assert _modules_declaring(trees, name) == _modules_declaring(_TREES, name) | {
            "src.thief"
        }

    @pytest.mark.parametrize(("package", "name"), _STAR_RE_EXPORT_CASES)
    def test_no_extracted_name_resolves_through_a_star_re_export(
        self, package: str, name: str
    ) -> None:
        assert not hasattr(importlib.import_module(package), name)

    @pytest.mark.parametrize(
        "package",
        ["src.ingestion.sources.generic_csv", "src.ingestion.sources.generic_json"],
    )
    @pytest.mark.parametrize("name", sorted(_ONE_HOME))
    def test_neither_generic_importer_re_exports_a_table(
        self, package: str, name: str
    ) -> None:
        """The sweep above skips if its derived population empties, and a skip
        is not a pass. Named rather than pinned as members: converting one to
        explicit re-exports must stay legitimate.
        """
        assert not hasattr(importlib.import_module(package), name)

    @pytest.mark.parametrize(("package", "source"), _PLUGIN_STAR_RE_EXPORTS)
    def test_a_star_re_export_carries_its_module_forward(
        self, package: str, source: str
    ) -> None:
        """An ``__init__`` re-exporting nothing would clear the sweep above too."""
        assert _module_level_bindings(_TREES[source]) & set(
            vars(importlib.import_module(package))
        )


class TestARedeclarationCannotHideInAGuard:
    """Nothing above would see one: a guarded declaration binds the name just
    the same, but it is not in ``tree.body``.
    """

    @pytest.mark.parametrize(
        "source",
        [
            "if TYPE_CHECKING:\n    STATUS_MAP = {}",
            "if a:\n    pass\nelse:\n    STATUS_MAP = {}",
            "try:\n    STATUS_MAP = {}\nexcept ImportError:\n    pass",
            "try:\n    pass\nexcept ImportError:\n    STATUS_MAP = {}",
            "try:\n    pass\nexcept ImportError:\n    pass\nelse:\n    STATUS_MAP = {}",
            "try:\n    pass\nfinally:\n    STATUS_MAP = {}",
            "try:\n    pass\nexcept* ImportError:\n    STATUS_MAP = {}",
            "with open(path) as handle:\n    STATUS_MAP = {}",
            "for row in rows:\n    STATUS_MAP = {}",
            "while a:\n    STATUS_MAP = {}",
            "match a:\n    case 1:\n        STATUS_MAP = {}",
        ],
    )
    def test_a_guarded_declaration_is_reported(self, source: str) -> None:
        assert "STATUS_MAP" in _module_level_bindings(ast.parse(source))

    def test_a_name_nobody_declares_is_not_reported(self) -> None:
        """A predicate answering yes to anything would pass every case above."""
        assert _module_level_bindings(ast.parse("if a:\n    pass")) == set()

    def test_a_name_bound_only_inside_a_function_is_not_reported(self) -> None:
        """Descending everywhere has to stop at ``def``, or a local reads as a
        second declaration."""
        assert _module_level_bindings(ast.parse("def f():\n    STATUS_MAP = {}")) == {
            "f"
        }

    def test_a_name_bound_only_inside_a_class_body_is_not_reported(self) -> None:
        """``C.STATUS_MAP`` is an attribute, not a module-level table."""
        assert _module_level_bindings(ast.parse("class C:\n    STATUS_MAP = {}")) == {
            "C"
        }

    def test_a_declaration_two_guards_deep_is_reported(self) -> None:
        """The descent recurses; enumerating one level would stop here."""
        source = "if a:\n    with open(path) as handle:\n        STATUS_MAP = {}"

        assert "STATUS_MAP" in _module_level_bindings(ast.parse(source))


class TestTheStarReExportSweepSeesEverySpelling:
    def test_both_star_re_exports_of_one_package_are_kept(self) -> None:
        """Keyed by importing module, the first of the two dropped out silently."""
        trees = {"src.pkg": ast.parse("from .alpha import *\nfrom .beta import *")}

        assert _star_re_exports(trees, {"src.pkg": "src.pkg"}) == [
            ("src.pkg", "src.pkg.alpha"),
            ("src.pkg", "src.pkg.beta"),
        ]

    @pytest.mark.parametrize(
        ("source", "resolved"),
        [
            ("from src.pkg.alpha import *", "src.pkg.alpha"),
            ("from .alpha import *", "src.pkg.alpha"),
            ("from . import *", "src.pkg"),
            ("from ..other import *", "src.other"),
        ],
    )
    def test_a_relative_star_re_export_resolves_to_its_module(
        self, source: str, resolved: str
    ) -> None:
        """Unresolved, the derived sweeps index ``_TREES`` with ``.alpha`` and
        raise a bare ``KeyError``."""
        trees = {"src.pkg": ast.parse(source)}

        assert _star_re_exports(trees, {"src.pkg": "src.pkg"}) == [
            ("src.pkg", resolved)
        ]

    def test_a_module_importing_no_star_contributes_nothing(self) -> None:
        """A collector answering yes to anything would pass both cases above."""
        trees = {"src.pkg": ast.parse("from .alpha import thing")}

        assert _star_re_exports(trees, {"src.pkg": "src.pkg"}) == []

    def test_no_table_home_package_is_swept(self) -> None:
        """Scoped out: a home re-exporting its own tables adds no definition."""
        homes = {home.rsplit(".", 1)[0] for home in _TABLE_HOMES}

        assert not homes & {package for package, _ in _PLUGIN_STAR_RE_EXPORTS}

    def test_the_plugin_packages_carrying_a_table_today_are_swept(self) -> None:
        """An empty population parametrizes to nothing, and pytest reads that as
        a skip. Renaming either tree in ``_PLUGIN_TREES`` empties it in silence.
        """
        assert {
            "src.enrichment.providers.tmdb",
            "src.ingestion.sources.generic_csv",
            "src.ingestion.sources.generic_json",
            _STORYGRAPH_PACKAGE,
        } <= {package for package, _ in _PLUGIN_STAR_RE_EXPORTS}

    def test_status_map_is_swept_over_every_plugin_package_but_its_own(self) -> None:
        """A stale spelling of ``_STORYGRAPH_PACKAGE`` excludes nobody, leaving
        the one package that legitimately re-exports the table asserted against.
        """
        packages = {package for package, _ in _PLUGIN_STAR_RE_EXPORTS}

        assert {
            package for package, name in _STAR_RE_EXPORT_CASES if name == "STATUS_MAP"
        } == packages - {_STORYGRAPH_PACKAGE}
        assert {package for package, _ in _STAR_RE_EXPORT_CASES} == packages


class TestTheParityGateReadsTheWholeCapabilitySurface:
    """Regression: the gate approved a diff that moved a capability.

    Root cause: Step 0 listed the trees to review, so relocating one took it
    out. Fix: a denylist, which no capability tree joins.
    """

    @pytest.mark.parametrize(
        "path",
        [
            "resources/js",
            "src/conversation",
            "src/enrichment",
            "src/models",
            "src/recommendations",
            "src/settings",
            "src/storage",
            # ``src/web`` does not start with ``src/web/``, so only a file path
            # catches the theme entry being shortened to the whole package.
            "src/cli/commands/_source.py",
            "src/web/api.py",
            # The SPA entry point is the one root-level file on the surface, so
            # it is what a root pattern widened to ``*`` would swallow.
            _ROOT_SURFACE_FILE,
        ],
    )
    def test_a_diff_on_the_capability_surface_reaches_the_review_body(
        self, path: str
    ) -> None:
        """Real paths, so a relocation fails here rather than leaving the gate
        pointed at a tree nobody ships."""
        assert (_REPO_ROOT / path).exists()
        assert not _auto_approved(path)

    @pytest.mark.parametrize(
        "path",
        [
            ".claude/agents/parity-review.md",
            ".github/workflows/ci.yml",
            "ARCHITECTURE.md",
            "Makefile",
            "config/example.yaml",
            "docker/entrypoint.sh",
            "docs/CLI.md",
            "pyproject.toml",
            "src/web/static/themes/nord/colors.css",
            "tests/test_interface_boundaries.py",
        ],
    )
    def test_a_diff_outside_the_surface_still_approves(self, path: str) -> None:
        """A denylist that stopped matching anything would pass the sweep above
        while sending every doc-only diff through a parity review."""
        assert (_REPO_ROOT / path).exists()
        assert _auto_approved(path)

    def test_the_themes_tree_is_the_only_denied_tree_inside_the_surface(self) -> None:
        """Any widened tree entry lands here, not only the paths above."""
        assert _denied_capability_trees(_AUTO_APPROVED_PATTERNS) == {
            "src/web/static/themes/"
        }

    def test_a_denied_capability_tree_outside_src_is_still_caught(self) -> None:
        """The old prefix pair, ``src/`` and ``resources/``, missed ``templates/``."""
        assert _denied_capability_trees({"docs/", "templates/"}) == {"templates/"}
        for tree in _CAPABILITY_TREES:
            assert (_REPO_ROOT / tree).is_dir()

    def test_no_root_file_pattern_reaches_a_nested_path(self) -> None:
        """``fnmatch`` globs span ``/``: the guard is what keeps ``*.md`` root-only."""
        assert [
            pattern
            for pattern in _AUTO_APPROVED_PATTERNS
            if not pattern.endswith("/") and _auto_approved(f"resources/js/{pattern}")
        ] == []

    @pytest.mark.parametrize(
        "rule",
        [
            f"{_STEP_ZERO_OPENING} `docs/`,\nor `vite.config.ts`.",
            f"{_STEP_ZERO_OPENING}\n`docs/` or `vite.config.ts`.",
        ],
    )
    def test_a_wrapped_step_zero_still_yields_every_pattern(self, rule: str) -> None:
        """A one-line parse would keep only the patterns before the wrap."""
        patterns = _auto_approved_patterns(f"{rule}\n\nLater: `src/`\n")

        assert patterns == {"docs/", "vite.config.ts"}

    @pytest.mark.parametrize(
        "rule",
        [
            "If any changed file is under `docs/`, approve.",
            "If every changed\nfile is under `docs/`, approve.",
        ],
    )
    def test_a_step_zero_the_parse_cannot_find_names_the_agent(self, rule: str) -> None:
        """With no default this raised ``StopIteration`` at import, taking every
        test in the file down with it. A wrap splitting the opening lands here too.
        """
        with pytest.raises(AssertionError, match="parity-review.md"):
            _auto_approved_patterns(rule)

    def test_every_pattern_a_docstring_names_is_one_the_gate_reads(self) -> None:
        """Prose naming a pattern nothing lists reads as a rule and is not one."""
        named = _docstring_patterns(_PARITY_GATE_CLASS)

        assert named, "the sweep found no patterns, so it proves nothing"
        assert named <= _AUTO_APPROVED_PATTERNS | set(_CAPABILITY_TREES)

    def test_the_agent_description_names_the_whole_surface(self) -> None:
        """The description is the trigger: a tree left out is never reviewed."""
        claim = _surface_claim(_PARITY_AGENT.read_text(encoding="utf-8"))

        assert set(_CLAIMED_TREE.findall(claim)) == set(_CAPABILITY_TREES)
        assert _ROOT_SURFACE_FILE in claim


class TestTheSweptPopulationIsNotEmpty:
    """``set()`` is also what a sweep that found no modules at all returns."""

    def test_discovery_finds_both_interface_packages(self) -> None:
        assert {"src.cli.commands._source", "src.cli.main"} <= _modules_under(
            _CLI_PACKAGE
        )
        assert {"src.web.api", "src.web.app"} <= _modules_under(_WEB_PACKAGE)

    def test_the_shared_services_are_outside_both_packages(self) -> None:
        """Named one by one: each used to live in an interface package."""
        assert {
            "src.auth.epic",
            "src.auth.gog",
            "src.auth.oauth_sources",
            "src.auth.trakt",
            "src.config.service",
            "src.sources.service",
            "src.utils.export",
        } <= set(_TREES) - _modules_under(_CLI_PACKAGE) - _modules_under(_WEB_PACKAGE)

    def test_the_sweep_reads_the_imports_it_judges(self) -> None:
        """A parser matching nothing would clear every module above."""
        reported = _imports_of(
            _TREES["src.cli.commands._source"], _CLI_PACKAGE, "src.sources"
        )

        assert {entry.split(" ")[0] for entry in reported} == {"src.sources.service"}

    def test_each_framework_is_imported_somewhere(self) -> None:
        """A framework nobody imports is a confinement rule proving nothing."""
        assert {
            framework
            for module, tree in _TREES.items()
            for names, _ in _import_statements(tree, _PACKAGE_OF[module])
            for framework in _FRAMEWORKS
            if _first_within(names, framework) is not None
        } == set(_FRAMEWORKS)


class TestTheSweepFailsOnACrossedBoundary:
    """The sweep above passes; these prove it is not passing vacuously."""

    @pytest.mark.parametrize(
        ("source", "reported"),
        [
            ("from src.web.api import router", "src.web.api"),
            ("from src.web import api", "src.web"),
            ("from src import web", "src.web"),
            ("import src.web.api", "src.web.api"),
            ("from ..web.api import router", "src.web.api"),
            ("from .. import web", "src.web"),
        ],
    )
    def test_a_web_import_from_the_cli_is_reported(
        self, source: str, reported: str
    ) -> None:
        tree = ast.parse(source)

        assert _imports_of(tree, _CLI_PACKAGE, _WEB_PACKAGE) == {f"{reported} (line 1)"}

    @pytest.mark.parametrize(
        ("source", "reported"),
        [
            ("from src.cli.commands import source", "src.cli.commands"),
            ("from src.cli import commands", "src.cli"),
            ("from src import cli", "src.cli"),
            ("import src.cli.main", "src.cli.main"),
            ("from ..cli.commands import source", "src.cli.commands"),
            ("from .. import cli", "src.cli"),
        ],
    )
    def test_a_cli_import_from_the_web_is_reported(
        self, source: str, reported: str
    ) -> None:
        tree = ast.parse(source)

        assert _imports_of(tree, _WEB_PACKAGE, _CLI_PACKAGE) == {f"{reported} (line 1)"}

    def test_a_package_whose_name_merely_starts_the_same_is_not_reported(self) -> None:
        """``src.website`` is not ``src.web``, and a prefix test would say it is."""
        tree = ast.parse("from src.website import thing")

        assert _imports_of(tree, _CLI_PACKAGE, _WEB_PACKAGE) == set()

    @pytest.mark.parametrize(
        ("source", "package", "reported"),
        [
            ("import click", "src.sources", "click"),
            ("from click import echo", "src.utils", "click"),
            ("from fastapi import APIRouter", "src.sources", "fastapi"),
            (
                "from starlette.responses import Response",
                "src.cli",
                "starlette.responses",
            ),
        ],
    )
    def test_a_framework_outside_its_package_is_reported(
        self, source: str, package: str, reported: str
    ) -> None:
        assert _misplaced_framework_imports(ast.parse(source), package) == {
            f"{reported} (line 1)"
        }

    @pytest.mark.parametrize(
        ("source", "package"),
        [
            ("from fastapi import APIRouter", _WEB_PACKAGE),
            ("import click", _CLI_PACKAGE),
            ("from pydantic import BaseModel", "src.models"),
        ],
    )
    def test_a_framework_in_the_package_it_serves_is_not_reported(
        self, source: str, package: str
    ) -> None:
        assert _misplaced_framework_imports(ast.parse(source), package) == set()
