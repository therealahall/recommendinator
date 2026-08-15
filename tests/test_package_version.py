from pathlib import Path
from unittest.mock import patch

import src


def _make_source_tree(tmp_path: Path, pyproject_content: str | None) -> Path:
    fake_init = tmp_path / "src" / "__init__.py"
    fake_init.parent.mkdir()
    fake_init.touch()
    if pyproject_content is not None:
        (tmp_path / "pyproject.toml").write_text(pyproject_content, encoding="utf-8")
    return fake_init


class TestPackageVersion:
    def test_resolve_prefers_pyproject_when_adjacent(self) -> None:
        with patch("src._read_source_version", return_value="9.9.9"):
            assert src._resolve_version() == "9.9.9"

    def test_resolve_uses_metadata_when_no_pyproject(self) -> None:
        with (
            patch("src._read_source_version", return_value=None),
            patch("src._pkg_version", return_value="1.2.3"),
        ):
            assert src._resolve_version() == "1.2.3"


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
        """Asserted unconditionally: everywhere this runs, pyproject.toml IS
        adjacent to ``src/``, so ``None`` is a layout regression, not a skip."""
        version = src._read_source_version()
        assert version is not None, "pyproject.toml is no longer adjacent to src/"
        parts = version.split(".")
        assert len(parts) >= 2, f"Expected dotted version, got {version!r}"
        assert all(part for part in parts), f"Empty version segment in {version!r}"

    def test_returns_none_when_no_pyproject(self, tmp_path: Path) -> None:
        fake_init = _make_source_tree(tmp_path, pyproject_content=None)
        assert src._read_source_version(str(fake_init)) is None

    def test_returns_none_for_malformed_pyproject(self, tmp_path: Path) -> None:
        fake_init = _make_source_tree(tmp_path, "this is = = not valid toml [[[")
        assert src._read_source_version(str(fake_init)) is None

    def test_returns_none_when_project_table_missing(self, tmp_path: Path) -> None:
        fake_init = _make_source_tree(tmp_path, "[build-system]\nrequires = []\n")
        assert src._read_source_version(str(fake_init)) is None

    def test_returns_none_when_version_is_empty_string(self, tmp_path: Path) -> None:
        fake_init = _make_source_tree(tmp_path, '[project]\nversion = ""\n')
        assert src._read_source_version(str(fake_init)) is None

    def test_returns_none_when_version_is_not_string(self, tmp_path: Path) -> None:
        fake_init = _make_source_tree(tmp_path, "[project]\nversion = 123\n")
        assert src._read_source_version(str(fake_init)) is None

    def test_returns_none_on_permission_error(self, tmp_path: Path) -> None:
        fake_init = _make_source_tree(tmp_path, '[project]\nversion = "1.0.0"\n')
        with patch("pathlib.Path.open", side_effect=PermissionError("denied")):
            assert src._read_source_version(str(fake_init)) is None
