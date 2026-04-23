"""Tests for CLI memory commands."""

import json
from typing import Any
from unittest.mock import MagicMock

from click.testing import CliRunner

from src.storage.manager import StorageManager

from .conftest import _invoke_with_mocks


def _make_memory_dict(
    memory_id: int = 1,
    memory_text: str = "Prefers sci-fi",
    memory_type: str = "user_stated",
    is_active: bool = True,
) -> dict[str, Any]:
    return {
        "id": memory_id,
        "user_id": 1,
        "memory_text": memory_text,
        "memory_type": memory_type,
        "source": "manual",
        "confidence": 1.0,
        "is_active": is_active,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


class TestMemoryList:
    """Tests for memory list command."""

    def test_list_empty(self, cli_runner: CliRunner) -> None:
        """Test listing memories when none exist."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_core_memories.return_value = []
        result = _invoke_with_mocks(cli_runner, ["memory", "list"], mock_storage)

        assert result.exit_code == 0
        assert "No memories found." in result.output

    def test_list_memories(self, cli_runner: CliRunner) -> None:
        """Test listing memories."""
        memories = [
            _make_memory_dict(1, "Prefers sci-fi"),
            _make_memory_dict(2, "Dislikes horror"),
        ]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_core_memories.return_value = memories
        result = _invoke_with_mocks(cli_runner, ["memory", "list"], mock_storage)

        assert result.exit_code == 0
        assert "Prefers sci-fi" in result.output
        assert "Dislikes horror" in result.output

    def test_list_json(self, cli_runner: CliRunner) -> None:
        """Test listing memories JSON output matches web API MemoryResponse shape."""
        memories = [_make_memory_dict(1, "Prefers sci-fi")]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_core_memories.return_value = memories
        result = _invoke_with_mocks(
            cli_runner, ["memory", "list", "--format", "json"], mock_storage
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        # Field set matches web MemoryResponse (no user_id, no updated_at)
        assert set(parsed[0].keys()) == {
            "id",
            "memory_text",
            "memory_type",
            "confidence",
            "is_active",
            "source",
            "created_at",
        }
        assert parsed[0]["memory_text"] == "Prefers sci-fi"
        assert parsed[0]["is_active"] is True

    def test_list_defaults_to_active_only(self, cli_runner: CliRunner) -> None:
        """Test that memory list defaults to active-only (matches web API)."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_core_memories.return_value = []
        result = _invoke_with_mocks(cli_runner, ["memory", "list"], mock_storage)

        assert result.exit_code == 0
        mock_storage.get_core_memories.assert_called_once_with(1, active_only=True)

    def test_list_include_inactive(self, cli_runner: CliRunner) -> None:
        """Test that --include-inactive flag returns all memories."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_core_memories.return_value = []
        result = _invoke_with_mocks(
            cli_runner, ["memory", "list", "--include-inactive"], mock_storage
        )

        assert result.exit_code == 0
        mock_storage.get_core_memories.assert_called_once_with(1, active_only=False)


class TestMemoryAdd:
    """Tests for memory add command."""

    def test_add_memory(self, cli_runner: CliRunner) -> None:
        """Test adding a new memory."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.save_core_memory.return_value = 1
        result = _invoke_with_mocks(
            cli_runner,
            ["memory", "add", "--text", "Loves dystopian fiction"],
            mock_storage,
        )

        assert result.exit_code == 0
        assert "Memory 1 created." in result.output
        mock_storage.save_core_memory.assert_called_once_with(
            user_id=1,
            memory_text="Loves dystopian fiction",
            memory_type="user_stated",
            source="manual",
            confidence=1.0,
        )


class TestMemoryEdit:
    """Tests for memory edit command."""

    def test_edit_not_found(self, cli_runner: CliRunner) -> None:
        """Test editing a memory that does not exist."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.update_core_memory.return_value = False
        result = _invoke_with_mocks(
            cli_runner,
            ["memory", "edit", "--id", "999", "--text", "New text"],
            mock_storage,
        )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_edit_memory(self, cli_runner: CliRunner) -> None:
        """Test editing a memory."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.update_core_memory.return_value = True
        result = _invoke_with_mocks(
            cli_runner,
            ["memory", "edit", "--id", "1", "--text", "Updated text"],
            mock_storage,
        )

        assert result.exit_code == 0
        mock_storage.update_core_memory.assert_called_once_with(
            memory_id=1, memory_text="Updated text", is_active=None
        )

    def test_edit_text_and_active(self, cli_runner: CliRunner) -> None:
        """--text and --active can be set in one call (matches web PUT).

        Bug: earlier the CLI required two separate commands (memory edit + memory
        toggle) to change text and active state, while the web PUT /api/memories
        accepts both in one request. The CLI now accepts --active/--inactive on
        edit so a single call mirrors the web contract.
        """
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.update_core_memory.return_value = True
        result = _invoke_with_mocks(
            cli_runner,
            [
                "memory",
                "edit",
                "--id",
                "1",
                "--text",
                "New text",
                "--inactive",
            ],
            mock_storage,
        )

        assert result.exit_code == 0
        mock_storage.update_core_memory.assert_called_once_with(
            memory_id=1, memory_text="New text", is_active=False
        )

    def test_edit_active_only(self, cli_runner: CliRunner) -> None:
        """--active without --text only changes active state."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.update_core_memory.return_value = True
        result = _invoke_with_mocks(
            cli_runner,
            ["memory", "edit", "--id", "1", "--active"],
            mock_storage,
        )

        assert result.exit_code == 0
        mock_storage.update_core_memory.assert_called_once_with(
            memory_id=1, memory_text=None, is_active=True
        )

    def test_edit_no_fields(self, cli_runner: CliRunner) -> None:
        """No --text and no --active/--inactive aborts without touching storage."""
        mock_storage = MagicMock(spec=StorageManager)
        result = _invoke_with_mocks(
            cli_runner,
            ["memory", "edit", "--id", "1"],
            mock_storage,
        )

        assert result.exit_code != 0
        assert "specify --text and/or --active/--inactive" in result.output
        mock_storage.update_core_memory.assert_not_called()


class TestMemoryToggle:
    """Tests for memory toggle command."""

    def test_toggle_not_found(self, cli_runner: CliRunner) -> None:
        """Test toggling a memory that does not exist."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_core_memories.return_value = []
        result = _invoke_with_mocks(
            cli_runner, ["memory", "toggle", "--id", "999"], mock_storage
        )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_toggle_memory_active_to_inactive(self, cli_runner: CliRunner) -> None:
        """Test toggling an active memory flips it to inactive."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_core_memories.return_value = [
            _make_memory_dict(1, "Test", is_active=True)
        ]
        mock_storage.update_core_memory.return_value = True
        result = _invoke_with_mocks(
            cli_runner, ["memory", "toggle", "--id", "1"], mock_storage
        )

        assert result.exit_code == 0
        assert "Memory 1 is now inactive." in result.output
        mock_storage.update_core_memory.assert_called_once_with(
            memory_id=1, is_active=False
        )

    def test_toggle_memory_inactive_to_active(self, cli_runner: CliRunner) -> None:
        """Test toggling an inactive memory flips it to active."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_core_memories.return_value = [
            _make_memory_dict(1, "Test", is_active=False)
        ]
        mock_storage.update_core_memory.return_value = True
        result = _invoke_with_mocks(
            cli_runner, ["memory", "toggle", "--id", "1"], mock_storage
        )

        assert result.exit_code == 0
        assert "Memory 1 is now active." in result.output
        mock_storage.update_core_memory.assert_called_once_with(
            memory_id=1, is_active=True
        )


class TestMemoryDelete:
    """Tests for memory delete command."""

    def test_delete_not_found(self, cli_runner: CliRunner) -> None:
        """Test deleting a memory that does not exist."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.delete_core_memory.return_value = False
        result = _invoke_with_mocks(
            cli_runner,
            ["memory", "delete", "--id", "999", "--yes"],
            mock_storage,
        )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_delete_memory(self, cli_runner: CliRunner) -> None:
        """Test deleting a memory."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.delete_core_memory.return_value = True
        result = _invoke_with_mocks(
            cli_runner,
            ["memory", "delete", "--id", "1", "--yes"],
            mock_storage,
        )

        assert result.exit_code == 0
        mock_storage.delete_core_memory.assert_called_once_with(memory_id=1)

    def test_delete_memory_aborted(self, cli_runner: CliRunner) -> None:
        """Test aborting memory deletion."""
        mock_storage = MagicMock(spec=StorageManager)
        result = _invoke_with_mocks(
            cli_runner,
            ["memory", "delete", "--id", "1"],
            mock_storage,
            input_text="n\n",
        )

        assert "Aborted" in result.output
        mock_storage.delete_core_memory.assert_not_called()
