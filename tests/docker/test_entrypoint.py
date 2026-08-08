"""Behavioural tests for docker/entrypoint.sh.

Exercises the script as a subprocess against a temp directory; no Docker
daemon is required. The entrypoint reads CONFIG_DIR from the environment
(defaulting to /app/config), so we redirect it to tmp_path for isolation.

The default value (/app/config) is intentionally not tested here — that path
only exists inside the container, and verifying the default would require a
Docker daemon. The runtime behavior is exercised end-to-end by the docker.yml
PR build's smoke test.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from src.cli.config import MIN_API_TOKEN_LENGTH as _MIN_TOKEN_LENGTH

# parents[2] resolves /tests/docker/test_entrypoint.py -> repo root.
# If this test file is ever moved, this constant must be updated.
_REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = _REPO_ROOT / "docker" / "entrypoint.sh"

# The shape of the shipped example.yaml the token substitution depends on: an
# empty api_token inside a web section carrying other keys.
_EXAMPLE_WITH_PLACEHOLDER = (
    'web:\n  api_token: ""\n  host: "127.0.0.1"\n  port: 18473\n  debug: false\n'
)


def _api_token(config: Path) -> str:
    """Read ``web.api_token`` back out of a written config file."""
    token = yaml.safe_load(config.read_text())["web"]["api_token"]
    assert isinstance(token, str)
    return token


def _run(config_dir: Path, *cmd: str) -> subprocess.CompletedProcess[str]:
    """Invoke the entrypoint with CONFIG_DIR overridden to ``config_dir``.

    The entrypoint exec's ``cmd`` after handling config bootstrap, so passing
    a benign command like ``echo`` lets us verify the exec path completed.
    HOME is set to /tmp to keep /bin/sh from sourcing developer-specific
    profile files on hosts with exotic shell configs.
    """
    return subprocess.run(
        [str(ENTRYPOINT), *cmd],
        env={
            "CONFIG_DIR": str(config_dir),
            "PATH": "/usr/bin:/bin",
            "HOME": "/tmp",
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    """Provide an empty config directory for each test.

    The path lives under pytest's tmp_path which resolves to /tmp on Linux,
    so it satisfies the entrypoint's CONFIG_DIR bounds check (/app/* | /tmp/*).
    """
    target = tmp_path / "config"
    target.mkdir()
    return target


class TestEntrypointFirstRun:
    """First-run config bootstrap: example.yaml present, config.yaml missing."""

    def test_copies_example_to_config_and_logs_guidance(self, config_dir: Path) -> None:
        """Requirement: an empty config directory mounted on first start gets
        a working config.yaml seeded from example.yaml.

        The guidance line must say the file is bootstrap-only. It used to read
        "Edit ./config/config.yaml on the host with your settings" — which is
        now a dead end, because example.yaml carries only the web bind and
        storage paths. A user who followed it would open the file, find nothing
        recognisable, and conclude the app was broken.
        """
        example_content = "features:\n  ai_enabled: false\n"
        (config_dir / "example.yaml").write_text(example_content)

        result = _run(config_dir, "echo", "exec-target-ran")

        assert result.returncode == 0
        config = config_dir / "config.yaml"
        assert config.exists()
        assert config.read_text() == example_content
        assert "copied example.yaml as a starting point" in result.stdout
        assert "Data sources, settings, and API keys are managed in the app" in (
            result.stdout
        )
        # The dead-end instruction must not come back.
        assert "Edit ./config/config.yaml on the host" not in result.stdout
        # Verify exec actually replaced the shell — the passed command's
        # stdout must reach the test, not get swallowed.
        assert "exec-target-ran" in result.stdout


class TestEntrypointMintsTheApiToken:
    """The container's answer to auth being required rather than optional.

    A fresh `docker compose up` has to reach a working, authenticated app, and
    the operator has to be told the token exactly once.
    """

    def test_the_placeholder_is_replaced_with_a_random_token(
        self, config_dir: Path
    ) -> None:
        """Both halves matter: a token is written, and it is not a constant."""
        (config_dir / "example.yaml").write_text(_EXAMPLE_WITH_PLACEHOLDER)

        first = _run(config_dir, "echo", "ok")
        written = _api_token(config_dir / "config.yaml")

        (config_dir / "config.yaml").unlink()
        _run(config_dir, "echo", "ok")

        assert first.returncode == 0
        assert len(written) >= _MIN_TOKEN_LENGTH
        assert written != _api_token(config_dir / "config.yaml")

    def test_the_token_is_printed_once_with_the_bearer_instruction(
        self, config_dir: Path
    ) -> None:
        """Nothing else tells the operator what to paste into the UI."""
        (config_dir / "example.yaml").write_text(_EXAMPLE_WITH_PLACEHOLDER)

        result = _run(config_dir, "echo", "ok")

        token = _api_token(config_dir / "config.yaml")
        assert result.stdout.count(token) == 1
        assert "Minted an API token" in result.stdout

    def test_the_rest_of_the_web_section_survives(self, config_dir: Path) -> None:
        """Substituted in place, so host, port and debug are not rewritten."""
        (config_dir / "example.yaml").write_text(_EXAMPLE_WITH_PLACEHOLDER)

        _run(config_dir, "echo", "ok")

        written = (config_dir / "config.yaml").read_text()
        assert yaml.safe_load(written)["web"]["port"] == 18473
        assert written.count("web:") == 1

    def test_a_second_start_neither_remints_nor_reprints(
        self, config_dir: Path
    ) -> None:
        """The token is a credential, so a steady-state restart must not log it."""
        (config_dir / "example.yaml").write_text(_EXAMPLE_WITH_PLACEHOLDER)
        _run(config_dir, "echo", "ok")
        token = _api_token(config_dir / "config.yaml")

        second = _run(config_dir, "echo", "ok")

        assert _api_token(config_dir / "config.yaml") == token
        assert token not in second.stdout
        assert token not in second.stderr

    def test_an_example_with_no_placeholder_warns_instead_of_failing_silently(
        self, config_dir: Path
    ) -> None:
        """Otherwise the app dies at boot and nothing says why."""
        (config_dir / "example.yaml").write_text("web:\n  port: 18473\n")

        result = _run(config_dir, "echo", "still-ran")

        assert result.returncode == 0
        assert "could not write web.api_token" in result.stderr
        assert "still-ran" in result.stdout

    def test_the_written_config_is_readable_only_by_its_owner(
        self, config_dir: Path
    ) -> None:
        """``cp`` inherits example.yaml's 0644 and ``sed -i`` preserves it.

        ``./config`` is bind-mounted, so that put the API token in a file every
        user on the host could read.
        """
        (config_dir / "example.yaml").write_text(_EXAMPLE_WITH_PLACEHOLDER)

        _run(config_dir, "echo", "ok")

        mode = (config_dir / "config.yaml").stat().st_mode & 0o777
        assert mode == 0o600

    def test_the_shipped_example_carries_the_placeholder_the_script_looks_for(
        self,
    ) -> None:
        """The substitution and the file it edits are in two repositories' worth
        of distance from each other, and nothing else pairs them. Matched on the
        script's own pattern, which takes any indentation."""
        example = (_REPO_ROOT / "config" / "example.yaml").read_text()

        assert re.search(r'^ *api_token: ""', example, re.MULTILINE) is not None


class TestEntrypointIdempotency:
    """Subsequent runs: existing config.yaml must never be clobbered."""

    def test_existing_config_is_preserved_silently(self, config_dir: Path) -> None:
        """Requirement: editing config.yaml then restarting must not lose
        user settings, and the script should not log copy-success messages
        (the entrypoint runs on every container start; spurious messages
        pollute logs at steady state).
        """
        user_config = "features:\n  ai_enabled: true\n  custom: value\n"
        (config_dir / "config.yaml").write_text(user_config)
        (config_dir / "example.yaml").write_text("features:\n  ai_enabled: false\n")

        result = _run(config_dir, "echo", "ok")

        assert result.returncode == 0
        assert (config_dir / "config.yaml").read_text() == user_config
        # No bootstrap messages on the steady-state path.
        assert "copied example.yaml" not in result.stdout
        assert "Edit ./config/config.yaml" not in result.stdout


class TestEntrypointMissingExample:
    """No example.yaml available — script warns but still execs."""

    def test_warns_with_specific_message_and_continues(self, config_dir: Path) -> None:
        """Requirement: an empty config dir with no example.yaml is not an
        abort condition — the application should still get a chance to start
        and surface a clearer error. The warning message must name both files
        so operators know what went wrong.
        """
        result = _run(config_dir, "echo", "still-ran")

        assert result.returncode == 0
        assert "still-ran" in result.stdout
        assert "neither config.yaml nor example.yaml present" in result.stderr
        assert not (config_dir / "config.yaml").exists()


class TestEntrypointFailurePropagation:
    """`set -eu` and `exec "$@"` must not swallow errors."""

    def test_exec_propagates_command_exit_code(self, config_dir: Path) -> None:
        """Requirement: exec replaces the shell, so the exec'd command's exit
        code must reach the container runtime. A non-zero exit from /bin/false
        must produce a non-zero exit from the entrypoint as a whole.
        """
        (config_dir / "example.yaml").write_text("placeholder: true\n")

        result = _run(config_dir, "/bin/false")

        assert result.returncode != 0

    def test_set_eu_aborts_on_cp_failure(
        self, config_dir: Path, tmp_path: Path
    ) -> None:
        """Requirement: set -eu must surface filesystem errors instead of
        silently dropping the user into a half-bootstrapped state. A
        read-only config dir would cause cp to fail; the script must abort
        before exec'ing the command.
        """
        (config_dir / "example.yaml").write_text("placeholder: true\n")
        # Drop write permission so cp will fail.
        config_dir.chmod(0o555)
        try:
            result = _run(config_dir, "echo", "should-not-run")
        finally:
            # Restore write so pytest can clean up tmp_path.
            config_dir.chmod(0o755)

        assert result.returncode != 0
        # The exec'd command must NOT have run.
        assert "should-not-run" not in result.stdout


class TestEntrypointBoundsCheck:
    """CONFIG_DIR override is restricted to /app/* and /tmp/* paths."""

    @pytest.mark.parametrize(
        "bad_dir",
        [
            "/etc/recommendinator",
            "/home/attacker/config",  # self-contained: allow hostile input under test
            # Prefix-collision boundary: /app-evil must NOT match /app/*.
            # Catches a regression where the case glob is loosened to /app*.
            "/app-evil",
            "/tmpevil",
            "relative/config",
        ],
    )
    def test_rejects_config_dir_outside_allowed_paths(self, bad_dir: str) -> None:
        """Requirement: defense-in-depth against accidental misconfiguration —
        a CONFIG_DIR pointing outside /app/* or /tmp/* would let the entrypoint
        write outside the application tree. The script must refuse and exit
        with a clear error before running cp.
        """
        result = subprocess.run(
            [str(ENTRYPOINT), "echo", "should-not-run"],
            env={
                "CONFIG_DIR": bad_dir,
                "PATH": "/usr/bin:/bin",
                "HOME": "/tmp",
            },
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert "CONFIG_DIR must be under" in result.stderr
        assert "should-not-run" not in result.stdout


class TestEntrypointShellLint:
    """Static checks that catch obvious script regressions in pytest."""

    def test_script_is_executable(self) -> None:
        """The script must be executable; a non-executable file fails to
        invoke as ENTRYPOINT in Docker."""
        assert ENTRYPOINT.exists()
        assert ENTRYPOINT.stat().st_mode & 0o100

    def test_script_declares_set_eu_at_a_real_command_line(self) -> None:
        """Anchored check: ``set -eu`` must appear at the start of a non-comment
        line so the shell actually executes it. A loose substring search
        (``"set -eu" in content``) would happily pass if the directive only
        appeared in a comment — useless for catching a regression where the
        line was accidentally deleted but a comment about it survived.
        """
        for raw_line in ENTRYPOINT.read_text().splitlines():
            line = raw_line.lstrip()
            if line.startswith("#") or not line:
                continue
            if line.startswith("set -eu"):
                return
        raise AssertionError(
            "entrypoint.sh does not start a non-comment line with 'set -eu'"
        )
