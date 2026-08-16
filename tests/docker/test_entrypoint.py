"""Behavioural tests for docker/entrypoint.sh.

Run as a subprocess against a temp directory; no Docker daemon needed.
CONFIG_DIR and SEED_CONFIG are redirected under tmp_path — the seed outside the
config directory, as it is in the image.
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
