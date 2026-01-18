"""Ollama client wrapper for LLM interactions."""

import logging
from typing import List, Optional, Dict, Any
import httpx
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

    def generate_embedding(self, text: str, model: Optional[str] = None) -> List[float]:
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
            embedding = response.get("embedding", [])
            if not embedding:
                raise RuntimeError(f"No embedding returned from model {model}")
            return embedding
        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            raise RuntimeError(f"Embedding generation failed: {e}") from e

    def generate_embeddings(
        self, texts: List[str], model: Optional[str] = None
    ) -> List[List[float]]:
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
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
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

            options: Dict[str, Any] = {"temperature": temperature}
            if max_tokens:
                options["num_predict"] = max_tokens

            response = self.client.chat(model=model, messages=messages, options=options)

            content = response.get("message", {}).get("content", "")
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

    def list_available_models(self) -> List[str]:
        """List all available models in Ollama.

        Returns:
            List of model names
        """
        try:
            response = self.client.list()
            # Ollama returns a ListResponse object with models attribute
            if hasattr(response, "models"):
                return [
                    model.model for model in response.models if hasattr(model, "model")
                ]
            # Fallback for dict response
            if isinstance(response, dict) and "models" in response:
                models_list = response["models"]
                return [
                    m.get("name") or m.get("model")
                    for m in models_list
                    if m.get("name") or m.get("model")
                ]
            return []
        except Exception as e:
            logger.error(f"Failed to list models: {e}")
            return []
