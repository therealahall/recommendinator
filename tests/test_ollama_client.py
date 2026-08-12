"""Tests for Ollama client."""

import logging
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from _pytest.logging import LogCaptureFixture
from ollama import ChatResponse, Client, ListResponse, ShowResponse
from ollama._types import Message

from src.config.service import create_llm_components
from src.llm.client import OllamaClient
from src.settings.metadata import all_entries, default_of
from src.settings.service import apply_settings, reset_setting
from src.storage.manager import StorageManager
from src.storage.settings_migration import migrate_config_settings


@pytest.fixture
def mock_ollama_client():
    """Create a mock Ollama client for testing."""
    with patch("src.llm.client.Client") as mock_client_class:
        mock_client = Mock(spec=Client)
        mock_client_class.return_value = mock_client
        yield mock_client


def test_ollama_client_initialization(mock_ollama_client):
    """Test Ollama client initialization."""
    client = OllamaClient(
        base_url="http://localhost:11434",
        default_model="mistral:7b",
        embedding_model="nomic-embed-text",
    )

    assert client.base_url == "http://localhost:11434"
    assert client.default_model == "mistral:7b"
    assert client.embedding_model == "nomic-embed-text"


def test_generate_embedding(mock_ollama_client):
    """Test embedding generation."""
    mock_ollama_client.embeddings.return_value = {
        "embedding": [0.1, 0.2, 0.3, 0.4, 0.5]
    }

    client = OllamaClient()
    embedding = client.generate_embedding("test text")

    assert embedding == [0.1, 0.2, 0.3, 0.4, 0.5]
    mock_ollama_client.embeddings.assert_called_once_with(
        model="nomic-embed-text", prompt="test text"
    )


def test_generate_embedding_custom_model(mock_ollama_client):
    """Test embedding generation with custom model."""
    mock_ollama_client.embeddings.return_value = {"embedding": [0.1, 0.2, 0.3]}

    client = OllamaClient()
    client.generate_embedding("test", model="custom-model")

    mock_ollama_client.embeddings.assert_called_once_with(
        model="custom-model", prompt="test"
    )


def test_generate_embedding_failure(mock_ollama_client):
    """Test embedding generation failure handling."""
    mock_ollama_client.embeddings.side_effect = Exception("API error")

    client = OllamaClient()

    with pytest.raises(RuntimeError, match="Embedding generation failed"):
        client.generate_embedding("test text")


def test_generate_text(mock_ollama_client):
    """Test text generation."""
    mock_ollama_client.chat.return_value = {
        "message": {"content": "Generated response"}
    }

    client = OllamaClient()
    response = client.generate_text("user prompt", system_prompt="system prompt")

    assert response == "Generated response"
    mock_ollama_client.chat.assert_called_once()
    call_args = mock_ollama_client.chat.call_args
    assert call_args.kwargs["model"] == "mistral:7b"
    assert len(call_args.kwargs["messages"]) == 2


def test_generate_text_with_options(mock_ollama_client):
    """Test text generation with custom options."""
    mock_ollama_client.chat.return_value = {"message": {"content": "Response"}}

    client = OllamaClient()
    client.generate_text(
        "prompt", temperature=0.9, max_tokens=100, model="custom-model"
    )

    call_args = mock_ollama_client.chat.call_args
    assert call_args.kwargs["model"] == "custom-model"
    assert call_args.kwargs["options"]["temperature"] == 0.9
    assert call_args.kwargs["options"]["num_predict"] == 100


def test_check_model_available(mock_ollama_client):
    """Test model availability check."""
    mock_ollama_client.show.return_value = Mock(spec=ShowResponse)

    client = OllamaClient()
    result = client.check_model_available("test-model")

    assert result is True
    mock_ollama_client.show.assert_called_once_with("test-model")


def test_check_model_available_not_found(mock_ollama_client):
    """Test model availability check when model not found."""
    mock_ollama_client.show.side_effect = Exception("Model not found")

    client = OllamaClient()
    result = client.check_model_available("nonexistent-model")

    assert result is False


def test_list_available_models(mock_ollama_client):
    """Test listing available models."""
    # Create mock models with model attribute
    mock_model1 = Mock(spec=ListResponse.Model)
    mock_model1.model = "model1"
    mock_model2 = Mock(spec=ListResponse.Model)
    mock_model2.model = "model2"

    mock_response = Mock(spec=ListResponse)
    mock_response.models = [mock_model1, mock_model2]
    mock_ollama_client.list.return_value = mock_response

    client = OllamaClient()
    models = client.list_available_models()

    assert "model1" in models
    assert "model2" in models


def test_conversation_model_defaults_to_default(mock_ollama_client):
    """Conversation model defaults to default_model when empty."""
    client = OllamaClient(default_model="mistral:7b")
    assert client.conversation_model == "mistral:7b"


def test_conversation_model_custom(mock_ollama_client):
    """Conversation model can be set independently."""
    client = OllamaClient(
        default_model="mistral:7b",
        conversation_model="qwen2.5:3b",
    )
    assert client.default_model == "mistral:7b"
    assert client.conversation_model == "qwen2.5:3b"


def test_conversation_model_empty_string_uses_default(mock_ollama_client):
    """Empty conversation_model falls back to default_model."""
    client = OllamaClient(
        default_model="mistral:7b",
        conversation_model="",
    )
    assert client.conversation_model == "mistral:7b"


def test_build_options_with_context_window(mock_ollama_client):
    """Context window size is passed as num_ctx in options."""
    options = OllamaClient._build_options(temperature=0.7, context_window_size=4096)
    assert options["num_ctx"] == 4096
    assert options["temperature"] == 0.7


def test_build_options_without_context_window(mock_ollama_client):
    """Options without context_window_size omit num_ctx."""
    options = OllamaClient._build_options(temperature=0.7)
    assert "num_ctx" not in options


def test_generate_text_with_context_window(mock_ollama_client):
    """generate_text passes context_window_size to options."""
    mock_ollama_client.chat.return_value = {"message": {"content": "Response"}}

    client = OllamaClient()
    client.generate_text("prompt", context_window_size=4096)

    call_args = mock_ollama_client.chat.call_args
    assert call_args.kwargs["options"]["num_ctx"] == 4096


def test_generate_text_failure(mock_ollama_client: Mock) -> None:
    """generate_text raises RuntimeError when Ollama call fails."""
    mock_ollama_client.chat.side_effect = ConnectionError("Ollama unreachable")

    client = OllamaClient()

    with pytest.raises(RuntimeError, match="Text generation failed"):
        client.generate_text("test prompt")


def test_chat_stream_failure(mock_ollama_client: Mock) -> None:
    """chat_stream raises RuntimeError when Ollama call fails."""
    mock_ollama_client.chat.side_effect = ConnectionError("Connection refused")

    client = OllamaClient()

    with pytest.raises(RuntimeError, match="Chat streaming failed"):
        list(client.chat_stream(messages=[{"role": "user", "content": "hi"}]))


def test_chat_stream_with_context_window(mock_ollama_client):
    """chat_stream passes context_window_size to options."""
    mock_response = iter([])
    mock_ollama_client.chat.return_value = mock_response

    client = OllamaClient()
    list(
        client.chat_stream(
            messages=[{"role": "user", "content": "test"}],
            context_window_size=8192,
        )
    )

    call_args = mock_ollama_client.chat.call_args
    assert call_args.kwargs["options"]["num_ctx"] == 8192


def test_build_options_with_all_parameters(mock_ollama_client):
    """_build_options includes all three options when all are provided.

    Verifies that temperature, max_tokens (as num_predict), and
    context_window_size (as num_ctx) are all present in the returned dict
    when all three parameters are supplied simultaneously.
    """
    options = OllamaClient._build_options(
        temperature=0.5,
        max_tokens=200,
        context_window_size=16384,
    )
    assert options["temperature"] == 0.5
    assert options["num_predict"] == 200
    assert options["num_ctx"] == 16384
    assert len(options) == 3


# ---------------------------------------------------------------------------
# generate_text_stream tests (8C)
# ---------------------------------------------------------------------------


class TestGenerateTextStream:
    """Tests for OllamaClient.generate_text_stream streaming text generation."""

    def test_yields_chunks_from_streaming_response(
        self, mock_ollama_client: Mock
    ) -> None:
        """generate_text_stream yields text chunks from the Ollama streaming response."""
        chunk1 = Mock(spec=ChatResponse)
        chunk1.message = Mock(spec=Message)
        chunk1.message.content = "Hello"
        chunk2 = Mock(spec=ChatResponse)
        chunk2.message = Mock(spec=Message)
        chunk2.message.content = " world"
        chunk3 = Mock(spec=ChatResponse)
        chunk3.message = Mock(spec=Message)
        chunk3.message.content = "!"

        mock_ollama_client.chat.return_value = iter([chunk1, chunk2, chunk3])

        client = OllamaClient()
        chunks = list(client.generate_text_stream("test prompt"))

        assert chunks == ["Hello", " world", "!"]
        call_args = mock_ollama_client.chat.call_args
        assert call_args.kwargs["stream"] is True
        assert call_args.kwargs["model"] == "mistral:7b"

    def test_uses_system_prompt(self, mock_ollama_client: Mock) -> None:
        """generate_text_stream includes system prompt in messages."""
        mock_ollama_client.chat.return_value = iter([])

        client = OllamaClient()
        list(
            client.generate_text_stream(
                "user prompt", system_prompt="system instructions"
            )
        )

        call_args = mock_ollama_client.chat.call_args
        messages = call_args.kwargs["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "system instructions"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "user prompt"

    def test_no_system_prompt_sends_only_user_message(
        self, mock_ollama_client: Mock
    ) -> None:
        """generate_text_stream sends only user message when no system prompt."""
        mock_ollama_client.chat.return_value = iter([])

        client = OllamaClient()
        list(client.generate_text_stream("just a question"))

        call_args = mock_ollama_client.chat.call_args
        messages = call_args.kwargs["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    def test_custom_model_and_options(self, mock_ollama_client: Mock) -> None:
        """generate_text_stream passes custom model, temperature, and max_tokens."""
        mock_ollama_client.chat.return_value = iter([])

        client = OllamaClient()
        list(
            client.generate_text_stream(
                "prompt",
                model="custom-model",
                temperature=0.3,
                max_tokens=50,
                context_window_size=4096,
            )
        )

        call_args = mock_ollama_client.chat.call_args
        assert call_args.kwargs["model"] == "custom-model"
        assert call_args.kwargs["options"]["temperature"] == 0.3
        assert call_args.kwargs["options"]["num_predict"] == 50
        assert call_args.kwargs["options"]["num_ctx"] == 4096

    def test_skips_chunks_with_no_content(self, mock_ollama_client: Mock) -> None:
        """generate_text_stream skips chunks with empty or missing content."""
        chunk_good = Mock(spec=ChatResponse)
        chunk_good.message = Mock(spec=Message)
        chunk_good.message.content = "data"

        chunk_empty = Mock(spec=ChatResponse)
        chunk_empty.message = Mock(spec=Message)
        chunk_empty.message.content = ""

        chunk_none = Mock(spec=ChatResponse)
        chunk_none.message = None

        mock_ollama_client.chat.return_value = iter(
            [chunk_good, chunk_empty, chunk_none]
        )

        client = OllamaClient()
        chunks = list(client.generate_text_stream("prompt"))

        assert chunks == ["data"]

    def test_raises_runtime_error_on_failure(self, mock_ollama_client: Mock) -> None:
        """generate_text_stream raises RuntimeError when Ollama call fails."""
        mock_ollama_client.chat.side_effect = ConnectionError("Connection refused")

        client = OllamaClient()

        with pytest.raises(RuntimeError, match="Streaming text generation failed"):
            list(client.generate_text_stream("prompt"))

    def test_raises_runtime_error_on_iteration_failure(
        self, mock_ollama_client: Mock
    ) -> None:
        """generate_text_stream raises RuntimeError when iteration fails mid-stream."""

        def _failing_iter():
            chunk = Mock(spec=ChatResponse)
            chunk.message = Mock(spec=Message)
            chunk.message.content = "start"
            yield chunk
            raise ConnectionError("Connection lost mid-stream")

        mock_ollama_client.chat.return_value = _failing_iter()

        client = OllamaClient()

        with pytest.raises(RuntimeError, match="Streaming text generation failed"):
            list(client.generate_text_stream("prompt"))


class TestLiveSettingsApply:
    """Bug reported: an ``ollama.*`` change on Settings needed a restart.

    Root cause: the section was frozen into ``OllamaClient`` while
    ``apply_settings`` only published it into the running config. Fixed as
    qs5i.10.7 fixed ``recommendations.*``: resolved per call.
    """

    @pytest.fixture()
    def storage(self, tmp_path: Path) -> StorageManager:
        return StorageManager(sqlite_path=tmp_path / "settings.db")

    @pytest.fixture()
    def client_class(self) -> Iterator[Mock]:
        """The patched ``ollama.Client`` class, one fresh instance per host."""
        with patch("src.llm.client.Client") as mock_client_class:
            mock_client_class.side_effect = lambda **_kwargs: Mock(spec=Client)
            yield mock_client_class

    @staticmethod
    def _booted(config: dict[str, Any], storage: StorageManager) -> OllamaClient:
        """The client boot builds: DB overlay applied, then the components."""
        migrate_config_settings(config, storage)
        client, _embeddings, _generator = create_llm_components(config)
        assert client is not None
        return client

    @pytest.fixture()
    def running(
        self, storage: StorageManager, client_class: Mock
    ) -> tuple[OllamaClient, dict[str, Any], StorageManager]:
        config: dict[str, Any] = {"features": {"ai_enabled": True}}
        return self._booted(config, storage), config, storage

    def test_model_change_reaches_the_next_call_regression(
        self, running: tuple[OllamaClient, dict[str, Any], StorageManager]
    ) -> None:
        """A saved ``ollama.model`` is the model the next generation asks for."""
        client, config, storage = running
        assert client.default_model == default_of("ollama.model")

        apply_settings(config, storage, {"ollama.model": "qwen2.5:14b"})

        client.client.chat.return_value = {"message": {"content": "hi"}}
        client.generate_text("prompt")
        assert client.client.chat.call_args.kwargs["model"] == "qwen2.5:14b"

    def test_embedding_model_change_reaches_the_next_call_regression(
        self, running: tuple[OllamaClient, dict[str, Any], StorageManager]
    ) -> None:
        """A saved ``ollama.embedding_model`` is what the next embed asks for."""
        client, config, storage = running

        apply_settings(config, storage, {"ollama.embedding_model": "mxbai-embed-large"})

        client.client.embeddings.return_value = {"embedding": [0.1]}
        client.generate_embedding("text")
        assert client.client.embeddings.call_args.kwargs["model"] == "mxbai-embed-large"

    def test_base_url_change_reaches_the_next_call_regression(
        self,
        running: tuple[OllamaClient, dict[str, Any], StorageManager],
        client_class: Mock,
    ) -> None:
        """A saved ``ollama.base_url`` is the host the next call is sent to."""
        client, config, storage = running
        client.client.chat.return_value = {"message": {"content": "hi"}}
        client.generate_text("prompt")
        assert client_class.call_args.kwargs["host"] == default_of("ollama.base_url")

        apply_settings(config, storage, {"ollama.base_url": "http://127.0.0.1:11500"})

        client.client.chat.return_value = {"message": {"content": "hi"}}
        client.generate_text("prompt")
        assert client_class.call_args.kwargs["host"] == "http://127.0.0.1:11500"

    def test_an_unchanged_base_url_reuses_one_connection(
        self,
        running: tuple[OllamaClient, dict[str, Any], StorageManager],
        client_class: Mock,
    ) -> None:
        """Re-resolving per call must not mean a new HTTP client per call."""
        client, _config, _storage = running
        client.client.chat.return_value = {"message": {"content": "hi"}}

        client.generate_text("one")
        client.generate_text("two")

        assert client_class.call_count == 1

    def test_conversation_model_follows_a_changed_generation_model(
        self, running: tuple[OllamaClient, dict[str, Any], StorageManager]
    ) -> None:
        """An empty ``conversation_model`` tracks the model in force now.

        Folding the fallback in at construction would pin chat to the model
        configured at boot.
        """
        client, config, storage = running

        apply_settings(config, storage, {"ollama.model": "qwen2.5:14b"})

        assert client.conversation_model == "qwen2.5:14b"

    def test_no_ollama_setting_requires_a_restart(self) -> None:
        """Marking one ``restart_required`` would silently re-freeze the client.

        ``apply_settings`` skips the running config for those leaves, so the
        page would go back to persisting a value nothing reads.
        """
        restart_gated = [
            entry.key
            for entry in all_entries()
            if entry.key.startswith("ollama.") and entry.restart_required
        ]

        assert restart_gated == []

    def test_resetting_a_model_reaches_the_next_call(
        self, running: tuple[OllamaClient, dict[str, Any], StorageManager]
    ) -> None:
        """Reset is the second live-apply path, and lands on the same read."""
        client, config, storage = running
        apply_settings(config, storage, {"ollama.model": "qwen2.5:14b"})

        reset_setting(config, storage, "ollama.model")

        client.client.chat.return_value = {"message": {"content": "hi"}}
        client.generate_text("prompt")
        assert client.client.chat.call_args.kwargs["model"] == default_of(
            "ollama.model"
        )

    def test_a_conversation_model_change_reaches_the_next_call(
        self, running: tuple[OllamaClient, dict[str, Any], StorageManager]
    ) -> None:
        """Set explicitly, chat stops tracking the generation model."""
        client, config, storage = running

        apply_settings(config, storage, {"ollama.conversation_model": "llama3.2:3b"})

        assert client.conversation_model == "llama3.2:3b"
        assert client.default_model == default_of("ollama.model")

    @pytest.mark.parametrize(
        "config", [{}, {"ollama": None}, {"ollama": []}, {"ollama": {"model": ""}}]
    )
    def test_a_section_that_says_nothing_leaves_the_baseline_alone(
        self, config: dict[str, Any], client_class: Mock
    ) -> None:
        """A bare ``ollama:`` header parses to None, and a cleared field to ''.

        Both are "no answer", not "no model" — falling through to either would
        send an empty model name to Ollama.
        """
        client = OllamaClient(
            base_url="http://127.0.0.1:11434",
            default_model="mistral:7b",
            config_provider=lambda: config,
        )

        assert client.default_model == "mistral:7b"
        assert client.base_url == "http://127.0.0.1:11434"

    def test_resolving_never_writes_to_the_config_it_reads(
        self, running: tuple[OllamaClient, dict[str, Any], StorageManager]
    ) -> None:
        """The per-call read is a reader: the lock in state.py guards writers.

        Materialising a default into the running config here would be a fifth
        writer of it, and one that never takes the lock.
        """
        client, config, _storage = running
        before = deepcopy(config)

        client.client.chat.return_value = {"message": {"content": "hi"}}
        client.generate_text("prompt")

        assert config == before

    def test_a_base_url_off_the_machine_is_logged(
        self, storage: StorageManager, client_class: Mock, caplog: LogCaptureFixture
    ) -> None:
        """config.yaml is the one way a remote host gets in, so say so once.

        The settings service rejects it, leaving a hand-edited file as the
        only route and nothing in the log to notice it by.
        """
        config: dict[str, Any] = {
            "features": {"ai_enabled": True},
            "ollama": {"base_url": "http://gpu.example.com:11434"},
        }

        with caplog.at_level(logging.WARNING, logger="src.llm.client"):
            client = self._booted(config, storage)
            client.client.embeddings.return_value = {"embedding": [0.1]}
            client.generate_embedding("text")

        assert "gpu.example.com" in caplog.text
        assert "not on this machine or network" in caplog.text

    def test_a_local_base_url_is_not_logged(
        self, storage: StorageManager, client_class: Mock, caplog: LogCaptureFixture
    ) -> None:
        """Without this the warning could fire on every localhost boot, which
        is how a real signal gets muted.
        """
        config: dict[str, Any] = {
            "features": {"ai_enabled": True},
            "ollama": {"base_url": "http://127.0.0.1:11434"},
        }

        with caplog.at_level(logging.WARNING, logger="src.llm.client"):
            client = self._booted(config, storage)
            client.client.embeddings.return_value = {"embedding": [0.1]}
            client.generate_embedding("text")

        assert caplog.text == ""
