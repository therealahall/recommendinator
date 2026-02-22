"""Tool definitions and executor for conversation system."""

import json
import logging
from collections.abc import Callable
from datetime import date
from typing import TYPE_CHECKING, Any

from src.models.content import (
    ConsumptionStatus,
    ContentItem,
    ContentType,
    get_enum_value,
)
from src.models.conversation import ToolResult

if TYPE_CHECKING:
    from src.storage.manager import StorageManager

logger = logging.getLogger(__name__)


# Tool definitions for LLM function calling
CONVERSATION_TOOLS = [
    {
        "name": "mark_completed",
        "description": "Mark a content item as completed with optional rating and review",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "integer",
                    "description": "Database ID of the item",
                },
                "rating": {
                    "type": "integer",
                    "description": "Optional 1-5 rating",
                    "minimum": 1,
                    "maximum": 5,
                },
                "review": {
                    "type": "string",
                    "description": "Optional review text",
                },
                "date_completed": {
                    "type": "string",
                    "description": "Optional completion date (YYYY-MM-DD)",
                },
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "update_rating",
        "description": "Update the rating for a completed item",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "integer",
                    "description": "Database ID of the item",
                },
                "rating": {
                    "type": "integer",
                    "description": "New 1-5 rating",
                    "minimum": 1,
                    "maximum": 5,
                },
                "review": {
                    "type": "string",
                    "description": "Optional updated review",
                },
            },
            "required": ["item_id", "rating"],
        },
    },
    {
        "name": "add_to_wishlist",
        "description": "Add a new item to the user's wishlist/backlog",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title of the content",
                },
                "content_type": {
                    "type": "string",
                    "description": "Type: book, movie, tv_show, or video_game",
                    "enum": ["book", "movie", "tv_show", "video_game"],
                },
                "author": {
                    "type": "string",
                    "description": "Optional author/director/developer",
                },
            },
            "required": ["title", "content_type"],
        },
    },
    {
        "name": "clarify_item",
        "description": "Ask user to clarify which item they mean when multiple matches found",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What the user said",
                },
                "matches": {
                    "type": "array",
                    "description": "List of matching items with IDs and details",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "title": {"type": "string"},
                            "author": {"type": "string"},
                            "content_type": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["query", "matches"],
        },
    },
    {
        "name": "save_memory",
        "description": "Save a user-stated preference or memory",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_text": {
                    "type": "string",
                    "description": "The preference statement",
                },
            },
            "required": ["memory_text"],
        },
    },
    {
        "name": "search_items",
        "description": "Search for items by title",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (partial title match)",
                },
                "content_type": {
                    "type": "string",
                    "description": "Optional type filter: book, movie, tv_show, video_game",
                    "enum": ["book", "movie", "tv_show", "video_game"],
                },
            },
            "required": ["query"],
        },
    },
]


def get_tool_descriptions() -> str:
    """Get formatted tool descriptions for inclusion in prompts.

    Returns:
        Formatted string describing available tools
    """
    descriptions = []
    for tool in CONVERSATION_TOOLS:
        name = tool["name"]
        desc = tool["description"]
        params_obj = tool.get("parameters", {})
        params: dict[str, Any] = {}
        if isinstance(params_obj, dict):
            props = params_obj.get("properties")
            if isinstance(props, dict):
                params = props
        param_list = ", ".join(
            f"{k}: {v.get('type', 'any') if isinstance(v, dict) else 'any'}"
            for k, v in params.items()
        )
        descriptions.append(f"- **{name}**({param_list}): {desc}")
    return "\n".join(descriptions)


class ToolExecutor:
    """Executes tool calls from LLM responses."""

    def __init__(self, storage_manager: "StorageManager") -> None:
        """Initialize the tool executor.

        Args:
            storage_manager: Storage manager for database operations
        """
        self.storage = storage_manager

    def execute(
        self, tool_name: str, params: dict[str, Any], user_id: int
    ) -> ToolResult:
        """Execute a tool and return the result.

        Args:
            tool_name: Name of the tool to execute
            params: Tool parameters
            user_id: User ID for the operation

        Returns:
            ToolResult with success status and data
        """
        handler = self._get_handler(tool_name)
        if not handler:
            return ToolResult(
                success=False,
                message=f"Unknown tool: {tool_name}",
            )

        try:
            return handler(params, user_id)
        except Exception as error:
            logger.error(f"Tool {tool_name} failed: {error}")
            return ToolResult(
                success=False,
                message=f"Failed to execute {tool_name}. Please try again.",
            )

    def _get_handler(
        self, tool_name: str
    ) -> "Callable[[dict[str, Any], int], ToolResult] | None":
        """Get the handler function for a tool.

        Args:
            tool_name: Name of the tool

        Returns:
            Handler function or None
        """
        handlers: dict[str, Callable[[dict[str, Any], int], ToolResult]] = {
            "mark_completed": self._handle_mark_completed,
            "update_rating": self._handle_update_rating,
            "add_to_wishlist": self._handle_add_to_wishlist,
            "clarify_item": self._handle_clarify_item,
            "save_memory": self._handle_save_memory,
            "search_items": self._handle_search_items,
        }
        return handlers.get(tool_name)

    def _handle_mark_completed(
        self, params: dict[str, Any], user_id: int
    ) -> ToolResult:
        """Mark an item as completed.

        Args:
            params: Tool parameters (item_id, rating, review, date_completed)
            user_id: User ID

        Returns:
            ToolResult
        """
        item_id = params.get("item_id")
        if item_id is None:
            return ToolResult(success=False, message="item_id is required")

        # Get the item
        item = self.storage.get_content_item(item_id, user_id=user_id)
        if not item:
            return ToolResult(success=False, message=f"Item {item_id} not found")

        # Update fields
        item.status = ConsumptionStatus.COMPLETED

        if "rating" in params and params["rating"] is not None:
            rating = params["rating"]
            if not (1 <= rating <= 5):
                return ToolResult(success=False, message="Rating must be between 1-5")
            item.rating = rating

        if "review" in params and params["review"]:
            item.review = params["review"]

        if "date_completed" in params and params["date_completed"]:
            try:
                item.date_completed = date.fromisoformat(params["date_completed"])
            except ValueError:
                return ToolResult(
                    success=False,
                    message="Invalid date format. Use YYYY-MM-DD",
                )
        else:
            item.date_completed = date.today()

        # Save the updated item
        self.storage.save_content_item(item, user_id=user_id)

        rating_text = f" with {item.rating}/5" if item.rating else ""
        return ToolResult(
            success=True,
            message=f"Marked '{item.title}' as completed{rating_text}",
            data={
                "item_id": item_id,
                "title": item.title,
                "rating": item.rating,
                "date_completed": str(item.date_completed),
            },
        )

    def _handle_update_rating(self, params: dict[str, Any], user_id: int) -> ToolResult:
        """Update the rating for an item.

        Args:
            params: Tool parameters (item_id, rating, review)
            user_id: User ID

        Returns:
            ToolResult
        """
        item_id = params.get("item_id")
        rating = params.get("rating")

        if not item_id:
            return ToolResult(success=False, message="item_id is required")
        if rating is None:
            return ToolResult(success=False, message="rating is required")
        if not (1 <= rating <= 5):
            return ToolResult(success=False, message="Rating must be between 1-5")

        # Get the item
        item = self.storage.get_content_item(item_id, user_id=user_id)
        if not item:
            return ToolResult(success=False, message=f"Item {item_id} not found")

        old_rating = item.rating
        item.rating = rating

        if "review" in params and params["review"]:
            item.review = params["review"]

        # Save the updated item
        self.storage.save_content_item(item, user_id=user_id)

        change_text = ""
        if old_rating:
            change_text = f" (was {old_rating}/5)"

        return ToolResult(
            success=True,
            message=f"Updated '{item.title}' to {rating}/5{change_text}",
            data={
                "item_id": item_id,
                "title": item.title,
                "old_rating": old_rating,
                "new_rating": rating,
            },
        )

    def _handle_add_to_wishlist(
        self, params: dict[str, Any], user_id: int
    ) -> ToolResult:
        """Add an item to the wishlist/backlog.

        Args:
            params: Tool parameters (title, content_type, author)
            user_id: User ID

        Returns:
            ToolResult
        """
        title = params.get("title")
        content_type_str = params.get("content_type")

        if not title:
            return ToolResult(success=False, message="title is required")
        if not content_type_str:
            return ToolResult(success=False, message="content_type is required")

        try:
            content_type = ContentType.from_string(content_type_str)
        except ValueError:
            content_type = None
        if not content_type:
            return ToolResult(
                success=False,
                message=f"Invalid content_type: {content_type_str}",
            )

        # Create the item
        # Note: Pydantic models have defaults for rating, but mypy doesn't see them
        item = ContentItem(
            title=title,
            content_type=content_type,
            status=ConsumptionStatus.UNREAD,
            author=params.get("author"),
            source="conversation",
            rating=None,  # Explicit for mypy
        )

        # Save it
        db_id = self.storage.save_content_item(item, user_id=user_id)

        return ToolResult(
            success=True,
            message=f"Added '{title}' to your {content_type_str.replace('_', ' ')} backlog",
            data={
                "item_id": db_id,
                "title": title,
                "content_type": content_type_str,
            },
        )

    def _handle_clarify_item(self, params: dict[str, Any], user_id: int) -> ToolResult:
        """Handle ambiguous item references by asking for clarification.

        Args:
            params: Tool parameters (query, matches)
            user_id: User ID (unused but kept for interface consistency)

        Returns:
            ToolResult with clarification options
        """
        query = params.get("query", "")
        matches = params.get("matches", [])

        if not matches:
            return ToolResult(
                success=False,
                message="No matches provided for clarification",
            )

        return ToolResult(
            success=True,
            message=f"Multiple items match '{query}'. Please clarify:",
            needs_clarification=True,
            clarification_options=matches,
        )

    def _handle_save_memory(self, params: dict[str, Any], user_id: int) -> ToolResult:
        """Save a user-stated preference as a core memory.

        Args:
            params: Tool parameters (memory_text)
            user_id: User ID

        Returns:
            ToolResult
        """
        memory_text = params.get("memory_text")
        if not memory_text:
            return ToolResult(success=False, message="memory_text is required")

        memory_id = self.storage.save_core_memory(
            user_id=user_id,
            memory_text=memory_text,
            memory_type="user_stated",
            source="conversation",
        )

        return ToolResult(
            success=True,
            message=f"Noted: {memory_text}",
            data={"memory_id": memory_id, "memory_text": memory_text},
        )

    def _handle_search_items(self, params: dict[str, Any], user_id: int) -> ToolResult:
        """Search for items by title.

        Args:
            params: Tool parameters (query, content_type)
            user_id: User ID

        Returns:
            ToolResult with matching items
        """
        query = params.get("query", "").lower()
        content_type_str = params.get("content_type")

        if not query:
            return ToolResult(success=False, message="query is required")

        # Map content type if provided
        content_type = None
        if content_type_str:
            try:
                content_type = ContentType.from_string(content_type_str)
            except ValueError:
                pass

        # Get items and filter by title
        items = self.storage.get_content_items(
            user_id=user_id,
            content_type=content_type,
            limit=100,  # Get a reasonable number
        )

        # Filter by query
        matches = []
        for item in items:
            if query in item.title.lower():
                matches.append(
                    {
                        "id": item.db_id,
                        "title": item.title,
                        "author": item.author,
                        "content_type": get_enum_value(item.content_type),
                        "status": get_enum_value(item.status),
                        "rating": item.rating,
                    }
                )

        if not matches:
            return ToolResult(
                success=True,
                message=f"No items found matching '{query}'",
                data={"matches": []},
            )

        # If exactly one match, return it
        if len(matches) == 1:
            return ToolResult(
                success=True,
                message=f"Found: {matches[0]['title']}",
                data={"matches": matches},
            )

        # Multiple matches - might need clarification
        return ToolResult(
            success=True,
            message=f"Found {len(matches)} items matching '{query}'",
            data={"matches": matches[:10]},  # Limit to 10 results
            needs_clarification=len(matches) > 1,
            clarification_options=matches[:10] if len(matches) > 1 else None,
        )

    def find_matching_items(
        self,
        query: str,
        user_id: int,
        content_type: ContentType | None = None,
    ) -> list[ContentItem]:
        """Find items matching a title query.

        Useful for identifying items when user mentions them by name.

        Args:
            query: Title query (partial match)
            user_id: User ID
            content_type: Optional content type filter

        Returns:
            List of matching ContentItem objects
        """
        query_lower = query.lower()

        items = self.storage.get_content_items(
            user_id=user_id,
            content_type=content_type,
            limit=200,
        )

        matches = [item for item in items if query_lower in item.title.lower()]

        return matches


def parse_tool_call_from_text(text: str) -> tuple[str | None, dict[str, Any] | None]:
    """Attempt to parse a tool call from LLM text output.

    Some models may output tool calls as JSON or structured text rather than
    using native function calling. This provides a fallback parser.

    Args:
        text: LLM response text

    Returns:
        Tuple of (tool_name, params) or (None, None) if not a tool call
    """
    # Try to find JSON in the text
    try:
        # Try to parse the entire text as JSON first
        try:
            data = json.loads(text.strip())
            if isinstance(data, dict):
                if "tool" in data or "function" in data or "name" in data:
                    tool_name = (
                        data.get("tool") or data.get("function") or data.get("name")
                    )
                    params = (
                        data.get("params")
                        or data.get("arguments")
                        or data.get("parameters", {})
                    )
                    if isinstance(params, str):
                        params = json.loads(params)
                    return tool_name, params
        except json.JSONDecodeError:
            pass

        # Look for JSON blocks in text (balanced braces)

        # Find potential JSON objects (simplistic approach)
        start_idx = text.find("{")
        if start_idx >= 0:
            # Count braces to find matching closing brace
            brace_count = 0
            end_idx = start_idx
            for i, char in enumerate(text[start_idx:], start_idx):
                if char == "{":
                    brace_count += 1
                elif char == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i
                        break

            if end_idx > start_idx:
                json_str = text[start_idx : end_idx + 1]
                data = json.loads(json_str)

                # Check if it looks like a tool call
                if "tool" in data or "function" in data or "name" in data:
                    tool_name = (
                        data.get("tool") or data.get("function") or data.get("name")
                    )
                    params = (
                        data.get("params")
                        or data.get("arguments")
                        or data.get("parameters", {})
                    )
                    if isinstance(params, str):
                        params = json.loads(params)
                    return tool_name, params

    except (json.JSONDecodeError, TypeError):
        pass

    return None, None
