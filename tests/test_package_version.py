from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

import src


class TestPackageVersion:
    def test_resolve_uses_metadata_when_no_pyproject(self) -> None:
        with (
            patch("src._read_source_version", return_value=None),
            patch("src._pkg_version", return_value="1.2.3"),
        ):
            assert src._resolve_version() == "1.2.3"

    def test_resolve_returns_sentinel_when_uninstalled_and_no_pyproject(self) -> None:
        with (
            patch("src._read_source_version", return_value=None),
            patch("src._pkg_version", side_effect=PackageNotFoundError),
        ):
            assert src._resolve_version() == "0.0.0"


class TestStaleEditableInstallRegression:
    """Issue #68: the web UI showed 0.7.0 after pulling 0.35.1. ``importlib``
    metadata is baked in at install time and an editable install never refreshes
    it, so an adjacent pyproject.toml wins."""

    def test_pyproject_version_overrides_stale_metadata(self) -> None:
        with (
            patch("src._read_source_version", return_value="0.11.0"),
            patch("src._pkg_version", return_value="0.7.0"),
        ):
            assert src._resolve_version() == "0.11.0", (
                "Expected pyproject.toml value '0.11.0' to win over stale "
                "metadata '0.7.0'"
            )

    def test_real_pyproject_is_parseable_in_dev_tree(self) -> None:
        assert (
            src._read_source_version() is not None
        ), "pyproject.toml is no longer adjacent to src/"

    def test_returns_none_when_no_pyproject(self, tmp_path: Path) -> None:
        fake_init = tmp_path / "src" / "__init__.py"
        fake_init.parent.mkdir()
        fake_init.touch()
        assert src._read_source_version(str(fake_init)) is None
