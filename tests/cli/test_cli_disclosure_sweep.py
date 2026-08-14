"""Every module under ``src/cli`` swept for a fault reaching the terminal.

Ten sites echoed the exception they caught, each fixed where it was pointed.
The guard is the package: a new one fails here rather than in review.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import src.cli as cli_package

_CLI_ROOT = Path(cli_package.__file__).parent

#: The funnel, in both its spellings: the one that stops the command, and the
#: one a loop calls to print the refusal and carry on. Everything reporting a
#: caught fault goes through one of them.
_ABORTING_FUNNEL = "abort_after_failure"
_REPORTING_FUNNEL = "report_failure"
_FUNNELS = {_ABORTING_FUNNEL, _REPORTING_FUNNEL}

#: Calls that put text in front of the person running the command. Click's
#: exceptions are here because Click prints their message and exits.
_OUTPUT_SINKS = {
    "abort_with",
    "click.BadParameter",
    "click.ClickException",
    "click.UsageError",
    "click.echo",
    "click.secho",
    "print",
}

#: Sinks none of the predicates here can see, so they are banned outright.
_UNSWEPT_SINKS = {"sys.stdout.write", "sys.stderr.write", "click.echo_via_pager"}

#: Exceptions whose message is written to be read, so rendering one is the
#: answer the matching web route gives. Everything else is a fault.
_USER_FACING_EXCEPTIONS = {
    "AccountNameError": "the name rule both interfaces enforce",
    "PreferenceValidationError": "the 400 detail the preferences route returns",
    "SettingsValidationError": "carries key and reason, both rendered by the web",
    "SourceConfigError": "its message is the detail the source routes answer with",
    "UnknownUserError": "names an id the caller passed",
    "ValueCoercionError": "'expected int', built from the declared type",
}

#: Faults rendered on purpose, each with the reason the funnel is wrong there.
_EXEMPT_SITES = {
    (
        "main.py",
        "f'Error: {exception_for_log(error)}'",
    ): "boot fails before configure_logging, so there is no log to point at",
    (
        "main.py",
        "f'Warning: no log file for this run: {exception_for_log(error)}'",
    ): "the log is what failed, so it cannot hold the reason",
    (
        "main.py",
        "f'Error initializing components: {exception_for_log(error)}'",
    ): "same, and this guard covers migrate_config_secrets",
    (
        "_shared.py",
        "f'Could not read {from_json}: {error}'",
    ): "the caller's own path, and the OSError on their own file",
    (
        "_shared.py",
        "f'Invalid JSON: {error}'",
    ): "the position in the caller's own document",
}

#: Named, so discovery finding nothing fails here rather than reporting a
#: clean sweep over an empty package.
_MODULES_CALLING_THE_FUNNEL = {
    # Its aborting half calls its reporting half.
    "_shared.py",
    "commands/_account.py",
    "commands/_auth.py",
    "commands/_complete.py",
    "commands/_recommend.py",
    "commands/_update.py",
}

_MODULES_THAT_CATCH = {
    "_shared.py",
    "main.py",
    "commands/_account.py",
    "commands/_auth.py",
    "commands/_complete.py",
    "commands/_preferences.py",
    "commands/_recommend.py",
    "commands/_settings.py",
    "commands/_source.py",
    "commands/_update.py",
}

_TREES = {
    path.relative_to(_CLI_ROOT).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
    for path in sorted(_CLI_ROOT.rglob("*.py"))
}


def _bound_handlers(tree: ast.AST) -> list[ast.ExceptHandler]:
    """Every ``except ... as name``; an unbound one has nothing to render."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.name
    ]


def _caught_types(handler: ast.ExceptHandler) -> set[str]:
    """A bare ``except:`` catches everything, so it is judged as such."""
    if handler.type is None:
        return {"BaseException"}
    caught = (
        handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    )
    return {ast.unparse(node) for node in caught}


def _mentions(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(found, ast.Name) and found.id == name for found in ast.walk(node)
    )


def _rendered_faults(tree: ast.AST) -> set[tuple[str, int]]:
    """Every ``(argument, line)`` handing a caught fault to an output sink.

    Keywords too: ``click.echo(message=...)`` reaches the same terminal.
    """
    return {
        (ast.unparse(argument), argument.lineno)
        for handler in _bound_handlers(tree)
        if not _caught_types(handler) <= set(_USER_FACING_EXCEPTIONS)
        for call in ast.walk(handler)
        if isinstance(call, ast.Call) and ast.unparse(call.func) in _OUTPUT_SINKS
        for argument in [*call.args, *(word.value for word in call.keywords)]
        if handler.name is not None and _mentions(argument, handler.name)
    }


def _disclosed_faults(module: str, tree: ast.AST) -> set[str]:
    return {
        f"{text} (line {line})"
        for text, line in _rendered_faults(tree)
        if (module, text) not in _EXEMPT_SITES
    }


def _funnel_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNNELS
    ]


def _handlers_that_say_nothing(tree: ast.AST) -> set[str]:
    """Handlers on a fault that neither funnel it, render it, nor re-raise.

    Swallowing one leaves the operator a command that did nothing and gave no
    reason why.
    """
    return {
        f"{ast.unparse(handler.type or ast.Name('bare'))} (line {handler.lineno})"
        for handler in _bound_handlers(tree)
        if not _caught_types(handler) <= set(_USER_FACING_EXCEPTIONS)
        and not _funnel_calls(handler)
        and not _rendered_faults(handler)
        and not any(isinstance(node, ast.Raise) for node in ast.walk(handler))
    }


def _refusals_the_funnel_should_own(tree: ast.AST) -> set[str]:
    """Handlers that log the fault and print instead, the funnel uncalled.

    That pair is the funnel's whole job. Hand-rolled it repeats the wording
    and answers ``--verbose`` with nothing, and an unbound ``except`` keeps it
    out of every check above.
    """
    return {
        f"{ast.unparse(handler.type or ast.Name('bare'))} (line {handler.lineno})"
        for handler in ast.walk(tree)
        if isinstance(handler, ast.ExceptHandler)
        and not _funnel_calls(handler)
        and any(
            keyword.arg == "exc_info"
            for call in _log_calls(handler)
            for keyword in call.keywords
        )
        and any(
            isinstance(call, ast.Call) and ast.unparse(call.func) in _OUTPUT_SINKS
            for call in ast.walk(handler)
        )
    }


def _output_sinks_the_sweep_cannot_see(tree: ast.AST) -> set[str]:
    return {
        f"{ast.unparse(node)} (line {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) in _UNSWEPT_SINKS
    }


def _log_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logger"
    ]


@pytest.mark.parametrize("module", sorted(_TREES))
class TestNoCliModuleDisclosesAFaultOutsideTheFunnel:
    """Parametrized over what discovery finds, so a new module is swept too."""

    def test_no_caught_fault_reaches_an_output_sink(self, module: str) -> None:
        assert _disclosed_faults(module, _TREES[module]) == set()

    def test_no_handler_swallows_the_failure(self, module: str) -> None:
        assert _handlers_that_say_nothing(_TREES[module]) == set()

    def test_every_reported_fault_goes_through_the_funnel(self, module: str) -> None:
        """Binding nothing exempts a handler from disclosure, not from the funnel.

        Reported: ``--verbose`` is documented as a promise the whole CLI keeps,
        and ``auth connect --source gog`` answered it with the same generic
        line either way.
        """
        assert _refusals_the_funnel_should_own(_TREES[module]) == set()

    def test_no_output_sink_emits_under_another_name(self, module: str) -> None:
        """``sys.stderr.write`` reaches the terminal past every check above."""
        assert _output_sinks_the_sweep_cannot_see(_TREES[module]) == set()


class TestTheSweptPopulationIsNotEmpty:
    """``set()`` is also what a sweep that found no modules at all returns."""

    def test_discovery_finds_the_command_modules(self) -> None:
        assert {"main.py", "_shared.py", "commands/_update.py"} <= set(_TREES)

    def test_the_funnel_is_reached_from_every_group_that_reports_a_fault(self) -> None:
        """The anchor: six assertions above hold over zero funnel call sites."""
        assert {
            module for module, tree in _TREES.items() if _funnel_calls(tree)
        } == _MODULES_CALLING_THE_FUNNEL

    def test_both_funnel_spellings_are_declared_in_one_module(self) -> None:
        assert {
            f"{module}: {node.name}"
            for module, tree in _TREES.items()
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name in _FUNNELS
        } == {f"_shared.py: {name}" for name in _FUNNELS}

    def test_every_module_that_catches_is_swept(self) -> None:
        """Otherwise a parse that stopped matching would clear the whole file."""
        assert {
            module for module, tree in _TREES.items() if _bound_handlers(tree)
        } == _MODULES_THAT_CATCH

    def test_every_exemption_is_still_a_live_site(self) -> None:
        """A stale waiver widens the blind spot in silence.

        Equality, so a raw site nobody enrolled fails here as well.
        """
        assert {
            (module, text)
            for module, tree in _TREES.items()
            for text, _ in _rendered_faults(tree)
        } == set(_EXEMPT_SITES)


class TestTheCliDisclosureSweepFailsOnANewRawSink:
    """The sweep above passes; these prove it is not passing vacuously.

    Each feeds the offending source to the predicate the tests above call and
    asserts the whole report, so one naming the wrong node fails too.
    """

    @staticmethod
    def _handler(body: str, caught: str = "Exception") -> ast.Module:
        return ast.parse(f"try:\n    go()\nexcept {caught} as error:\n    {body}")

    @pytest.mark.parametrize(
        "body",
        [
            "click.echo(f'Error: {error}', err=True)",
            "click.echo('Error: %s' % error)",
            "click.secho(str(error), fg='red')",
            "abort_with(f'Could not sync: {error}')",
            "print(error)",
            "click.echo(message=f'{error}')",
            "raise click.ClickException(str(error)) from None",
        ],
    )
    def test_a_fault_handed_to_an_output_sink_is_reported(self, body: str) -> None:
        reported = _disclosed_faults("commands/_thief.py", self._handler(body))

        assert len(reported) == 1
        assert "error" in next(iter(reported))

    @pytest.mark.parametrize("caught", ["Exception", "OSError", "TraktAuthError"])
    def test_every_fault_type_is_judged_the_same(self, caught: str) -> None:
        tree = self._handler("click.echo(f'{error}')", caught)

        assert _disclosed_faults("commands/_thief.py", tree) == {"f'{error}' (line 4)"}

    def test_a_bare_except_is_judged_as_catching_everything(self) -> None:
        """``except:`` carries no type node for the tuple parse to read."""
        tree = ast.parse("try:\n    go()\nexcept BaseException as error:\n    p(error)")
        handler = _bound_handlers(tree)[0]
        handler.type = None

        assert _caught_types(handler) == {"BaseException"}

    @pytest.mark.parametrize(
        "caught",
        ["SourceConfigError", "SettingsValidationError", "ValueCoercionError"],
    )
    def test_a_user_facing_exception_is_not_reported(self, caught: str) -> None:
        """Flagging these leaves the source and settings groups no way to pass."""
        tree = self._handler("abort_with(error.message)", caught)

        assert _disclosed_faults("commands/_thief.py", tree) == set()

    def test_a_mixed_tuple_falls_back_to_the_strict_rule(self) -> None:
        """One user-facing type beside a fault must not waive the fault."""
        tree = self._handler("click.echo(f'{error}')", "(SourceConfigError, OSError)")

        assert _disclosed_faults("commands/_thief.py", tree) == {"f'{error}' (line 4)"}

    def test_a_waived_site_is_reported_under_another_module(self) -> None:
        """The waiver is keyed on the module, so a copy elsewhere still fails."""
        tree = self._handler("abort_with(f'Invalid JSON: {error}')", "ValueError")

        assert _disclosed_faults("commands/_thief.py", tree) == {
            "f'Invalid JSON: {error}' (line 4)"
        }
        assert _disclosed_faults("_shared.py", tree) == set()

    def test_a_swallowed_fault_is_reported(self) -> None:
        assert _handlers_that_say_nothing(self._handler("pass")) == {
            "Exception (line 3)"
        }

    @pytest.mark.parametrize(
        "body",
        [
            f"{_ABORTING_FUNNEL}(ctx, MESSAGE, error)",
            "raise click.Abort() from error",
            "click.echo(f'{error}')",
        ],
        ids=["funnelled", "re-raised", "rendered"],
    )
    def test_a_handler_that_answers_is_not_reported(self, body: str) -> None:
        """Only silence is this finding; the rendered case is the check above's."""
        assert _handlers_that_say_nothing(self._handler(body)) == set()

    @pytest.mark.parametrize("funnel", sorted(_FUNNELS))
    def test_either_funnel_spelling_answers_the_handler(self, funnel: str) -> None:
        """A loop that must keep going calls the reporting half instead."""
        tree = self._handler(f"{funnel}(ctx, MESSAGE, error)")

        assert _handlers_that_say_nothing(tree) == set()
        assert _refusals_the_funnel_should_own(tree) == set()

    @pytest.mark.parametrize(
        "clause",
        ["Exception as error", "Exception", "TraktAuthError"],
        ids=["bound", "unbound", "narrow"],
    )
    def test_a_hand_rolled_refusal_is_reported_whatever_it_binds(
        self, clause: str
    ) -> None:
        """The unbound spelling is the one the other predicates cannot see."""
        tree = ast.parse(
            f"try:\n    go()\nexcept {clause}:\n"
            "    logger.error('Trakt sync failed', exc_info=True)\n"
            "    click.echo('Error: Failed. Check logs for details.', err=True)"
        )

        assert _refusals_the_funnel_should_own(tree) == {
            f"{clause.removesuffix(' as error')} (line 3)"
        }

    @pytest.mark.parametrize(
        "body",
        [
            f"{_ABORTING_FUNNEL}(ctx, MESSAGE, error)",
            "logger.error('Trakt sync failed', exc_info=True)",
            "click.echo('Error: Failed.', err=True)",
            "logger.error('Trakt sync failed')\n    click.echo('Error: Failed.')",
        ],
        ids=["funnelled", "logged-only", "printed-only", "logged-without-detail"],
    )
    def test_a_handler_that_is_not_the_funnels_job_is_not_reported(
        self, body: str
    ) -> None:
        """Only the log-the-detail-and-print-a-line pair is the funnel's."""
        assert _refusals_the_funnel_should_own(self._handler(body)) == set()

    @pytest.mark.parametrize(
        "source",
        [
            "sys.stderr.write(str(error))",
            "sys.stdout.write(line)",
            "click.echo_via_pager(text)",
        ],
    )
    def test_an_output_sink_under_another_name_is_reported(self, source: str) -> None:
        assert _output_sinks_the_sweep_cannot_see(ast.parse(source)) == {
            f"{source} (line 1)"
        }

    def test_the_clean_shape_is_not_reported(self) -> None:
        """The predicates accept what the command modules actually write."""
        tree = self._handler(f"{_ABORTING_FUNNEL}(ctx, MESSAGE, error)")

        assert _disclosed_faults("commands/_thief.py", tree) == set()
        assert _handlers_that_say_nothing(tree) == set()
        assert _refusals_the_funnel_should_own(tree) == set()
        assert _output_sinks_the_sweep_cannot_see(tree) == set()


class TestTheSweptCliModulesThemselvesFailOnAHandRolledRefusal:
    """The controls above hand the predicate a line nobody wrote in ``src/``.

    That leaves the link between them untested: whether ``_TREES`` holds the
    text the next hand-rolled refusal would land in.
    """

    @pytest.mark.parametrize("module", sorted(_MODULES_THAT_CATCH))
    def test_an_unbound_refusal_added_to_it_is_reported(self, module: str) -> None:
        source = (_CLI_ROOT / module).read_text(encoding="utf-8")
        tree = ast.parse(
            f"{source}\ntry:\n    go()\nexcept Exception:\n"
            "    logger.error('Sync failed', exc_info=True)\n"
            "    click.echo('Error: Sync failed. Check logs.', err=True)\n"
        )

        assert _refusals_the_funnel_should_own(tree) != set()
        assert _refusals_the_funnel_should_own(_TREES[module]) == set()
