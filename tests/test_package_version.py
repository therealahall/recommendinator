"""Tests for package version resolution in src/__init__.py."""

from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

import pytest

import src


def _make_source_tree(tmp_path: Path, pyproject_content: str | None) -> Path:
    """Build a fake source tree with optional pyproject.toml; return src/__init__.py path."""
    fake_init = tmp_path / "src" / "__init__.py"
    fake_init.parent.mkdir()
    fake_init.touch()
    if pyproject_content is not None:
        (tmp_path / "pyproject.toml").write_text(pyproject_content, encoding="utf-8")
    return fake_init


class TestPackageVersion:
    """Tests for __version__ resolution via pyproject.toml + importlib.metadata."""

    def test_version_attribute_matches_resolved_value(self) -> None:
        """__version__ resolves at import time and equals _resolve_version()'s output."""
        assert (
            isinstance(src.__version__, str) and src.__version__
        ), f"Expected non-empty version string, got {src.__version__!r}"
        # In any environment that loaded the module, __version__ must agree
        # with the live resolver — guards against drift between the two.
        assert src.__version__ == src._resolve_version()

    def test_resolve_prefers_pyproject_when_adjacent(self) -> None:
        """_resolve_version uses pyproject.toml value when present."""
        with patch("src._read_source_version", return_value="9.9.9"):
            assert src._resolve_version() == "9.9.9"

    def test_resolve_uses_metadata_when_no_pyproject(self) -> None:
        """_resolve_version falls back to importlib.metadata when no pyproject.toml."""
        with (
            patch("src._read_source_version", return_value=None),
            patch("src._pkg_version", return_value="1.2.3"),
        ):
            assert src._resolve_version() == "1.2.3"

    def test_resolve_falls_back_to_sentinel_when_not_installed(self) -> None:
        """_resolve_version returns '0.0.0' when both sources unavailable."""
        with (
            patch("src._read_source_version", return_value=None),
            patch(
                "src._pkg_version",
                side_effect=PackageNotFoundError("recommendinator"),
            ),
        ):
            assert src._resolve_version() == "0.0.0"


class TestStaleEditableInstallRegression:
    """Regression tests for issue #68.

    Bug symptom: web UI shows old version (e.g., 0.7.0) on local dev instance
    even after pulling new commits that bumped pyproject.toml.

    Root cause: importlib.metadata.version() returns the version baked into
    package metadata at install time. python-semantic-release bumps
    pyproject.toml on every release commit, but editable installs do not
    automatically refresh their metadata, so __version__ stays pinned to
    whatever was installed.

    Fix: src.__init__ now reads pyproject.toml when it sits adjacent to the
    source tree (dev or Docker), and only falls back to importlib.metadata
    for true wheel installs where pyproject.toml is not bundled alongside
    the package.
    """

    def test_pyproject_version_overrides_stale_metadata(self) -> None:
        """When pyproject.toml is adjacent, its version wins over metadata."""
        with (
            patch("src._read_source_version", return_value="0.11.0"),
            patch("src._pkg_version", return_value="0.7.0"),
        ):
            assert src._resolve_version() == "0.11.0", (
                "Expected pyproject.toml value '0.11.0' to win over stale "
                "metadata '0.7.0'"
            )

    def test_real_pyproject_is_parseable_in_dev_tree(self) -> None:
        """The dev tree's real pyproject.toml resolves to a SemVer-shaped string.

        Skipped on wheel-only installs where pyproject.toml is not bundled
        alongside the package, since that environment legitimately falls
        back to importlib.metadata.
        """
        version = src._read_source_version()
        if version is None:
            pytest.skip("pyproject.toml not adjacent to src/ (wheel install)")
        parts = version.split(".")
        assert len(parts) >= 2, f"Expected dotted version, got {version!r}"
        assert all(p for p in parts), f"Empty version segment in {version!r}"

    def test_returns_none_when_no_pyproject(self, tmp_path: Path) -> None:
        """Wheel-install layout: no pyproject.toml adjacent → fall through to metadata."""
        fake_init = _make_source_tree(tmp_path, pyproject_content=None)
        assert src._read_source_version(str(fake_init)) is None

    def test_returns_none_for_malformed_pyproject(self, tmp_path: Path) -> None:
        """Corrupted pyproject.toml must not crash module import — fall through cleanly."""
        fake_init = _make_source_tree(tmp_path, "this is = = not valid toml [[[")
        assert src._read_source_version(str(fake_init)) is None

    def test_returns_none_when_project_table_missing(self, tmp_path: Path) -> None:
        """pyproject.toml without a [project] table is not a packaging file we own."""
        fake_init = _make_source_tree(tmp_path, "[build-system]\nrequires = []\n")
        assert src._read_source_version(str(fake_init)) is None

    def test_returns_none_when_version_is_empty_string(self, tmp_path: Path) -> None:
        """Empty version string is treated as 'not set' so metadata fallback kicks in."""
        fake_init = _make_source_tree(tmp_path, '[project]\nversion = ""\n')
        assert src._read_source_version(str(fake_init)) is None

    def test_returns_none_when_version_is_not_string(self, tmp_path: Path) -> None:
        """Non-string TOML value (e.g., bare integer) must not be returned as a version."""
        fake_init = _make_source_tree(tmp_path, "[project]\nversion = 123\n")
        assert src._read_source_version(str(fake_init)) is None

    def test_returns_none_on_permission_error(self, tmp_path: Path) -> None:
        """OSError other than FileNotFoundError (e.g., permission denied) is caught."""
        fake_init = _make_source_tree(tmp_path, '[project]\nversion = "1.0.0"\n')
        with patch("pathlib.Path.open", side_effect=PermissionError("denied")):
            assert src._read_source_version(str(fake_init)) is None
