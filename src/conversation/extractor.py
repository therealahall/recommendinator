"""Memory extraction from conversations."""

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from src.models.conversation import ConversationMessage, CoreMemory

if TYPE_CHECKING:
    from src.llm.client import OllamaClient

logger = logging.getLogger(__name__)


# System prompt for memory extraction
MEMORY_EXTRACTION_SYSTEM_PROMPT = """You are analyzing a conversation to extract preference signals and memories.

For each preference or significant statement, classify it as:
- "user_stated": The user explicitly said this preference
- "inferred": You're inferring this from their behavior/reactions

Output ONLY a JSON array with no other text:
[
  {
    "memory_text": "Prefers shorter games during weekdays",
    "memory_type": "user_stated",
    "confidence": 1.0
  },
  {
    "memory_text": "Tends to abandon games with grinding mechanics",
    "memory_type": "inferred",
    "confidence": 0.7
  }
]

Rules:
- Only extract meaningful, actionable preferences
- Ignore small talk and greetings
- User-stated memories should have confidence 1.0
- Inferred memories should have confidence 0.5-0.9 based on evidence strength
- If no preferences found, return an empty array: []
- Do not include explanations, only the JSON array
"""


class MemoryExtractor:
    """Extracts memories and preferences from conversations."""

    def __init__(
        self,
        ollama_client: "OllamaClient",
        model: str | None = None,
    ) -> None:
        """Initialize the memory extractor.

        Args:
            ollama_client: Ollama client for LLM interactions
            model: Model to use for extraction (defaults to client's default)
        """
        self.ollama = ollama_client
        self.model = model

    def extract_from_conversation(
        self,
        messages: list[ConversationMessage],
        user_id: int,
    ) -> list[CoreMemory]:
        """Extract memories and preferences from a conversation.

        Runs a secondary LLM pass to extract:
        - User-stated preferences ("I don't like slow burns")
        - Inferred patterns (user consistently rates X type highly)
        - Feedback on recommendations (accepted, rejected, why)

        Args:
            messages: List of conversation messages to analyze
            user_id: User ID for the memories

        Returns:
            List of CoreMemory objects with type and confidence scores
        """
        if not messages:
            return []

        # Build the extraction prompt
        prompt = self._build_extraction_prompt(messages)

        try:
            # Get LLM response
            response = self.ollama.generate_text(
                prompt=prompt,
                system_prompt=MEMORY_EXTRACTION_SYSTEM_PROMPT,
                model=self.model,
                temperature=0.3,  # Lower temperature for more consistent extraction
            )

            # Parse the response
            extracted = self._parse_extraction_response(response)

            # Convert to CoreMemory objects
            memories = []
            for item in extracted:
                memory = CoreMemory(
                    user_id=user_id,
                    memory_text=item["memory_text"],
                    memory_type=item["memory_type"],
                    source="conversation",
                    confidence=item.get("confidence", 1.0),
                )
                memories.append(memory)

            return memories

        except Exception as error:
            logger.error(f"Memory extraction failed: {error}")
            return []

    def extract_from_single_message(
        self,
        message: str,
        user_id: int,
    ) -> list[CoreMemory]:
        """Extract memories from a single user message.

        Useful for quick extraction without full conversation context.

        Args:
            message: The user's message
            user_id: User ID for the memories

        Returns:
            List of CoreMemory objects
        """
        fake_message = ConversationMessage(
            user_id=user_id,
            role="user",
            content=message,
        )
        return self.extract_from_conversation([fake_message], user_id)

    def _build_extraction_prompt(self, messages: list[ConversationMessage]) -> str:
        """Build the prompt for memory extraction.

        Args:
            messages: List of conversation messages

        Returns:
            Formatted prompt string
        """
        lines = ["Analyze this conversation for preference signals:\n"]

        for message in messages:
            role_label = "User" if message.role == "user" else "Assistant"
            lines.append(f"{role_label}: {message.content}")

        lines.append("\nExtract any preferences or memories as JSON:")

        return "\n".join(lines)

    def _parse_extraction_response(self, response: str) -> list[dict[str, Any]]:
        """Parse the LLM response into memory dictionaries.

        Args:
            response: Raw LLM response text

        Returns:
            List of memory dictionaries with memory_text, memory_type, confidence
        """
        # Try to find JSON array in the response
        # The response might contain extra text before/after the JSON
        response = response.strip()

        # Try direct JSON parse first
        try:
            result = json.loads(response)
            if isinstance(result, list):
                return self._validate_memories(result)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON array from the response
        # Look for content between [ and ]
        match = re.search(r"\[[\s\S]*\]", response)
        if match:
            try:
                result = json.loads(match.group())
                if isinstance(result, list):
                    return self._validate_memories(result)
            except json.JSONDecodeError:
                pass

        logger.warning(f"Could not parse extraction response: {response[:200]}")
        return []

    def _validate_memories(self, items: list[Any]) -> list[dict[str, Any]]:
        """Validate and clean extracted memory items.

        Args:
            items: Raw list of items from JSON

        Returns:
            List of valid memory dictionaries
        """
        valid_memories = []

        for item in items:
            if not isinstance(item, dict):
                continue

            # Must have memory_text
            memory_text = item.get("memory_text", "").strip()
            if not memory_text:
                continue

            # Validate memory_type
            memory_type = item.get("memory_type", "inferred")
            if memory_type not in ("user_stated", "inferred"):
                memory_type = "inferred"

            # Validate confidence
            confidence = item.get("confidence", 1.0)
            try:
                confidence = float(confidence)
                confidence = max(0.0, min(1.0, confidence))
            except (ValueError, TypeError):
                confidence = 0.5 if memory_type == "inferred" else 1.0

            valid_memories.append(
                {
                    "memory_text": memory_text,
                    "memory_type": memory_type,
                    "confidence": confidence,
                }
            )

        return valid_memories
