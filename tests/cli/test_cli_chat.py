"""Tests for CLI chat commands."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.conversation.engine import ConversationEngine
from src.models.content import ContentType
from src.storage.manager import StorageManager

from .conftest import _invoke_with_mocks

_AI_CONFIG = {"features": {"ai_enabled": True}, "ollama": {}}


class TestChatSend:
    """Tests for chat send (single-shot) command."""

    def test_send_message(self, cli_runner: CliRunner) -> None:
        """Test sending a single message."""
        mock_storage = MagicMock(spec=StorageManager)
        with patch("src.cli.commands.ConversationEngine") as mock_engine_cls:
            mock_engine = MagicMock(spec=ConversationEngine)
            mock_engine.process_message_sync.return_value = (
                "I recommend Dune by Frank Herbert!"
            )
            mock_engine_cls.return_value = mock_engine
            result = _invoke_with_mocks(
                cli_runner,
                ["chat", "send", "--message", "What should I read?"],
                mock_storage,
                config=_AI_CONFIG,
                llm_client=MagicMock(),
            )

        assert result.exit_code == 0
        assert "Dune" in result.output

    def test_send_requires_ai(self, cli_runner: CliRunner) -> None:
        """Test that chat requires AI to be enabled."""
        mock_storage = MagicMock(spec=StorageManager)
        result = _invoke_with_mocks(
            cli_runner,
            ["chat", "send", "--message", "Hello"],
            mock_storage,
            config={"features": {"ai_enabled": False}},
        )

        assert result.exit_code != 0
        assert "ai features are not enabled" in result.output.lower()

    def test_send_engine_error(self, cli_runner: CliRunner) -> None:
        """Test that chat send handles engine exceptions gracefully."""
        mock_storage = MagicMock(spec=StorageManager)
        with patch("src.cli.commands.ConversationEngine") as mock_engine_cls:
            mock_engine = MagicMock(spec=ConversationEngine)
            mock_engine.process_message_sync.side_effect = RuntimeError(
                "model unavailable"
            )
            mock_engine_cls.return_value = mock_engine
            result = _invoke_with_mocks(
                cli_runner,
                ["chat", "send", "--message", "Hello"],
                mock_storage,
                config=_AI_CONFIG,
                llm_client=MagicMock(),
            )

        assert result.exit_code != 0
        assert "Failed to get a response" in result.output

    def test_send_with_type_filter(self, cli_runner: CliRunner) -> None:
        """Test that --type forwards a ContentType filter to the engine."""
        mock_storage = MagicMock(spec=StorageManager)
        with patch("src.cli.commands.ConversationEngine") as mock_engine_cls:
            mock_engine = MagicMock(spec=ConversationEngine)
            mock_engine.process_message_sync.return_value = "ok"
            mock_engine_cls.return_value = mock_engine
            result = _invoke_with_mocks(
                cli_runner,
                ["chat", "send", "--message", "Hi", "--type", "book"],
                mock_storage,
                config=_AI_CONFIG,
                llm_client=MagicMock(),
            )

        assert result.exit_code == 0
        call_kwargs = mock_engine.process_message_sync.call_args[1]
        assert call_kwargs["content_type"] == ContentType.BOOK


class TestChatHistory:
    """Tests for chat history command."""

    def test_history_empty(self, cli_runner: CliRunner) -> None:
        """Test displaying chat history when no messages exist."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_conversation_history.return_value = []
        result = _invoke_with_mocks(cli_runner, ["chat", "history"], mock_storage)

        assert result.exit_code == 0
        assert "No conversation history." in result.output

    def test_show_history(self, cli_runner: CliRunner) -> None:
        """Test displaying chat history."""
        history = [
            {
                "role": "user",
                "content": "What should I read?",
                "created_at": "2026-01-01T00:00:00",
            },
            {
                "role": "assistant",
                "content": "Try Dune!",
                "created_at": "2026-01-01T00:00:01",
            },
        ]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_conversation_history.return_value = history
        result = _invoke_with_mocks(cli_runner, ["chat", "history"], mock_storage)

        assert result.exit_code == 0
        assert "What should I read?" in result.output
        assert "Try Dune!" in result.output

    def test_history_forwards_limit(self, cli_runner: CliRunner) -> None:
        """--limit is forwarded to storage.get_conversation_history."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_conversation_history.return_value = []
        result = _invoke_with_mocks(
            cli_runner,
            ["chat", "history", "--limit", "25"],
            mock_storage,
        )

        assert result.exit_code == 0
        mock_storage.get_conversation_history.assert_called_once_with(1, limit=25)

    def test_history_json_empty(self, cli_runner: CliRunner) -> None:
        """Empty history emits [] in JSON mode (matches web API shape)."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_conversation_history.return_value = []
        result = _invoke_with_mocks(
            cli_runner,
            ["chat", "history", "--format", "json"],
            mock_storage,
        )

        assert result.exit_code == 0
        assert json.loads(result.output) == []

    def test_history_json(self, cli_runner: CliRunner) -> None:
        """Test chat history JSON output matches web API MessageResponse shape."""
        history = [
            {
                "id": 1,
                "user_id": 1,
                "role": "user",
                "content": "Hi",
                "tool_calls": None,
                "created_at": "2026-01-01T00:00:00",
            },
        ]
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.get_conversation_history.return_value = history
        result = _invoke_with_mocks(
            cli_runner, ["chat", "history", "--format", "json"], mock_storage
        )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        # Field set matches web MessageResponse (no user_id)
        assert set(parsed[0].keys()) == {
            "id",
            "role",
            "content",
            "tool_calls",
            "created_at",
        }
        assert parsed[0]["content"] == "Hi"
        assert parsed[0]["id"] == 1


class TestChatReset:
    """Tests for chat reset command."""

    def test_reset_conversation(self, cli_runner: CliRunner) -> None:
        """Test resetting conversation history."""
        mock_storage = MagicMock(spec=StorageManager)
        mock_storage.clear_conversation_history.return_value = 10
        result = _invoke_with_mocks(cli_runner, ["chat", "reset"], mock_storage)

        assert result.exit_code == 0
        assert "Cleared 10 message(s)." in result.output


class TestChatStart:
    """Tests for chat start (REPL) command."""

    def test_start_requires_ai(self, cli_runner: CliRunner) -> None:
        """Test that chat start requires AI to be enabled."""
        mock_storage = MagicMock(spec=StorageManager)
        result = _invoke_with_mocks(
            cli_runner,
            ["chat", "start"],
            mock_storage,
            config={"features": {"ai_enabled": False}},
        )

        assert result.exit_code != 0
        assert "ai features are not enabled" in result.output.lower()

    def test_start_repl_exit(self, cli_runner: CliRunner) -> None:
        """Test that REPL exits on empty input (Ctrl+D / EOF)."""
        mock_storage = MagicMock(spec=StorageManager)
        with patch("src.cli.commands.ConversationEngine"):
            result = _invoke_with_mocks(
                cli_runner,
                ["chat", "start"],
                mock_storage,
                config=_AI_CONFIG,
                llm_client=MagicMock(),
                input_text="",
            )

        assert result.exit_code == 0

    def test_start_repl_message_and_exit(self, cli_runner: CliRunner) -> None:
        """Test sending a message in REPL then exiting."""
        mock_storage = MagicMock(spec=StorageManager)
        with patch("src.cli.commands.ConversationEngine") as mock_engine_cls:
            mock_engine = MagicMock(spec=ConversationEngine)
            mock_engine.process_message_sync.return_value = "Great choice!"
            mock_engine_cls.return_value = mock_engine
            result = _invoke_with_mocks(
                cli_runner,
                ["chat", "start"],
                mock_storage,
                config=_AI_CONFIG,
                llm_client=MagicMock(),
                input_text="I just finished Dune\n",
            )

        assert result.exit_code == 0
        assert "Great choice!" in result.output
