"""Tests for Ollama client."""

from unittest.mock import Mock, patch

import pytest
from ollama import ChatResponse, Client, ListResponse, ShowResponse
from ollama._types import Message

from src.llm.client import OllamaClient


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
