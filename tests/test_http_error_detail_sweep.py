"""Guard: no HTTP refusal builds its detail by rendering a value.

``detail=str(error)`` is forbidden outright (docs/SECURITY.md), and review
found two fresh sites in one pull request, both fixed by hand. The rule is
swept over the syntax tree here instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import src.web
from tests.ast_sweeps import renders_a_value_as_text

_WEB_ROOT = Path(src.web.__file__).parent

_SRC_ROOT = _WEB_ROOT.parent

_HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put"}

#: The names a route decorator hangs off: the three routers and, for the SPA
#: index alone, the application itself.
_ROUTE_DECORATOR_HOLDERS = {"app", "router"}

#: The modules declaring a route. Named so discovery finding nothing fails here
#: rather than reporting a clean sweep over an empty route list.
_ROUTE_MODULES = {"api.py", "app.py", "auth_api.py"}

#: The modules constructing an ``HTTPException``. A dependency refuses the
#: caller exactly as a route does, so the sweep is the package rather than the
#: three modules above.
_MODULES_THAT_REFUSE = {
    "api.py",
    "auth.py",
    "auth_api.py",
    "csrf.py",
    "guards.py",
}

_TREES = {
    path.name: ast.parse(path.read_text(encoding="utf-8"))
    for path in sorted(_WEB_ROOT.glob("*.py"))
}


def _names_the_refusal(node: ast.AST) -> bool:
    """Both spellings: ``app.py`` reaches the class through ``starlette``."""
    if isinstance(node, ast.Name):
        return node.id == "HTTPException"
    return isinstance(node, ast.Attribute) and node.attr == "HTTPException"


def _refusal_calls(tree: ast.AST) -> list[ast.Call]:
    """Every ``HTTPException(...)``, raised here or returned to be raised."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _names_the_refusal(node.func)
    ]


def _detail_argument(call: ast.Call) -> ast.expr | None:
    """The refusal's detail, written either way round."""
    for keyword in call.keywords:
        if keyword.arg == "detail":
            return keyword.value
    return call.args[1] if len(call.args) > 1 else None


def _bindings(tree: ast.AST) -> list[tuple[list[ast.expr], ast.expr]]:
    """Every assignment's targets and value, annotated ones included.

    Mypy runs strict here, so ``detail: str = f"…"`` is ordinary style rather
    than an exotic spelling worth leaving to review.
    """
    bindings: list[tuple[list[ast.expr], ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            bindings.append((node.targets, node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            bindings.append(([node.target], node.value))
    return bindings


def _names_bound_to_rendered_text(tree: ast.AST) -> set[str]:
    """Locals holding a rendering, so binding one first is not a way past."""
    return {
        target.id
        for targets, value in _bindings(tree)
        if renders_a_value_as_text(value)
        for target in targets
        if isinstance(target, ast.Name)
    }


def _rendered_refusal_details(tree: ast.AST) -> set[str]:
    """Details holding a rendering anywhere in them.

    Judged over the whole expression rather than its root: a rendering nested
    in the dict body of a structured detail reaches the client the same way.
    """
    rendered_names = _names_bound_to_rendered_text(tree)
    return {
        f"{ast.unparse(detail)} (line {detail.lineno})"
        for call in _refusal_calls(tree)
        if (detail := _detail_argument(call)) is not None
        and (
            any(renders_a_value_as_text(node) for node in ast.walk(detail))
            or (isinstance(detail, ast.Name) and detail.id in rendered_names)
        )
    }


def _refusals_with_no_readable_detail(tree: ast.AST) -> set[str]:
    """Refusals whose detail the check above cannot see.

    A spread hands over arguments the syntax tree does not name, so the whole
    call passes every predicate here in silence.
    """
    return {
        f"{ast.unparse(call)} (line {call.lineno})"
        for call in _refusal_calls(tree)
        if any(keyword.arg is None for keyword in call.keywords)
        or any(isinstance(argument, ast.Starred) for argument in call.args)
    }


def _route_decorators(tree: ast.AST) -> list[ast.expr]:
    """``@router.get(...)`` and its siblings, the decorators mounting a path."""
    return [
        decorator
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id in _ROUTE_DECORATOR_HOLDERS
        and decorator.func.attr in _HTTP_METHODS
    ]


def _modules_naming_the_refusal() -> set[str]:
    """Every module under ``src/`` that so much as names ``HTTPException``."""
    return {
        path.relative_to(_SRC_ROOT).as_posix()
        for path in _SRC_ROOT.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if _names_the_refusal(node)
    }


@pytest.mark.parametrize("module", sorted(_TREES))
class TestNoRefusalRendersItsDetail:
    """Internal error text never reaches an HTTP response (docs/SECURITY.md).

    Parametrized over what discovery finds rather than a list, so a module
    added tomorrow is swept without anyone remembering to enrol it.
    """

    def test_no_detail_is_built_by_rendering_a_value(self, module: str) -> None:
        assert _rendered_refusal_details(_TREES[module]) == set()

    def test_every_refusal_shows_the_sweep_its_detail(self, module: str) -> None:
        assert _refusals_with_no_readable_detail(_TREES[module]) == set()


class TestTheSweptRefusalsAreNotEmpty:
    """``set()`` is also what a sweep finding no refusals at all returns."""

    def test_discovery_finds_the_route_modules(self) -> None:
        assert {
            module for module, tree in _TREES.items() if _route_decorators(tree)
        } == _ROUTE_MODULES

    def test_every_module_that_refuses_is_swept(self) -> None:
        assert {
            module for module, tree in _TREES.items() if _refusal_calls(tree)
        } == _MODULES_THAT_REFUSE

    def test_the_sweep_reads_a_detail_off_every_one(self) -> None:
        """An extractor answering ``None`` everywhere would pass every module."""
        assert {
            module
            for module, tree in _TREES.items()
            if any(_detail_argument(call) is not None for call in _refusal_calls(tree))
        } == _MODULES_THAT_REFUSE

    def test_nothing_outside_the_swept_package_refuses(self) -> None:
        """``src/web`` is the root; a refusal elsewhere would be unswept.

        ``app.py`` names the class without constructing one: it renders the
        raised refusal on a response body that can carry it.
        """
        assert _modules_naming_the_refusal() == {
            f"web/{module}" for module in _MODULES_THAT_REFUSE | {"app.py"}
        }


class TestTheSweptModulesThemselvesFailOnANewRendering:
    """The controls below hand the predicate a line nobody wrote in ``src/``.

    That leaves the link between them untested: whether ``_TREES`` holds the
    text the violation would land in.
    """

    @pytest.mark.parametrize("module", sorted(_MODULES_THAT_REFUSE | _ROUTE_MODULES))
    def test_a_violation_added_to_it_is_reported(self, module: str) -> None:
        source = (_WEB_ROOT / module).read_text(encoding="utf-8")
        tree = ast.parse(f"{source}\nraise HTTPException(500, detail=str(error))\n")

        assert _rendered_refusal_details(tree) != set()
        assert _rendered_refusal_details(_TREES[module]) == set()


class TestTheDetailSweepFailsOnANewRendering:
    """The sweep above passes; these prove it is not passing vacuously.

    Each feeds the offending source to the predicate the test above calls and
    asserts the whole report, so one naming the wrong node fails too.
    """

    @pytest.mark.parametrize(
        "detail",
        [
            "str(error)",
            "f'{error}'",
            "'Sync failed: %s' % error",
            "'Sync failed: {}'.format(error)",
        ],
        ids=["str", "an f-string", "a % expression", "a .format call"],
    )
    def test_a_rendered_detail_is_reported(self, detail: str) -> None:
        source = f"raise HTTPException(status_code=500, detail={detail})"

        assert _rendered_refusal_details(ast.parse(source)) == {f"{detail} (line 1)"}

    def test_a_positional_detail_is_reported(self) -> None:
        """FastAPI takes the detail second, so a keyword-only sweep misses it."""
        tree = ast.parse("raise HTTPException(500, str(error))")

        assert _rendered_refusal_details(tree) == {"str(error) (line 1)"}

    def test_a_rendering_nested_in_a_structured_detail_is_reported(self) -> None:
        """The live structured details are dicts, so the dict body is in scope."""
        tree = ast.parse("raise HTTPException(400, {'reason': str(error)})")

        assert _rendered_refusal_details(tree) == {"{'reason': str(error)} (line 1)"}

    def test_a_detail_bound_before_the_call_is_reported(self) -> None:
        """One assignment ahead of the raise would otherwise lose it."""
        tree = ast.parse(
            "message = f'Sync failed: {error}'\n"
            "raise HTTPException(status_code=500, detail=message)"
        )

        assert _rendered_refusal_details(tree) == {"message (line 2)"}

    def test_a_returned_refusal_is_reported(self) -> None:
        """``_config_error_to_http`` builds one to be raised by its caller."""
        tree = ast.parse("return HTTPException(status_code=400, detail=str(error))")

        assert _rendered_refusal_details(tree) == {"str(error) (line 1)"}

    @pytest.mark.parametrize(
        "reference",
        ["fastapi.HTTPException", "starlette.exceptions.HTTPException"],
        ids=["through fastapi", "through starlette"],
    )
    def test_a_refusal_reached_through_its_module_is_reported(
        self, reference: str
    ) -> None:
        """``app.py`` already imports the class from ``starlette``.

        The anchor below counts an attribute reference as a module that
        refuses, so a sweep blind to it passes on a violation its own
        population check has already admitted.
        """
        tree = ast.parse(f"raise {reference}(status_code=500, detail=str(error))")

        assert _rendered_refusal_details(tree) == {"str(error) (line 1)"}
        assert _refusals_with_no_readable_detail(tree) == set()

    def test_a_spread_refusal_reached_through_its_module_is_reported(self) -> None:
        """The unreadable-detail check keys on the same call list."""
        tree = ast.parse("raise fastapi.HTTPException(**refusal)")

        assert _refusals_with_no_readable_detail(tree) == {
            "fastapi.HTTPException(**refusal) (line 1)"
        }

    def test_an_annotated_binding_ahead_of_the_call_is_reported(self) -> None:
        """Mypy runs strict here, so an annotated local is ordinary style."""
        tree = ast.parse(
            "detail: str = f'Sync failed: {error}'\n"
            "raise HTTPException(status_code=500, detail=detail)"
        )

        assert _rendered_refusal_details(tree) == {"detail (line 2)"}

    def test_a_rendering_bound_inside_the_detail_is_reported(self) -> None:
        """A walrus binds and passes in one expression, skipping the assignment."""
        tree = ast.parse("raise HTTPException(500, (message := str(error)))")

        assert _rendered_refusal_details(tree) == {"(message := str(error)) (line 1)"}

    def test_a_rendering_nested_in_a_list_detail_is_reported(self) -> None:
        """FastAPI's own 422 detail is a list, so the shape is not exotic."""
        tree = ast.parse("raise HTTPException(422, [{'msg': f'{error}'}])")

        assert _rendered_refusal_details(tree) == {"[{'msg': f'{error}'}] (line 1)"}

    @pytest.mark.parametrize(
        "call",
        [
            "HTTPException(**refusal)",
            "HTTPException(status_code=400, **body)",
            "HTTPException(*refusal)",
        ],
        ids=["every argument spread", "the detail spread", "a positional spread"],
    )
    def test_a_spread_refusal_is_reported(self, call: str) -> None:
        """The one shape hiding a detail from the check above entirely."""
        tree = ast.parse(f"raise {call}")

        assert _refusals_with_no_readable_detail(tree) == {f"{call} (line 1)"}
        assert _rendered_refusal_details(tree) == set()

    @pytest.mark.parametrize(
        "detail",
        [
            "'Item not found'",
            "PASSWORD_TOO_SHORT",
            "error.message",
            "_misconfigured_detail(plugin, validation_errors)",
            "_ERROR_KIND_TO_DETAIL.get(error.kind, 'Invalid request.')",
            "{'key': error.key, 'reason': error.reason}",
        ],
    )
    def test_the_details_the_routes_actually_write_are_not_reported(
        self, detail: str
    ) -> None:
        """Flagging these would leave no spelling anybody could pass."""
        tree = ast.parse(f"raise HTTPException(status_code=400, detail={detail})")

        assert _rendered_refusal_details(tree) == set()
        assert _refusals_with_no_readable_detail(tree) == set()

    def test_a_rendering_that_reaches_no_detail_is_not_reported(self) -> None:
        """The sweep is the detail slot, not every f-string in the module."""
        tree = ast.parse(
            "summary = f'{count} rows'\n"
            "raise HTTPException(status_code=404, detail='Item not found')"
        )

        assert _rendered_refusal_details(tree) == set()
