"""Memory management for conversation system."""

from datetime import datetime
from typing import Literal

from src.models.conversation import ConversationMessage, CoreMemory, PreferenceProfile
from src.storage.manager import StorageManager


class MemoryManager:
    """Manages core memories and conversation history.

    This class provides a high-level interface for storing and retrieving
    user memories and conversation messages. It wraps the lower-level
    storage operations and handles conversion between data models.
    """

    def __init__(self, storage_manager: StorageManager) -> None:
        """Initialize the memory manager.

        Args:
            storage_manager: Storage manager for database access
        """
        self.storage = storage_manager

    # Core Memory Operations

    def get_core_memories(
        self,
        user_id: int,
        active_only: bool = True,
        memory_type: Literal["user_stated", "inferred"] | None = None,
    ) -> list[CoreMemory]:
        """Get core memories for a user.

        Args:
            user_id: User ID
            active_only: If True, only return active memories
            memory_type: Filter by type ("user_stated" or "inferred")

        Returns:
            List of CoreMemory objects
        """
        from src.storage.schema import get_core_memories

        conn = self.storage.sqlite_db._get_connection()
        try:
            memory_dicts = get_core_memories(
                conn, user_id, active_only=active_only, memory_type=memory_type
            )
            return [
                self._dict_to_core_memory(memory_dict) for memory_dict in memory_dicts
            ]
        finally:
            conn.close()

    def save_core_memory(
        self,
        user_id: int,
        memory_text: str,
        memory_type: Literal["user_stated", "inferred"],
        source: str,
        confidence: float = 1.0,
    ) -> CoreMemory:
        """Save a new core memory.

        Args:
            user_id: User ID
            memory_text: The preference statement
            memory_type: "user_stated" or "inferred"
            source: "conversation", "rating_pattern", or "manual"
            confidence: Confidence score (0.0-1.0)

        Returns:
            The saved CoreMemory with its ID
        """
        from src.storage.schema import save_core_memory

        conn = self.storage.sqlite_db._get_connection()
        try:
            memory_id = save_core_memory(
                conn,
                user_id=user_id,
                memory_text=memory_text,
                memory_type=memory_type,
                source=source,
                confidence=confidence,
            )
            return CoreMemory(
                id=memory_id,
                user_id=user_id,
                memory_text=memory_text,
                memory_type=memory_type,
                source=source,
                confidence=confidence,
                is_active=True,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        finally:
            conn.close()

    def update_core_memory(
        self,
        memory_id: int,
        memory_text: str | None = None,
        is_active: bool | None = None,
    ) -> bool:
        """Update a core memory.

        Args:
            memory_id: Memory ID to update
            memory_text: New memory text (optional)
            is_active: New active status (optional)

        Returns:
            True if updated, False if not found
        """
        from src.storage.schema import update_core_memory

        conn = self.storage.sqlite_db._get_connection()
        try:
            return update_core_memory(
                conn,
                memory_id=memory_id,
                memory_text=memory_text,
                is_active=is_active,
            )
        finally:
            conn.close()

    def delete_core_memory(self, memory_id: int) -> bool:
        """Delete a core memory.

        Args:
            memory_id: Memory ID to delete

        Returns:
            True if deleted, False if not found
        """
        from src.storage.schema import delete_core_memory

        conn = self.storage.sqlite_db._get_connection()
        try:
            return delete_core_memory(conn, memory_id)
        finally:
            conn.close()

    def deactivate_memory(self, memory_id: int) -> bool:
        """Deactivate a memory (soft delete).

        Args:
            memory_id: Memory ID to deactivate

        Returns:
            True if deactivated, False if not found
        """
        return self.update_core_memory(memory_id, is_active=False)

    def reactivate_memory(self, memory_id: int) -> bool:
        """Reactivate a previously deactivated memory.

        Args:
            memory_id: Memory ID to reactivate

        Returns:
            True if reactivated, False if not found
        """
        return self.update_core_memory(memory_id, is_active=True)

    # Conversation History Operations

    def get_conversation_history(
        self,
        user_id: int,
        limit: int = 50,
    ) -> list[ConversationMessage]:
        """Get recent conversation history for a user.

        Args:
            user_id: User ID
            limit: Maximum number of messages to return

        Returns:
            List of ConversationMessage objects ordered chronologically
        """
        from src.storage.schema import get_conversation_history

        conn = self.storage.sqlite_db._get_connection()
        try:
            message_dicts = get_conversation_history(conn, user_id, limit=limit)
            return [
                self._dict_to_conversation_message(message_dict)
                for message_dict in message_dicts
            ]
        finally:
            conn.close()

    def save_conversation_message(
        self,
        user_id: int,
        role: Literal["user", "assistant"],
        content: str,
        tool_calls: list[dict] | None = None,
    ) -> ConversationMessage:
        """Save a conversation message.

        Args:
            user_id: User ID
            role: "user" or "assistant"
            content: Message content
            tool_calls: Optional list of tool calls made

        Returns:
            The saved ConversationMessage with its ID
        """
        from src.storage.schema import save_conversation_message

        conn = self.storage.sqlite_db._get_connection()
        try:
            message_id = save_conversation_message(
                conn,
                user_id=user_id,
                role=role,
                content=content,
                tool_calls=tool_calls,
            )
            return ConversationMessage(
                id=message_id,
                user_id=user_id,
                role=role,
                content=content,
                tool_calls=tool_calls,
                created_at=datetime.now(),
            )
        finally:
            conn.close()

    def clear_conversation_history(self, user_id: int) -> int:
        """Clear conversation history for a user (the "reset" functionality).

        Note: This clears the conversation but preserves core memories.

        Args:
            user_id: User ID

        Returns:
            Number of messages deleted
        """
        from src.storage.schema import clear_conversation_history

        conn = self.storage.sqlite_db._get_connection()
        try:
            return clear_conversation_history(conn, user_id)
        finally:
            conn.close()

    # Preference Profile Operations

    def get_preference_profile(self, user_id: int) -> PreferenceProfile | None:
        """Get the preference profile for a user.

        Args:
            user_id: User ID

        Returns:
            PreferenceProfile or None if not found
        """
        from src.storage.schema import get_preference_profile

        conn = self.storage.sqlite_db._get_connection()
        try:
            profile_dict = get_preference_profile(conn, user_id)
            if profile_dict:
                return self._dict_to_preference_profile(profile_dict)
            return None
        finally:
            conn.close()

    def save_preference_profile(self, profile: PreferenceProfile) -> int:
        """Save or update a preference profile.

        Args:
            profile: PreferenceProfile to save

        Returns:
            Profile ID
        """
        import json

        from src.storage.schema import save_preference_profile

        conn = self.storage.sqlite_db._get_connection()
        try:
            profile_data = {
                "genre_affinities": profile.genre_affinities,
                "theme_preferences": profile.theme_preferences,
                "anti_preferences": profile.anti_preferences,
                "cross_media_patterns": profile.cross_media_patterns,
            }
            return save_preference_profile(
                conn,
                user_id=profile.user_id,
                profile_json=json.dumps(profile_data),
            )
        finally:
            conn.close()

    # Helper Methods

    def _dict_to_core_memory(self, memory_dict: dict) -> CoreMemory:
        """Convert a dictionary to a CoreMemory object.

        Args:
            memory_dict: Dictionary from database

        Returns:
            CoreMemory object
        """
        created_at = memory_dict.get("created_at")
        updated_at = memory_dict.get("updated_at")

        # Parse datetime strings if needed
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        return CoreMemory(
            id=memory_dict["id"],
            user_id=memory_dict["user_id"],
            memory_text=memory_dict["memory_text"],
            memory_type=memory_dict["memory_type"],
            source=memory_dict["source"],
            confidence=memory_dict.get("confidence", 1.0),
            is_active=memory_dict.get("is_active", True),
            created_at=created_at,
            updated_at=updated_at,
        )

    def _dict_to_conversation_message(self, message_dict: dict) -> ConversationMessage:
        """Convert a dictionary to a ConversationMessage object.

        Args:
            message_dict: Dictionary from database

        Returns:
            ConversationMessage object
        """
        created_at = message_dict.get("created_at")

        # Parse datetime string if needed
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        return ConversationMessage(
            id=message_dict["id"],
            user_id=message_dict["user_id"],
            role=message_dict["role"],
            content=message_dict["content"],
            tool_calls=message_dict.get("tool_calls"),
            created_at=created_at,
        )

    def _dict_to_preference_profile(self, profile_dict: dict) -> PreferenceProfile:
        """Convert a dictionary to a PreferenceProfile object.

        Args:
            profile_dict: Dictionary from database

        Returns:
            PreferenceProfile object
        """
        profile_data = profile_dict.get("profile", {})
        generated_at = profile_dict.get("generated_at")

        # Parse datetime string if needed
        if isinstance(generated_at, str):
            generated_at = datetime.fromisoformat(generated_at)

        return PreferenceProfile(
            user_id=profile_dict["user_id"],
            genre_affinities=profile_data.get("genre_affinities", {}),
            theme_preferences=profile_data.get("theme_preferences", []),
            anti_preferences=profile_data.get("anti_preferences", []),
            cross_media_patterns=profile_data.get("cross_media_patterns", []),
            generated_at=generated_at,
        )
