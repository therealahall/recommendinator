"""Tests for package version resolution in src/__init__.py."""

import importlib
from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

import src


class TestPackageVersion:
    """Tests for __version__ resolution via importlib.metadata."""

    def test_version_returns_metadata_value_when_installed(self) -> None:
        """__version__ equals the value from importlib.metadata when available."""
        try:
            with patch(
                "importlib.metadata.version",
                return_value="1.2.3",
            ):
                importlib.reload(src)
                assert (
                    src.__version__ == "1.2.3"
                ), f"Expected '1.2.3' from mocked metadata, got {src.__version__!r}"
        finally:
            importlib.reload(src)

    def test_version_falls_back_to_sentinel_when_not_installed(self) -> None:
        """__version__ falls back to '0.0.0' when package metadata is unavailable.

        Covers the PackageNotFoundError path for when the project is run
        from a cloned repo without being installed via pip or uv.
        """
        try:
            with patch(
                "importlib.metadata.version",
                side_effect=PackageNotFoundError("recommendinator"),
            ):
                # Reload inside the patch context so the module re-executes
                # its top-level try/except while the patch is active.
                importlib.reload(src)
                assert (
                    src.__version__ == "0.0.0"
                ), f"Expected fallback sentinel '0.0.0', got {src.__version__!r}"
        finally:
            # Restore the real version for subsequent tests.
            importlib.reload(src)
