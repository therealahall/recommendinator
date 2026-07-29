"""The shared fakes must be callable the way the real pipeline calls a plugin."""

from __future__ import annotations

import pytest

from src.ingestion.plugin_base import SourcePlugin
from tests.fakes.source_plugins import FakeApiPlugin, FakeFilePlugin, FakeUploadPlugin


class TestFakePluginsMatchTheRealCallSignature:
    """Regression: every fake declared ``fetch(self, config)``.

    ``execute_multi_source_sync`` and ``import_file`` both call
    ``plugin.fetch(plugin_config, progress_callback=...)``. The fakes are only
    ever driven through the config endpoints today, so the mismatch never
    fired — it was a ``TypeError`` waiting for the first test that exercised a
    fake through the pipeline.
    """

    @pytest.mark.parametrize(
        "plugin",
        [FakeFilePlugin(), FakeApiPlugin(), FakeUploadPlugin()],
        ids=lambda plugin: plugin.name,
    )
    def test_fetch_accepts_a_progress_callback(self, plugin: SourcePlugin) -> None:
        items = list(
            plugin.fetch(
                {"_source_id": "my_source"},
                progress_callback=lambda *_: None,
            )
        )

        assert [item.source for item in items] == ["my_source"]
