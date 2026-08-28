from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = _REPO_ROOT / "docker" / "entrypoint.sh"


def _seed_for(config_dir: Path) -> Path:
    return config_dir.parent / "example.yaml"


def _run(config_dir: Path, *cmd: str) -> subprocess.CompletedProcess[str]:
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
    target = tmp_path / "config"
    target.mkdir()
    return target


class TestTheSeedSurvivesTheConfigMount:
    def test_the_seed_is_read_from_outside_the_mounted_directory(
        self, config_dir: Path
    ) -> None:
        seeded = 'web:\n  host: "127.0.0.1"\n'
        _seed_for(config_dir).write_text(seeded)
        (config_dir / "example.yaml").write_text("hidden-by-the-mount: true\n")

        result = _run(config_dir, "echo", "ok")

        assert result.returncode == 0
        assert (config_dir / "config.yaml").read_text() == seeded

    def test_an_existing_config_is_left_alone(self, config_dir: Path) -> None:
        written = "storage:\n  database_path: /srv/library.db\n"
        (config_dir / "config.yaml").write_text(written)
        _seed_for(config_dir).write_text('web:\n  host: "127.0.0.1"\n')

        result = _run(config_dir, "echo", "ok")

        assert result.returncode == 0
        assert (config_dir / "config.yaml").read_text() == written, (
            "every restart reseeds the operator's config, so the app boots on an "
            "empty library — docs/DOCKER.md promises restarts are safe"
        )
