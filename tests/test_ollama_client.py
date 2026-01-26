"""Tests for Ollama client."""

from unittest.mock import Mock, patch

import pytest

from src.llm.client import OllamaClient


@pytest.fixture
def mock_ollama_client():
    """Create a mock Ollama client for testing."""
    with patch("src.llm.client.Client") as mock_client_class:
        mock_client = Mock()
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
    mock_ollama_client.show.return_value = Mock()

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
    mock_model1 = Mock()
    mock_model1.model = "model1"
    mock_model2 = Mock()
    mock_model2.model = "model2"

    mock_response = Mock()
    mock_response.models = [mock_model1, mock_model2]
    mock_ollama_client.list.return_value = mock_response

    client = OllamaClient()
    models = client.list_available_models()

    assert "model1" in models
    assert "model2" in models
