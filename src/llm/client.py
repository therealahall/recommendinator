"""Ollama client wrapper for LLM interactions."""

import logging
from collections.abc import Iterator
from typing import Any

from ollama import Client

logger = logging.getLogger(__name__)


class OllamaClient:
    """Client for interacting with Ollama API."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        default_model: str = "mistral:7b",
        embedding_model: str = "nomic-embed-text",
        timeout: float = 300.0,
    ) -> None:
        """Initialize Ollama client.

        Args:
            base_url: Base URL for Ollama API
            default_model: Default model for text generation
            embedding_model: Model for generating embeddings
            timeout: Request timeout in seconds
        """
        self.base_url = base_url
        self.default_model = default_model
        self.embedding_model = embedding_model
        self.timeout = timeout
        self.client = Client(host=base_url, timeout=timeout)

    def generate_embedding(self, text: str, model: str | None = None) -> list[float]:
        """Generate embedding for text.

        Args:
            text: Text to generate embedding for
            model: Model to use (defaults to embedding_model)

        Returns:
            Embedding vector as list of floats

        Raises:
            RuntimeError: If embedding generation fails
        """
        model = model or self.embedding_model

        try:
            response = self.client.embeddings(model=model, prompt=text)
            embedding: list[float] = response.get("embedding", [])
            if not embedding:
                raise RuntimeError(f"No embedding returned from model {model}")
            return embedding
        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            raise RuntimeError(f"Embedding generation failed: {e}") from e

    def generate_embeddings(
        self, texts: list[str], model: str | None = None
    ) -> list[list[float]]:
        """Generate embeddings for multiple texts.

        Args:
            texts: List of texts to generate embeddings for
            model: Model to use (defaults to embedding_model)

        Returns:
            List of embedding vectors

        Raises:
            RuntimeError: If embedding generation fails
        """
        model = model or self.embedding_model
        embeddings = []

        for text in texts:
            try:
                embedding = self.generate_embedding(text, model)
                embeddings.append(embedding)
            except Exception as e:
                logger.error(f"Failed to generate embedding for text: {e}")
                # Continue with other texts, but log the error
                raise

        return embeddings

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        """Generate text using the LLM.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            model: Model to use (defaults to default_model)
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate

        Returns:
            Generated text

        Raises:
            RuntimeError: If text generation fails
        """
        model = model or self.default_model

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            options: dict[str, Any] = {"temperature": temperature}
            if max_tokens:
                options["num_predict"] = max_tokens

            response = self.client.chat(model=model, messages=messages, options=options)

            content: str = response.get("message", {}).get("content", "")
            if not content:
                raise RuntimeError(f"No content returned from model {model}")

            return content
        except Exception as e:
            logger.error(f"Failed to generate text: {e}")
            raise RuntimeError(f"Text generation failed: {e}") from e

    def check_model_available(self, model: str) -> bool:
        """Check if a model is available in Ollama.

        Args:
            model: Model name to check

        Returns:
            True if model is available, False otherwise
        """
        try:
            # Try to get model info
            self.client.show(model)
            return True
        except Exception:
            return False

    def list_available_models(self) -> list[str]:
        """List all available models in Ollama.

        Returns:
            List of model names
        """
        try:
            response = self.client.list()
            # Ollama returns a ListResponse object with models attribute
            if hasattr(response, "models"):
                models_attr = response.models
                return [
                    m.model
                    for m in models_attr
                    if hasattr(m, "model") and m.model is not None
                ]
            return []
        except Exception as e:
            logger.error(f"Failed to list models: {e}")
            return []

    def generate_text_stream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """Generate text using the LLM with streaming response.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            model: Model to use (defaults to default_model)
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate

        Yields:
            Text chunks as they are generated

        Raises:
            RuntimeError: If text generation fails
        """
        model = model or self.default_model

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            options: dict[str, Any] = {"temperature": temperature}
            if max_tokens:
                options["num_predict"] = max_tokens

            response = self.client.chat(
                model=model, messages=messages, options=options, stream=True
            )

            for chunk in response:
                if hasattr(chunk, "message") and chunk.message:
                    content = chunk.message.content
                    if content:
                        yield content

        except Exception as e:
            logger.error(f"Failed to generate streaming text: {e}")
            raise RuntimeError(f"Streaming text generation failed: {e}") from e

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """Multi-turn chat with streaming response.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt (prepended to messages)
            model: Model to use (defaults to default_model)
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate

        Yields:
            Text chunks as they are generated

        Raises:
            RuntimeError: If chat fails
        """
        model = model or self.default_model

        try:
            full_messages = []
            if system_prompt:
                full_messages.append({"role": "system", "content": system_prompt})
            full_messages.extend(messages)

            options: dict[str, Any] = {"temperature": temperature}
            if max_tokens:
                options["num_predict"] = max_tokens

            response = self.client.chat(
                model=model, messages=full_messages, options=options, stream=True
            )

            for chunk in response:
                if hasattr(chunk, "message") and chunk.message:
                    content = chunk.message.content
                    if content:
                        yield content

        except Exception as e:
            logger.error(f"Failed to stream chat: {e}")
            raise RuntimeError(f"Chat streaming failed: {e}") from e
