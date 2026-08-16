"""Behavioural tests for docker/entrypoint.sh.

Run as a subprocess against a temp directory; no Docker daemon needed.
CONFIG_DIR and SEED_CONFIG are redirected under tmp_path — the seed outside the
config directory, as it is in the image.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# parents[2] resolves /tests/docker/test_entrypoint.py -> repo root.
# If this test file is ever moved, this constant must be updated.
_REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = _REPO_ROOT / "docker" / "entrypoint.sh"


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
