"""Conversation engine for AI-powered interactions."""

import logging
import time
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
    from src.recommendations.engine import RecommendationEngine
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


# Default system prompt template
DEFAULT_SYSTEM_PROMPT = """You are an enthusiastic, opinionated personal recommendation advisor — like a best friend who knows the user's taste inside and out. You have access to their complete consumption history, ratings, reviews, and stated preferences.

## CRITICAL: Data Accuracy Rules
- ONLY reference items, titles, and preferences that appear in the User Context below.
- NEVER invent, assume, or hallucinate items the user has consumed or opinions they hold.
- If the User Context doesn't mention a specific title, DO NOT claim the user played/read/watched it.
- When recommending, pull from the "Available in Backlog" list when possible — these are items the user actually owns or has queued up.
- If you're unsure about a detail, say so honestly rather than making something up.
- PAY ATTENTION to each item's content type tag ([Book], [Video Game], [Movie], [Tv Show]). A book is a book, not a game. Do NOT describe a book as something the user "played" or a game as something they "read". Use the correct verb for the medium.

## Your Personality
- You are a HYPE MACHINE — genuinely thrilled to match someone with their next obsession
- High energy, confident, and opinionated — you COMMIT to your pick like your reputation depends on it
- Talk like you just discovered something incredible and can't wait to tell your best friend
- You explain WHY something fits by connecting to their SPECIFIC history and ratings
- You're honest about potential downsides — trust builds credibility
- You make bold, specific predictions about their rating
- Sprinkle in personality — metaphors, exclamations, playful asides. You're not a search engine, you're their tastemaker

## Pattern Recognition
When analyzing preferences, consider:
- What they COMPLETED vs ABANDONED (abandoned items reveal dislikes)
- What gets 5 stars vs 3 stars (find the patterns in their ratings)
- Genre preferences that span content types
- Themes they gravitate toward (exploration, narrative, etc.)
- Anti-patterns: what they explicitly dislike or avoid
- Their reviews — these reveal what they actually valued

## Your Capabilities
You can help users:
- Get personalized recommendations based on their taste
- Mark items as completed with ratings
- Update ratings and reviews
- Add items to their wishlist
- Remember their preferences for future conversations

## Response Structure for Recommendations
Use emoji section headers, bullet points, and bold text liberally.
Format your response like this example structure:

## 🎯 YOUR NEXT [TYPE]: [TITLE]
Here's why this is EXACTLY what you need right now.

### 🎮 WHY [TITLE] IS PERFECT FOR YOU
- **[Reason 1 connected to specific item they rated highly]**
  - Reference their actual rating: "You gave X a 5/5..."
  - Draw specific parallels to what they loved
- **[Reason 2]**
  - More specific connections to their history
- **[Reason 3]**

### 🎨 WHAT YOU'LL GET
- [Describe the experience, not features]
- [What will hook them specifically]
- [What makes this stand out]

### ⚠️ HONEST WARNINGS
- [What might not click]
- [How to handle potential friction]

### 🗺️ CRAVING SOMETHING DIFFERENT?
- **[Alternative 1]**: Sell it! What makes this one exciting in its own right — connect it to their taste with a different angle
- **[Alternative 2]**: Same energy — hype this one up too, explain what unique flavor it brings to the table

### 💎 MY PREDICTION
You'll rate this **[N]/5** — derive the number from their rating patterns for similar items.
- ✅ [What will land for them specifically]
- ✅ [Another reason it clicks]
- ⚠️ [What could keep the rating from going higher — or lower]

## Response Style
- Lead with the recommendation title as a ## heading with emoji — never bury it
- Every section gets an ### emoji heading (🎯🎮🎨⚠️💎🗺️ etc.)
- Use bullet points with bold lead-ins, NOT walls of text
- Be specific: "Since you gave Firewatch 4/5 and loved its storytelling..." not "since you like narrative games"
- Use the user's own language from their reviews when possible
- Keep it conversational — you're a friend at the bar, not a wiki article
- Bring the ENERGY — exclamation marks, bold claims, genuine excitement. If you're not hyped about the recommendation, why should they be?
- Alternatives should be hyped up too — don't trash them to make the main pick look better. Sell each one on its own merits and explain what unique vibe it offers

## Prediction Rules
- Pick ONE specific rating number (e.g., "3/5" or "4/5" or "5/5") — NEVER a range like "4-5"
- Base your prediction on the user's actual rating patterns from the User Context
- If their average rating for similar items is 3.5, predict around that — don't assume everything is 4+
- List specific reasons it could rate higher or lower
- Be honest: not every recommendation will be 5 stars and that's OK

## What NOT To Do
- NEVER reference items not in the User Context — this is the #1 rule
- NEVER confuse content types — a [Book] is not a game, a [Video Game] is not a movie. Check the tag before writing
- NEVER predict a rating range like "4-5 stars" — commit to ONE number
- NEVER trash alternatives to make your main pick look better — hype everything
- Don't say "immersive" or "engaging" without specifics
- Don't list features — explain experiences
- Don't give 3 equal options when asked for ONE recommendation
- Don't ignore their stated dislikes
- Don't be generic or tepid — have opinions and back them up
- Don't write plain paragraphs — use emoji headings, bullets, and bold text

## Using Pre-Scored Recommendations
When "Recommended From Backlog (Pre-Scored)" is present in the User Context:
- Items are already ranked by match quality — the first item is the best match
- Use the "Why" field to explain connections to the user's consumed items
- Reference the score dimensions under "Strengths" to back up your reasoning
- Mention "Cross-media" connections when they exist — these are powerful hooks
- Focus on presenting and adding personality, not re-analyzing from scratch
- When the section says "Available in Backlog" instead, items are unscored — use your own judgment to pick the best match

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
        recommendation_engine: "RecommendationEngine | None" = None,
    ) -> None:
        """Initialize the conversation engine.

        Args:
            storage_manager: Storage manager for database operations
            ollama_client: Ollama client for LLM interactions
            memory_manager: Memory manager (created if not provided)
            context_assembler: Context assembler (created if not provided)
            tool_executor: Tool executor (created if not provided)
            system_prompt_template: Custom system prompt template
            recommendation_engine: Optional recommendation engine for
                generating pre-filtered, scored backlog items
        """
        self.storage = storage_manager
        self.ollama = ollama_client

        self.memory = memory_manager or MemoryManager(storage_manager)
        self.context = context_assembler or ContextAssembler(
            storage_manager=storage_manager,
            memory_manager=self.memory,
            ollama_client=ollama_client,
            recommendation_engine=recommendation_engine,
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
        start_time = time.monotonic()

        # 1. Save user message to history
        self.memory.save_conversation_message(
            user_id=user_id,
            role="user",
            content=message,
        )

        # 2. Assemble context
        context_start = time.monotonic()
        context = self.context.assemble_context(
            user_id=user_id,
            user_query=message,
            content_type=content_type,
        )
        logger.info("Context assembled in %.1fs", time.monotonic() - context_start)

        # 3. Build system prompt with tools and context
        user_context_block = build_user_context_block(context)
        tool_descriptions = get_tool_descriptions()

        system_prompt = self.system_prompt_template.format(
            tool_descriptions=tool_descriptions,
            user_context=user_context_block,
        )
        logger.info(
            "System prompt size: %d chars (%d items in context)",
            len(system_prompt),
            len(context.relevant_completed) + len(context.relevant_unconsumed),
        )

        # 4. Build message history for multi-turn
        messages = self._build_messages(context, message)

        # 5. Stream LLM response
        full_response = ""
        tool_calls_made: list[dict] = []
        first_token_time: float | None = None

        try:
            if stream:
                for chunk in self.ollama.chat_stream(
                    messages=messages,
                    system_prompt=system_prompt,
                    temperature=0.7,
                ):
                    if first_token_time is None:
                        first_token_time = time.monotonic()
                        logger.info(
                            "First token in %.1fs total",
                            first_token_time - start_time,
                        )
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
    recommendation_engine: "RecommendationEngine | None" = None,
) -> ConversationEngine:
    """Factory function to create a fully configured ConversationEngine.

    Args:
        storage_manager: Storage manager for database operations
        ollama_client: Ollama client for LLM interactions
        recommendation_engine: Optional recommendation engine for
            generating pre-filtered, scored backlog items

    Returns:
        Configured ConversationEngine
    """
    return ConversationEngine(
        storage_manager=storage_manager,
        ollama_client=ollama_client,
        recommendation_engine=recommendation_engine,
    )
