"""Conversation engine for AI-powered interactions."""

import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING

from src.conversation.context import ContextAssembler, build_user_context_block
from src.conversation.memory import MemoryManager
from src.conversation.tools import (
    ToolExecutor,
    get_tool_descriptions,
    parse_tool_call_from_text,
)
from src.models.content import ContentType
from src.models.conversation import ConversationChunk, ConversationContext, ToolResult

if TYPE_CHECKING:
    from src.llm.client import OllamaClient
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


# Default system prompt template
DEFAULT_SYSTEM_PROMPT = """You are an enthusiastic personal recommendation advisor with deep knowledge across books, movies, TV shows, and video games. You have access to the user's complete consumption history, ratings, and stated preferences.

## Your Personality
- Enthusiastic and encouraging, but grounded in specifics
- You explain WHY something fits, not just THAT it fits
- You reference the user's actual history ("Since you loved RDR2's story...")
- You're honest about potential downsides
- You commit to recommendations confidently - pick ONE, don't hedge with "you might like"

## Pattern Recognition
When analyzing preferences, consider:
- What they COMPLETED vs ABANDONED (abandoned items reveal dislikes)
- What gets 5 stars vs 3 stars (find the patterns)
- Genre preferences that span content types
- Themes they gravitate toward (exploration, narrative, etc.)
- Anti-patterns: what they explicitly dislike or avoid

## Your Capabilities
You can help users:
- Get personalized recommendations based on their taste
- Mark items as completed with ratings
- Update ratings and reviews
- Add items to their wishlist
- Remember their preferences for future conversations

## Response Style
- Lead with your recommendation, don't bury it
- Connect to their demonstrated preferences with specific examples
- Set realistic expectations
- Be conversational but organized
- Use clear structure (headers, bullets) for longer responses
- Use the user's own language patterns when possible
- Emojis are OK but use sparingly

## When Recommending
1. **State your pick confidently** - "Play Outer Wilds next" not "You might enjoy..."
2. **Explain the connection** - "This will hit you the same way Firewatch did..."
3. **Set expectations** - tone, length, pacing, what to expect
4. **Note caveats honestly** - "Fair warning: the first 2 hours can feel slow"
5. **Generate excitement** - describe the experience they'll have

## What NOT To Do
- Don't say "immersive" or "engaging" without specifics
- Don't list features - explain experiences
- Don't give 3 equal options when asked for a recommendation
- Don't ignore their stated dislikes
- Don't be generic - reference their specific history

## Available Tools
{tool_descriptions}

When the user mentions completing something or wanting to update data, use the appropriate tool.
When multiple items might match a title, use clarify_item to ask which one.
When the user states a preference explicitly, use save_memory to remember it.

## User Context
{user_context}
"""


class ConversationEngine:
    """Main orchestrator for conversational AI.

    Coordinates context assembly, LLM interaction, tool execution,
    and memory extraction for conversational recommendations.
    """

    def __init__(
        self,
        storage_manager: "StorageManager",
        ollama_client: "OllamaClient",
        memory_manager: MemoryManager | None = None,
        context_assembler: ContextAssembler | None = None,
        tool_executor: ToolExecutor | None = None,
        system_prompt_template: str | None = None,
    ) -> None:
        """Initialize the conversation engine.

        Args:
            storage_manager: Storage manager for database operations
            ollama_client: Ollama client for LLM interactions
            memory_manager: Memory manager (created if not provided)
            context_assembler: Context assembler (created if not provided)
            tool_executor: Tool executor (created if not provided)
            system_prompt_template: Custom system prompt template
        """
        self.storage = storage_manager
        self.ollama = ollama_client

        self.memory = memory_manager or MemoryManager(storage_manager)
        self.context = context_assembler or ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=self.memory,
            ollama_client=ollama_client,
        )
        self.tools = tool_executor or ToolExecutor(storage_manager)

        self.system_prompt_template = system_prompt_template or DEFAULT_SYSTEM_PROMPT

    def process_message(
        self,
        user_id: int,
        message: str,
        content_type: ContentType | None = None,
        stream: bool = True,
    ) -> Iterator[ConversationChunk]:
        """Process a user message and stream response.

        Args:
            user_id: User ID
            message: User's message
            content_type: Optional content type filter for context
            stream: Whether to stream the response

        Yields:
            ConversationChunks for text, tool calls, results, and completion
        """
        # 1. Save user message to history
        self.memory.save_conversation_message(
            user_id=user_id,
            role="user",
            content=message,
        )

        # 2. Assemble context
        context = self.context.assemble_context(
            user_id=user_id,
            user_query=message,
            content_type=content_type,
        )

        # 3. Build system prompt with tools and context
        user_context_block = build_user_context_block(context)
        tool_descriptions = get_tool_descriptions()

        system_prompt = self.system_prompt_template.format(
            tool_descriptions=tool_descriptions,
            user_context=user_context_block,
        )

        # 4. Build message history for multi-turn
        messages = self._build_messages(context, message)

        # 5. Stream LLM response
        full_response = ""
        tool_calls_made: list[dict] = []

        try:
            if stream:
                for chunk in self.ollama.chat_stream(
                    messages=messages,
                    system_prompt=system_prompt,
                    temperature=0.7,
                ):
                    full_response += chunk
                    yield ConversationChunk(
                        chunk_type="text",
                        content=chunk,
                    )
            else:
                # Non-streaming fallback
                full_response = self.ollama.generate_text(
                    prompt=message,
                    system_prompt=system_prompt,
                    temperature=0.7,
                )
                yield ConversationChunk(
                    chunk_type="text",
                    content=full_response,
                )

        except Exception as error:
            logger.error(f"LLM generation failed: {error}")
            error_message = (
                "I'm having trouble connecting to the AI. "
                "Please make sure Ollama is running and try again."
            )
            yield ConversationChunk(
                chunk_type="text",
                content=error_message,
            )
            full_response = error_message

        # 6. Check for tool calls in response
        tool_name, tool_params = parse_tool_call_from_text(full_response)
        if tool_name and tool_params:
            yield ConversationChunk(
                chunk_type="tool_call",
                tool_name=tool_name,
                tool_params=tool_params,
            )

            # Execute the tool
            result = self.tools.execute(tool_name, tool_params, user_id)
            tool_calls_made.append(
                {
                    "name": tool_name,
                    "params": tool_params,
                    "result": {
                        "success": result.success,
                        "message": result.message,
                    },
                }
            )

            yield ConversationChunk(
                chunk_type="tool_result",
                tool_name=tool_name,
                tool_result=result,
            )

            # If tool needs clarification, that's part of the response
            if result.needs_clarification:
                clarification_text = self._format_clarification(result)
                full_response += f"\n\n{clarification_text}"
                yield ConversationChunk(
                    chunk_type="text",
                    content=f"\n\n{clarification_text}",
                )

        # 7. Save assistant message (with tool calls if any)
        self.memory.save_conversation_message(
            user_id=user_id,
            role="assistant",
            content=full_response,
            tool_calls=tool_calls_made if tool_calls_made else None,
        )

        # 8. Signal completion
        yield ConversationChunk(chunk_type="done")

    def process_message_sync(
        self,
        user_id: int,
        message: str,
        content_type: ContentType | None = None,
    ) -> str:
        """Process a message and return the full response (non-streaming).

        Args:
            user_id: User ID
            message: User's message
            content_type: Optional content type filter

        Returns:
            Full response text
        """
        chunks = list(
            self.process_message(
                user_id=user_id,
                message=message,
                content_type=content_type,
                stream=False,
            )
        )

        # Combine text chunks
        text_parts = [
            chunk.content
            for chunk in chunks
            if chunk.chunk_type == "text" and chunk.content
        ]
        return "".join(text_parts)

    def reset_conversation(self, user_id: int) -> int:
        """Clear conversation history for a user.

        Note: This clears the conversation but preserves core memories.

        Args:
            user_id: User ID

        Returns:
            Number of messages cleared
        """
        return self.memory.clear_conversation_history(user_id)

    def _build_messages(
        self,
        context: ConversationContext,
        current_message: str,
    ) -> list[dict[str, str]]:
        """Build message list for multi-turn conversation.

        Args:
            context: Conversation context with history
            current_message: Current user message

        Returns:
            List of message dicts for the LLM
        """
        messages = []

        # Add recent history
        for msg in context.recent_messages:
            messages.append(
                {
                    "role": msg.role,
                    "content": msg.content,
                }
            )

        # Add current message
        messages.append(
            {
                "role": "user",
                "content": current_message,
            }
        )

        return messages

    def _format_clarification(self, result: ToolResult) -> str:
        """Format a clarification request for the user.

        Args:
            result: ToolResult with clarification options

        Returns:
            Formatted clarification text
        """
        if not result.clarification_options:
            return result.message

        lines = [result.message, ""]
        for i, option in enumerate(result.clarification_options, 1):
            title = option.get("title", "Unknown")
            author = option.get("author", "")
            content_type = option.get("content_type", "")
            item_id = option.get("id", "")

            author_str = f" by {author}" if author else ""
            type_str = f" ({content_type})" if content_type else ""
            lines.append(f"{i}. {title}{author_str}{type_str} [ID: {item_id}]")

        return "\n".join(lines)


def create_conversation_engine(
    storage_manager: "StorageManager",
    ollama_client: "OllamaClient",
) -> ConversationEngine:
    """Factory function to create a fully configured ConversationEngine.

    Args:
        storage_manager: Storage manager for database operations
        ollama_client: Ollama client for LLM interactions

    Returns:
        Configured ConversationEngine
    """
    return ConversationEngine(
        storage_manager=storage_manager,
        ollama_client=ollama_client,
    )
