from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

import pytest

import src
from src.utils import dependencies


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

    def test_the_version_comes_off_the_pyproject_beside_the_package(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nversion = "9.9.9"\n', encoding="utf-8"
        )
        assert (
            src._read_source_version(str(tmp_path / "src" / "__init__.py")) == "9.9.9"
        )

    def test_returns_none_when_no_pyproject(self, tmp_path: Path) -> None:
        fake_init = tmp_path / "src" / "__init__.py"
        fake_init.parent.mkdir()
        fake_init.touch()
        assert src._read_source_version(str(fake_init)) is None


class TestDependencyDrift:
    def test_a_missing_dependency_is_drift_unless_it_is_a_dev_extra(self) -> None:
        with patch.object(
            dependencies, "_pkg_version", side_effect=PackageNotFoundError
        ):
            missing = dependencies._drift_of("newdep>=2.0")
            assert dependencies._drift_of('newdep>=2.0; extra == "dev"') is None
        assert missing is not None
        assert missing.message == "newdep is not installed (needs >=2.0)"

    def test_a_dependency_outside_its_range_is_named_with_what_is_there(self) -> None:
        with patch.object(dependencies, "_pkg_version", return_value="1.0"):
            drift = dependencies._drift_of("newdep>=2.0")
        assert drift and drift.message == "newdep 1.0 is installed (needs >=2.0)"

    def test_what_the_mounted_source_declares_is_checked_not_what_was_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["newdep>=2.0"]\n', encoding="utf-8"
        )
        monkeypatch.setattr(src, "__file__", str(tmp_path / "src" / "__init__.py"))
        with patch.object(
            dependencies, "_pkg_version", side_effect=PackageNotFoundError
        ):
            drift = dependencies.dependency_drift()
        assert [entry.package for entry in drift] == ["newdep"]
