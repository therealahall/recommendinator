"""Behavioural tests for docker/entrypoint.sh.

Run as a subprocess against a temp directory; no Docker daemon needed.
CONFIG_DIR and SEED_CONFIG are redirected under tmp_path — the seed outside the
config directory, as it is in the image. Their defaults exist only inside the
container; that the seed's default escapes the bind mount is held statically in
``test_compose.py``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# parents[2] resolves /tests/docker/test_entrypoint.py -> repo root.
# If this test file is ever moved, this constant must be updated.
_REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = _REPO_ROOT / "docker" / "entrypoint.sh"

_SHIPPED_EXAMPLE = 'web:\n  host: "127.0.0.1"\n  port: 18473\n  debug: false\n'

# A run of hex as long as the token this script used to generate. Nothing the
# entrypoint writes or prints may look like one.
_MINTED_SECRET = re.compile(r"[0-9a-f]{32,}")


def _seed_for(config_dir: Path) -> Path:
    """Where the seed lives: beside the config directory, never in it. Inside
    it is the arrangement that shipped broken."""
    return config_dir.parent / "example.yaml"


def _run(config_dir: Path, *cmd: str) -> subprocess.CompletedProcess[str]:
    """Invoke the entrypoint against ``config_dir`` and its adjacent seed.

    A benign ``cmd`` like ``echo`` is how the exec path proves it completed.
    HOME is /tmp so /bin/sh sources no developer profile.
    """
    return subprocess.run(
        [str(ENTRYPOINT), *cmd],
        env={
            "CONFIG_DIR": str(config_dir),
            "SEED_CONFIG": str(_seed_for(config_dir)),
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
        _seed_for(config_dir).write_text(example_content)

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


class TestTheSeedSurvivesTheConfigMount:
    """Regression: a fresh compose install wrote no config.yaml.

    Root cause: the seed shipped at /app/config/example.yaml, which the host's
    ./config bind mount hides. Fix: it lives at /app/example.yaml, outside the
    mount.
    """

    def test_the_seed_is_read_from_outside_the_mounted_directory(
        self, config_dir: Path
    ) -> None:
        """Both locations are populated, so a script reading the old one fails
        here rather than passing."""
        seeded = 'web:\n  host: "127.0.0.1"\n'
        _seed_for(config_dir).write_text(seeded)
        (config_dir / "example.yaml").write_text("hidden-by-the-mount: true\n")

        result = _run(config_dir, "echo", "ok")

        assert result.returncode == 0
        assert (config_dir / "config.yaml").read_text() == seeded


class TestEntrypointInventsNoSecret:
    """Regression: the container invented the operator's credential.

    Root cause: only this script minted one.
    Fix: nobody does — the account is created in the browser.
    """

    def test_no_generated_secret_reaches_the_config_or_the_log(
        self, config_dir: Path
    ) -> None:
        """A credential this script chose is one the operator never saw go by."""
        _seed_for(config_dir).write_text(_SHIPPED_EXAMPLE)

        result = _run(config_dir, "echo", "ok")

        written = (config_dir / "config.yaml").read_text()
        for text in (written, result.stdout, result.stderr):
            assert _MINTED_SECRET.search(text) is None

    def test_the_operator_is_told_to_claim_the_instance(self, config_dir: Path) -> None:
        """The open-claim window is the next thing they meet, so it is named."""
        _seed_for(config_dir).write_text(_SHIPPED_EXAMPLE)

        result = _run(config_dir, "echo", "ok")

        assert "create your account" in result.stdout
        assert "whoever reaches it first" in result.stdout

    def test_the_written_config_is_readable_only_by_its_owner(
        self, config_dir: Path
    ) -> None:
        """``cp`` inherits example.yaml's 0644, and ``./config`` is bind-mounted,
        so a file naming where the database lives is one every user on the host
        could otherwise read.
        """
        _seed_for(config_dir).write_text(_SHIPPED_EXAMPLE)

        _run(config_dir, "echo", "ok")

        mode = (config_dir / "config.yaml").stat().st_mode & 0o777
        assert mode == 0o600


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
        _seed_for(config_dir).write_text("features:\n  ai_enabled: false\n")

        result = _run(config_dir, "echo", "ok")

        assert result.returncode == 0
        assert (config_dir / "config.yaml").read_text() == user_config
        # No bootstrap messages on the steady-state path.
        assert "copied example.yaml" not in result.stdout
        assert "Edit ./config/config.yaml" not in result.stdout


class TestEntrypointMissingExample:
    """No seed available — script warns but still execs."""

    def test_warns_with_specific_message_and_continues(self, config_dir: Path) -> None:
        """Requirement: a missing seed is not an abort condition — the app
        still gets its chance to surface a clearer error. The warning names
        both paths, because which one is wrong decides what to do about it.
        """
        result = _run(config_dir, "echo", "still-ran")

        assert result.returncode == 0
        assert "still-ran" in result.stdout
        assert "no config.yaml in" in result.stderr
        assert str(_seed_for(config_dir)) in result.stderr
        assert not (config_dir / "config.yaml").exists()


class TestEntrypointFailurePropagation:
    """`set -eu` and `exec "$@"` must not swallow errors."""

    def test_exec_propagates_command_exit_code(self, config_dir: Path) -> None:
        """Requirement: exec replaces the shell, so the exec'd command's exit
        code must reach the container runtime. A non-zero exit from /bin/false
        must produce a non-zero exit from the entrypoint as a whole.
        """
        _seed_for(config_dir).write_text("placeholder: true\n")

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
        _seed_for(config_dir).write_text("placeholder: true\n")
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
    """Both path overrides are restricted to /app/* and /tmp/*."""

    @pytest.mark.parametrize("variable", ["CONFIG_DIR", "SEED_CONFIG"])
    @pytest.mark.parametrize(
        "bad_path",
        [
            "/etc/recommendinator",
            "/home/attacker/config",  # self-contained: allow hostile input under test
            # Prefix-collision boundary: /app-evil must NOT match /app/*.
            # Catches a regression where the case glob is loosened to /app*.
            "/app-evil",
            "/tmpevil",
            "relative/config",
            # A string prefix test passes both of these while they name /etc
            # and /root: CONFIG_DIR decides where config.yaml is written, and
            # SEED_CONFIG what gets copied there and chmod 600'd.
            "/app/../etc/recommendinator",
            "/tmp/../root",
        ],
    )
    def test_rejects_paths_outside_the_application_tree(
        self, variable: str, bad_path: str
    ) -> None:
        """Requirement: defense-in-depth against accidental misconfiguration.
        One override decides where the script writes, the other what it copies
        in; either outside the tree is a misuse. Refuse before running cp.
        """
        result = subprocess.run(
            [str(ENTRYPOINT), "echo", "should-not-run"],
            env={
                variable: bad_path,
                "PATH": "/usr/bin:/bin",
                "HOME": "/tmp",
            },
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert f"{variable} must be under" in result.stderr
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
