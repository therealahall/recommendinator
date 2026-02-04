"""Tests for memory management functionality."""

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from src.conversation.memory import MemoryManager
from src.models.conversation import CoreMemory, PreferenceProfile
from src.storage.manager import StorageManager
from src.storage.schema import create_user


@pytest.fixture
def storage_manager() -> Generator[StorageManager, None, None]:
    """Create a storage manager with a temporary database."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        manager = StorageManager(sqlite_path=db_path)
        # Create user 2 for multi-user tests (user 1 is created by default)
        conn = manager.sqlite_db._get_connection()
        try:
            create_user(conn, "testuser2", "Test User 2")
        finally:
            conn.close()
        yield manager


@pytest.fixture
def memory_manager(storage_manager: StorageManager) -> MemoryManager:
    """Create a memory manager for testing."""
    return MemoryManager(storage_manager)


class TestCoreMemoryCRUD:
    """Tests for core memory CRUD operations."""

    def test_save_and_get_core_memory(self, memory_manager: MemoryManager) -> None:
        """Test saving and retrieving a core memory."""
        memory = memory_manager.save_core_memory(
            user_id=1,
            memory_text="I prefer shorter games during weekdays",
            memory_type="user_stated",
            source="conversation",
        )

        assert memory.id is not None
        assert memory.memory_text == "I prefer shorter games during weekdays"
        assert memory.memory_type == "user_stated"
        assert memory.source == "conversation"
        assert memory.confidence == 1.0
        assert memory.is_active is True

    def test_get_core_memories_returns_all_active(
        self, memory_manager: MemoryManager
    ) -> None:
        """Test getting all active memories."""
        memory_manager.save_core_memory(
            user_id=1,
            memory_text="Memory 1",
            memory_type="user_stated",
            source="conversation",
        )
        memory_manager.save_core_memory(
            user_id=1,
            memory_text="Memory 2",
            memory_type="inferred",
            source="rating_pattern",
            confidence=0.8,
        )

        memories = memory_manager.get_core_memories(user_id=1)

        assert len(memories) == 2
        assert all(isinstance(memory, CoreMemory) for memory in memories)

    def test_get_core_memories_filters_by_type(
        self, memory_manager: MemoryManager
    ) -> None:
        """Test filtering memories by type."""
        memory_manager.save_core_memory(
            user_id=1,
            memory_text="User stated memory",
            memory_type="user_stated",
            source="conversation",
        )
        memory_manager.save_core_memory(
            user_id=1,
            memory_text="Inferred memory",
            memory_type="inferred",
            source="rating_pattern",
        )

        user_stated = memory_manager.get_core_memories(
            user_id=1, memory_type="user_stated"
        )
        inferred = memory_manager.get_core_memories(user_id=1, memory_type="inferred")

        assert len(user_stated) == 1
        assert user_stated[0].memory_text == "User stated memory"
        assert len(inferred) == 1
        assert inferred[0].memory_text == "Inferred memory"

    def test_update_core_memory_text(self, memory_manager: MemoryManager) -> None:
        """Test updating memory text."""
        memory = memory_manager.save_core_memory(
            user_id=1,
            memory_text="Original text",
            memory_type="user_stated",
            source="conversation",
        )

        assert memory.id is not None
        result = memory_manager.update_core_memory(
            memory_id=memory.id, memory_text="Updated text"
        )

        assert result is True
        memories = memory_manager.get_core_memories(user_id=1)
        assert memories[0].memory_text == "Updated text"

    def test_deactivate_and_reactivate_memory(
        self, memory_manager: MemoryManager
    ) -> None:
        """Test deactivating and reactivating a memory."""
        memory = memory_manager.save_core_memory(
            user_id=1,
            memory_text="Test memory",
            memory_type="inferred",
            source="conversation",
        )
        assert memory.id is not None

        # Deactivate
        result = memory_manager.deactivate_memory(memory.id)
        assert result is True

        # Should not appear in active_only query
        active_memories = memory_manager.get_core_memories(user_id=1, active_only=True)
        assert len(active_memories) == 0

        # Should appear when including inactive
        all_memories = memory_manager.get_core_memories(user_id=1, active_only=False)
        assert len(all_memories) == 1
        assert all_memories[0].is_active is False

        # Reactivate
        result = memory_manager.reactivate_memory(memory.id)
        assert result is True

        active_memories = memory_manager.get_core_memories(user_id=1, active_only=True)
        assert len(active_memories) == 1
        assert active_memories[0].is_active is True

    def test_delete_core_memory(self, memory_manager: MemoryManager) -> None:
        """Test deleting a memory."""
        memory = memory_manager.save_core_memory(
            user_id=1,
            memory_text="To be deleted",
            memory_type="user_stated",
            source="conversation",
        )
        assert memory.id is not None

        result = memory_manager.delete_core_memory(memory.id)
        assert result is True

        memories = memory_manager.get_core_memories(user_id=1, active_only=False)
        assert len(memories) == 0

    def test_delete_nonexistent_memory_returns_false(
        self, memory_manager: MemoryManager
    ) -> None:
        """Test deleting a non-existent memory returns False."""
        result = memory_manager.delete_core_memory(99999)
        assert result is False

    def test_memories_isolated_by_user(self, memory_manager: MemoryManager) -> None:
        """Test that memories are isolated by user."""
        memory_manager.save_core_memory(
            user_id=1,
            memory_text="User 1 memory",
            memory_type="user_stated",
            source="conversation",
        )
        memory_manager.save_core_memory(
            user_id=2,
            memory_text="User 2 memory",
            memory_type="user_stated",
            source="conversation",
        )

        user1_memories = memory_manager.get_core_memories(user_id=1)
        user2_memories = memory_manager.get_core_memories(user_id=2)

        assert len(user1_memories) == 1
        assert user1_memories[0].memory_text == "User 1 memory"
        assert len(user2_memories) == 1
        assert user2_memories[0].memory_text == "User 2 memory"

    def test_inferred_memory_with_confidence(
        self, memory_manager: MemoryManager
    ) -> None:
        """Test saving an inferred memory with confidence score."""
        memory = memory_manager.save_core_memory(
            user_id=1,
            memory_text="Tends to abandon games with grinding mechanics",
            memory_type="inferred",
            source="rating_pattern",
            confidence=0.75,
        )

        assert memory.confidence == 0.75
        assert memory.memory_type == "inferred"


class TestConversationHistory:
    """Tests for conversation history functionality."""

    def test_save_and_get_conversation_message(
        self, memory_manager: MemoryManager
    ) -> None:
        """Test saving and retrieving a conversation message."""
        message = memory_manager.save_conversation_message(
            user_id=1,
            role="user",
            content="What game should I play next?",
        )

        assert message.id is not None
        assert message.role == "user"
        assert message.content == "What game should I play next?"

    def test_get_conversation_history_returns_chronologically(
        self, memory_manager: MemoryManager
    ) -> None:
        """Test that conversation history is returned in chronological order."""
        memory_manager.save_conversation_message(
            user_id=1, role="user", content="First message"
        )
        memory_manager.save_conversation_message(
            user_id=1, role="assistant", content="Second message"
        )
        memory_manager.save_conversation_message(
            user_id=1, role="user", content="Third message"
        )

        history = memory_manager.get_conversation_history(user_id=1)

        assert len(history) == 3
        assert history[0].content == "First message"
        assert history[1].content == "Second message"
        assert history[2].content == "Third message"

    def test_save_message_with_tool_calls(self, memory_manager: MemoryManager) -> None:
        """Test saving a message with tool calls."""
        tool_calls = [
            {"name": "mark_completed", "params": {"item_id": 123, "rating": 5}}
        ]
        message = memory_manager.save_conversation_message(
            user_id=1,
            role="assistant",
            content="I've marked that as completed!",
            tool_calls=tool_calls,
        )

        assert message.tool_calls == tool_calls

        history = memory_manager.get_conversation_history(user_id=1)
        assert history[0].tool_calls == tool_calls

    def test_clear_conversation_history(self, memory_manager: MemoryManager) -> None:
        """Test clearing conversation history."""
        memory_manager.save_conversation_message(
            user_id=1, role="user", content="Message 1"
        )
        memory_manager.save_conversation_message(
            user_id=1, role="assistant", content="Message 2"
        )

        deleted_count = memory_manager.clear_conversation_history(user_id=1)

        assert deleted_count == 2
        history = memory_manager.get_conversation_history(user_id=1)
        assert len(history) == 0

    def test_clear_preserves_core_memories(self, memory_manager: MemoryManager) -> None:
        """Test that clearing conversation preserves core memories."""
        memory_manager.save_core_memory(
            user_id=1,
            memory_text="Important memory",
            memory_type="user_stated",
            source="conversation",
        )
        memory_manager.save_conversation_message(
            user_id=1, role="user", content="Message"
        )

        memory_manager.clear_conversation_history(user_id=1)

        memories = memory_manager.get_core_memories(user_id=1)
        assert len(memories) == 1
        assert memories[0].memory_text == "Important memory"

    def test_conversation_history_respects_limit(
        self, memory_manager: MemoryManager
    ) -> None:
        """Test that conversation history respects the limit parameter."""
        for i in range(10):
            memory_manager.save_conversation_message(
                user_id=1, role="user", content=f"Message {i}"
            )

        history = memory_manager.get_conversation_history(user_id=1, limit=5)

        assert len(history) == 5
        # Should be the most recent 5 messages
        assert history[0].content == "Message 5"
        assert history[4].content == "Message 9"

    def test_conversation_history_isolated_by_user(
        self, memory_manager: MemoryManager
    ) -> None:
        """Test that conversation history is isolated by user."""
        memory_manager.save_conversation_message(
            user_id=1, role="user", content="User 1 message"
        )
        memory_manager.save_conversation_message(
            user_id=2, role="user", content="User 2 message"
        )

        user1_history = memory_manager.get_conversation_history(user_id=1)
        user2_history = memory_manager.get_conversation_history(user_id=2)

        assert len(user1_history) == 1
        assert user1_history[0].content == "User 1 message"
        assert len(user2_history) == 1
        assert user2_history[0].content == "User 2 message"


class TestPreferenceProfile:
    """Tests for preference profile functionality."""

    def test_save_and_get_preference_profile(
        self, memory_manager: MemoryManager
    ) -> None:
        """Test saving and retrieving a preference profile."""
        profile = PreferenceProfile(
            user_id=1,
            genre_affinities={"sci-fi": 0.9, "fantasy": 0.7},
            theme_preferences=["exploration", "narrative depth"],
            anti_preferences=["grinding", "precision platformers"],
            cross_media_patterns=["loves sci-fi books but prefers fantasy games"],
        )

        memory_manager.save_preference_profile(profile)

        retrieved = memory_manager.get_preference_profile(user_id=1)

        assert retrieved is not None
        assert retrieved.user_id == 1
        assert retrieved.genre_affinities == {"sci-fi": 0.9, "fantasy": 0.7}
        assert retrieved.theme_preferences == ["exploration", "narrative depth"]
        assert retrieved.anti_preferences == ["grinding", "precision platformers"]
        assert retrieved.cross_media_patterns == [
            "loves sci-fi books but prefers fantasy games"
        ]

    def test_save_profile_updates_existing(self, memory_manager: MemoryManager) -> None:
        """Test that saving a profile updates an existing one."""
        profile1 = PreferenceProfile(
            user_id=1,
            genre_affinities={"sci-fi": 0.5},
            theme_preferences=["action"],
        )
        memory_manager.save_preference_profile(profile1)

        profile2 = PreferenceProfile(
            user_id=1,
            genre_affinities={"fantasy": 0.8},
            theme_preferences=["exploration"],
        )
        memory_manager.save_preference_profile(profile2)

        retrieved = memory_manager.get_preference_profile(user_id=1)

        assert retrieved is not None
        assert retrieved.genre_affinities == {"fantasy": 0.8}
        assert retrieved.theme_preferences == ["exploration"]

    def test_get_nonexistent_profile_returns_none(
        self, memory_manager: MemoryManager
    ) -> None:
        """Test that getting a non-existent profile returns None."""
        profile = memory_manager.get_preference_profile(user_id=99999)
        assert profile is None

    def test_profile_isolated_by_user(self, memory_manager: MemoryManager) -> None:
        """Test that profiles are isolated by user."""
        profile1 = PreferenceProfile(
            user_id=1,
            genre_affinities={"sci-fi": 0.9},
        )
        profile2 = PreferenceProfile(
            user_id=2,
            genre_affinities={"fantasy": 0.9},
        )
        memory_manager.save_preference_profile(profile1)
        memory_manager.save_preference_profile(profile2)

        user1_profile = memory_manager.get_preference_profile(user_id=1)
        user2_profile = memory_manager.get_preference_profile(user_id=2)

        assert user1_profile is not None
        assert user1_profile.genre_affinities == {"sci-fi": 0.9}
        assert user2_profile is not None
        assert user2_profile.genre_affinities == {"fantasy": 0.9}


class TestStorageManagerMemoryMethods:
    """Tests for memory methods on StorageManager directly."""

    def test_storage_manager_core_memory_methods(
        self, storage_manager: StorageManager
    ) -> None:
        """Test core memory methods on StorageManager."""
        # Save
        memory_id = storage_manager.save_core_memory(
            user_id=1,
            memory_text="Test memory",
            memory_type="user_stated",
            source="manual",
        )
        assert memory_id is not None

        # Get
        memories = storage_manager.get_core_memories(user_id=1)
        assert len(memories) == 1
        assert memories[0]["memory_text"] == "Test memory"

        # Update
        result = storage_manager.update_core_memory(
            memory_id=memory_id, memory_text="Updated memory"
        )
        assert result is True

        # Verify update
        memories = storage_manager.get_core_memories(user_id=1)
        assert memories[0]["memory_text"] == "Updated memory"

        # Delete
        result = storage_manager.delete_core_memory(memory_id)
        assert result is True

        memories = storage_manager.get_core_memories(user_id=1)
        assert len(memories) == 0

    def test_storage_manager_conversation_methods(
        self, storage_manager: StorageManager
    ) -> None:
        """Test conversation methods on StorageManager."""
        # Save messages
        msg1_id = storage_manager.save_conversation_message(
            user_id=1, role="user", content="Hello"
        )
        msg2_id = storage_manager.save_conversation_message(
            user_id=1, role="assistant", content="Hi there!"
        )

        assert msg1_id is not None
        assert msg2_id is not None

        # Get history
        history = storage_manager.get_conversation_history(user_id=1)
        assert len(history) == 2
        assert history[0]["content"] == "Hello"
        assert history[1]["content"] == "Hi there!"

        # Clear
        deleted = storage_manager.clear_conversation_history(user_id=1)
        assert deleted == 2

        history = storage_manager.get_conversation_history(user_id=1)
        assert len(history) == 0

    def test_storage_manager_preference_profile_methods(
        self, storage_manager: StorageManager
    ) -> None:
        """Test preference profile methods on StorageManager."""
        import json

        profile_data = {
            "genre_affinities": {"action": 0.8},
            "theme_preferences": ["adventure"],
        }

        # Save
        profile_id = storage_manager.save_preference_profile(
            user_id=1, profile_json=json.dumps(profile_data)
        )
        assert profile_id is not None

        # Get
        profile = storage_manager.get_preference_profile(user_id=1)
        assert profile is not None
        assert profile["profile"]["genre_affinities"] == {"action": 0.8}
        assert profile["profile"]["theme_preferences"] == ["adventure"]
