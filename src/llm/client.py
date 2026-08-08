"""Ollama client wrapper for LLM interactions."""

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from src.utils.urls import is_local_url

# Optional dependency: ollama is only installed with the [ai] extra.
# The non-AI Docker image does not include it. ImportError is caught by
# create_llm_components() in src/cli/config.py for graceful degradation.
try:
    from ollama import Client
except ImportError:
    Client = None  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _OllamaSettings:
    """Where to reach Ollama and which model to ask, from one config read."""

    base_url: str
    default_model: str
    embedding_model: str
    conversation_model: str


def _text(value: Any, fallback: str) -> str:
    """Return *value* when it is a non-empty string, else *fallback*."""
    return value if isinstance(value, str) and value else fallback


class OllamaClient:
    """Client for interacting with Ollama API.

    With a *config_provider* the ``ollama`` section resolves per call instead
    of freezing at construction, so a Settings-page change reaches the next
    request without a restart.
    """

    def __init__(
        self,
        base_url: str = "http://ollama:11434",
        default_model: str = "mistral:7b",
        embedding_model: str = "nomic-embed-text",
        timeout: float = 300.0,
        conversation_model: str = "",
        config_provider: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        """Initialize Ollama client.

        The arguments are the baseline *config_provider*'s ``ollama`` section
        overlays; an empty *conversation_model* means the generation model.
        Raises ImportError when the ollama package is missing.
        """
        if Client is None:
            raise ImportError("ollama package is not installed")

        self.timeout = timeout
        self._config_provider = config_provider
        self._baseline = _OllamaSettings(
            base_url=base_url,
            default_model=default_model,
            embedding_model=embedding_model,
            # Kept raw: folding the fallback in here would pin chat to
            # whichever generation model was configured at boot.
            conversation_model=conversation_model,
        )
        self._client: Client | None = None
        self._client_host: str | None = None

    @property
    def base_url(self) -> str:
        """The Ollama URL the next call will be sent to."""
        return self._resolved().base_url

    @property
    def default_model(self) -> str:
        """The currently configured model for text generation."""
        return self._resolved().default_model

    @property
    def embedding_model(self) -> str:
        """The currently configured model for embeddings."""
        return self._resolved().embedding_model

    @property
    def conversation_model(self) -> str:
        """The currently configured chat model, or the generation model."""
        return self._resolved().conversation_model

    @property
    def client(self) -> "Client":
        """The ``ollama.Client`` for the currently configured base URL."""
        return self._client_for(self.base_url)

    def _resolved(self) -> _OllamaSettings:
        """Overlay the running ``ollama`` section onto the baseline, in one read.

        One read: a call uses the URL and a model together, and reading again
        in between would let a save land inside one request.
        """
        section = self._ollama_config()
        baseline = self._baseline
        default_model = _text(section.get("model"), baseline.default_model)
        return _OllamaSettings(
            base_url=_text(section.get("base_url"), baseline.base_url),
            default_model=default_model,
            embedding_model=_text(
                section.get("embedding_model"), baseline.embedding_model
            ),
            conversation_model=_text(
                section.get("conversation_model"), baseline.conversation_model
            )
            or default_model,
        )

    def _ollama_config(self) -> dict[str, Any]:
        """Return the running ``ollama`` section, or an empty mapping.

        Type-guarded rather than defaulted: an ``ollama:`` header with no
        children parses to ``None``, which ``dict.get``'s default never
        catches because the key is present.
        """
        config = self._config_provider() if self._config_provider is not None else None
        if config is None:
            return {}
        section = config.get("ollama")
        return section if isinstance(section, dict) else {}

    def _client_for(self, base_url: str) -> "Client":
        """Return the transport for *base_url*, rebuilt when the URL changes.

        Cached because one per call is one connection pool per call. Two
        threads racing here each build one and the last stored wins, costing
        a socket.
        """
        if self._client is None or self._client_host != base_url:
            if not is_local_url(base_url):
                logger.warning(
                    "Ollama base URL %s is not on this machine or network — "
                    "prompts, titles, ratings and reviews are being sent there. "
                    "Only config.yaml can set that.",
                    base_url,
                )
            self._client = Client(host=base_url, timeout=self.timeout)
            self._client_host = base_url
        return self._client

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
        settings = self._resolved()
        model = model or settings.embedding_model

        try:
            response = self._client_for(settings.base_url).embeddings(
                model=model, prompt=text
            )
            embedding: list[float] = response.get("embedding", [])
            if not embedding:
                raise RuntimeError(f"No embedding returned from model {model}")
            return embedding
        except Exception as error:
            logger.error("Failed to generate embedding: %s", error)
            raise RuntimeError("Embedding generation failed") from error

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
            except Exception as error:
                logger.error("Failed to generate embedding for text: %s", error)
                raise

        return embeddings

    @staticmethod
    def _build_options(
        temperature: float,
        max_tokens: int | None = None,
        context_window_size: int | None = None,
    ) -> dict[str, Any]:
        """Build Ollama options dict from common parameters."""
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        if context_window_size is not None:
            options["num_ctx"] = context_window_size
        return options

    @staticmethod
    def _iter_stream_chunks(response: Any) -> Iterator[str]:
        """Yield text content from a streaming Ollama response."""
        for chunk in response:
            if hasattr(chunk, "message") and chunk.message:
                content = chunk.message.content
                if content:
                    yield content

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        context_window_size: int | None = None,
    ) -> str:
        """Generate text using the LLM.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            model: Model to use (defaults to default_model)
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            context_window_size: Override Ollama's default context window

        Returns:
            Generated text

        Raises:
            RuntimeError: If text generation fails
        """
        settings = self._resolved()
        model = model or settings.default_model

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            options = self._build_options(
                temperature, max_tokens, context_window_size=context_window_size
            )
            response = self._client_for(settings.base_url).chat(
                model=model, messages=messages, options=options
            )

            content: str = response.get("message", {}).get("content", "")
            if not content:
                raise RuntimeError(f"No content returned from model {model}")

            return content
        except Exception as error:
            logger.error("Failed to generate text: %s", error)
            raise RuntimeError("Text generation failed") from error

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
                    model_entry.model
                    for model_entry in models_attr
                    if hasattr(model_entry, "model") and model_entry.model is not None
                ]
            return []
        except Exception as error:
            logger.error("Failed to list models: %s", error)
            return []

    def generate_text_stream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        context_window_size: int | None = None,
    ) -> Iterator[str]:
        """Generate text using the LLM with streaming response.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            model: Model to use (defaults to default_model)
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            context_window_size: Override Ollama's default context window

        Yields:
            Text chunks as they are generated

        Raises:
            RuntimeError: If text generation fails
        """
        settings = self._resolved()
        model = model or settings.default_model

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            options = self._build_options(
                temperature, max_tokens, context_window_size=context_window_size
            )
            response = self._client_for(settings.base_url).chat(
                model=model, messages=messages, options=options, stream=True
            )

            yield from self._iter_stream_chunks(response)

        except Exception as error:
            logger.error("Failed to generate streaming text: %s", error)
            raise RuntimeError("Streaming text generation failed") from error

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        context_window_size: int | None = None,
    ) -> Iterator[str]:
        """Multi-turn chat with streaming response.

        Args:
            messages: List of message dicts with 'role' and 'content'
            system_prompt: Optional system prompt (prepended to messages)
            model: Model to use (defaults to default_model)
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            context_window_size: Override Ollama's default context window

        Yields:
            Text chunks as they are generated

        Raises:
            RuntimeError: If chat fails
        """
        settings = self._resolved()
        model = model or settings.default_model

        try:
            full_messages = []
            if system_prompt:
                full_messages.append({"role": "system", "content": system_prompt})
            full_messages.extend(messages)

            options = self._build_options(
                temperature, max_tokens, context_window_size=context_window_size
            )
            response = self._client_for(settings.base_url).chat(
                model=model, messages=full_messages, options=options, stream=True
            )

            yield from self._iter_stream_chunks(response)

        except Exception as error:
            logger.error("Failed to stream chat: %s", error)
            raise RuntimeError("Chat streaming failed") from error
