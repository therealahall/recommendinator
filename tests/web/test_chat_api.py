"""Tests for chat and memory API endpoints."""

import json
import tempfile
from collections.abc import Generator, Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.models.content import ConsumptionStatus, ContentItem, ContentType
from src.models.conversation import ConversationChunk
from src.storage.manager import StorageManager
from src.web.state import app_state


@pytest.fixture
def storage_manager() -> Generator[StorageManager, None, None]:
    """Create a storage manager with a temporary database."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        yield StorageManager(sqlite_path=db_path)


@pytest.fixture
def mock_conversation_engine() -> MagicMock:
    """Create a mock conversation engine."""
    engine = MagicMock()

    def mock_process_message(*args, **kwargs) -> Iterator[ConversationChunk]:
        yield ConversationChunk(chunk_type="text", content="Hello! ")
        yield ConversationChunk(chunk_type="text", content="How can I help?")
        yield ConversationChunk(chunk_type="done")

    engine.process_message.side_effect = mock_process_message
    engine.reset_conversation.return_value = 5  # 5 messages deleted
    return engine


@pytest.fixture
def test_client(
    storage_manager: StorageManager,
    mock_conversation_engine: MagicMock,
) -> Generator[TestClient, None, None]:
    """Create a test client with mocked dependencies."""
    # Store original state
    original_state = app_state.copy()

    # Set up test state
    app_state["storage"] = storage_manager
    app_state["conversation_engine"] = mock_conversation_engine
    app_state["config"] = {"web": {"allowed_origins": ["*"]}}

    # Import and create app after setting state
    from fastapi import FastAPI

    from src.web.chat_api import router

    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        yield client

    # Restore original state
    app_state.clear()
    app_state.update(original_state)


class TestChatEndpoint:
    """Tests for /api/chat endpoint."""

    def test_chat_streams_response(
        self,
        test_client: TestClient,
        mock_conversation_engine: MagicMock,
    ) -> None:
        """Test that chat endpoint streams SSE response."""
        response = test_client.post(
            "/api/chat",
            json={"user_id": 1, "message": "Hello!"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        # Parse SSE events
        events = []
        for line in response.text.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

        # Should have text chunks and done
        text_events = [e for e in events if e["type"] == "text"]
        done_events = [e for e in events if e["type"] == "done"]

        assert len(text_events) >= 1
        assert len(done_events) == 1

    def test_chat_with_content_type(
        self,
        test_client: TestClient,
        mock_conversation_engine: MagicMock,
    ) -> None:
        """Test chat with content type filter."""
        response = test_client.post(
            "/api/chat",
            json={
                "user_id": 1,
                "message": "Recommend a book",
                "content_type": "book",
            },
        )

        assert response.status_code == 200

        # Verify content type was passed to engine
        call_kwargs = mock_conversation_engine.process_message.call_args.kwargs
        assert call_kwargs["content_type"] == ContentType.BOOK

    def test_chat_invalid_content_type(
        self,
        test_client: TestClient,
    ) -> None:
        """Test chat with invalid content type."""
        response = test_client.post(
            "/api/chat",
            json={
                "user_id": 1,
                "message": "Test",
                "content_type": "invalid_type",
            },
        )

        assert response.status_code == 400
        assert "Invalid content type" in response.json()["detail"]

    def test_chat_without_engine(
        self,
        storage_manager: StorageManager,
    ) -> None:
        """Test chat when conversation engine is not available."""
        # Store original state
        original_state = app_state.copy()

        app_state["storage"] = storage_manager
        app_state["conversation_engine"] = None

        from fastapi import FastAPI

        from src.web.chat_api import router

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={"user_id": 1, "message": "Hello"},
            )

            assert response.status_code == 503
            assert "not available" in response.json()["detail"]

        # Restore
        app_state.clear()
        app_state.update(original_state)


class TestChatResetEndpoint:
    """Tests for /api/chat/reset endpoint."""

    def test_reset_chat_success(
        self,
        test_client: TestClient,
        mock_conversation_engine: MagicMock,
    ) -> None:
        """Test resetting chat history."""
        response = test_client.post("/api/chat/reset?user_id=1")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["deleted_count"] == 5


class TestChatHistoryEndpoint:
    """Tests for /api/chat/history endpoint."""

    def test_get_chat_history(
        self,
        test_client: TestClient,
        storage_manager: StorageManager,
    ) -> None:
        """Test getting chat history."""
        # Add some messages
        from src.conversation.memory import MemoryManager

        memory = MemoryManager(storage_manager)
        memory.save_conversation_message(user_id=1, role="user", content="Hello")
        memory.save_conversation_message(
            user_id=1, role="assistant", content="Hi there!"
        )

        response = test_client.get("/api/chat/history?user_id=1")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["role"] == "user"  # Oldest first (chronological)
        assert data[1]["role"] == "assistant"

    def test_get_chat_history_with_limit(
        self,
        test_client: TestClient,
        storage_manager: StorageManager,
    ) -> None:
        """Test chat history with limit parameter."""
        from src.conversation.memory import MemoryManager

        memory = MemoryManager(storage_manager)
        for i in range(10):
            memory.save_conversation_message(
                user_id=1, role="user", content=f"Message {i}"
            )

        response = test_client.get("/api/chat/history?user_id=1&limit=5")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 5


class TestMemoriesEndpoints:
    """Tests for /api/memories endpoints."""

    def test_get_memories_empty(
        self,
        test_client: TestClient,
    ) -> None:
        """Test getting memories when none exist."""
        response = test_client.get("/api/memories?user_id=1")

        assert response.status_code == 200
        assert response.json() == []

    def test_create_memory(
        self,
        test_client: TestClient,
    ) -> None:
        """Test creating a memory."""
        response = test_client.post(
            "/api/memories",
            json={
                "user_id": 1,
                "memory_text": "I prefer sci-fi books",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["memory_text"] == "I prefer sci-fi books"
        assert data["memory_type"] == "user_stated"
        assert data["confidence"] == 1.0
        assert data["is_active"] is True

    def test_get_memories_after_create(
        self,
        test_client: TestClient,
    ) -> None:
        """Test getting memories after creating some."""
        # Create a memory
        test_client.post(
            "/api/memories",
            json={"user_id": 1, "memory_text": "I love RPGs"},
        )

        response = test_client.get("/api/memories?user_id=1")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["memory_text"] == "I love RPGs"

    def test_update_memory_text(
        self,
        test_client: TestClient,
        storage_manager: StorageManager,
    ) -> None:
        """Test updating memory text."""
        # Create a memory
        create_response = test_client.post(
            "/api/memories",
            json={"user_id": 1, "memory_text": "Original text"},
        )
        memory_id = create_response.json()["id"]

        # Update it
        response = test_client.put(
            f"/api/memories/{memory_id}",
            json={"memory_text": "Updated text"},
        )

        assert response.status_code == 200
        assert response.json()["success"] is True

        # Verify the update
        memories_response = test_client.get("/api/memories?user_id=1")
        assert memories_response.json()[0]["memory_text"] == "Updated text"

    def test_update_memory_active_status(
        self,
        test_client: TestClient,
    ) -> None:
        """Test toggling memory active status."""
        # Create a memory
        create_response = test_client.post(
            "/api/memories",
            json={"user_id": 1, "memory_text": "Test memory"},
        )
        memory_id = create_response.json()["id"]

        # Deactivate it
        response = test_client.put(
            f"/api/memories/{memory_id}",
            json={"is_active": False},
        )

        assert response.status_code == 200

        # Should not appear in default query
        memories_response = test_client.get("/api/memories?user_id=1")
        assert len(memories_response.json()) == 0

        # Should appear when including inactive
        memories_response = test_client.get(
            "/api/memories?user_id=1&include_inactive=true"
        )
        assert len(memories_response.json()) == 1

    def test_update_memory_not_found(
        self,
        test_client: TestClient,
    ) -> None:
        """Test updating non-existent memory."""
        response = test_client.put(
            "/api/memories/99999",
            json={"memory_text": "New text"},
        )

        assert response.status_code == 404

    def test_delete_memory(
        self,
        test_client: TestClient,
    ) -> None:
        """Test deleting a memory."""
        # Create a memory
        create_response = test_client.post(
            "/api/memories",
            json={"user_id": 1, "memory_text": "To be deleted"},
        )
        memory_id = create_response.json()["id"]

        # Delete it
        response = test_client.delete(f"/api/memories/{memory_id}")

        assert response.status_code == 200
        assert response.json()["success"] is True

        # Verify it's gone
        memories_response = test_client.get(
            "/api/memories?user_id=1&include_inactive=true"
        )
        assert len(memories_response.json()) == 0

    def test_delete_memory_not_found(
        self,
        test_client: TestClient,
    ) -> None:
        """Test deleting non-existent memory."""
        response = test_client.delete("/api/memories/99999")

        assert response.status_code == 404


class TestProfileEndpoints:
    """Tests for /api/profile endpoints."""

    def test_get_profile_empty(
        self,
        test_client: TestClient,
    ) -> None:
        """Test getting profile when none exists."""
        response = test_client.get("/api/profile?user_id=1")

        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == 1
        assert data["genre_affinities"] == {}
        assert data["theme_preferences"] == []
        assert data["anti_preferences"] == []
        assert data["cross_media_patterns"] == []

    def test_regenerate_profile(
        self,
        test_client: TestClient,
        storage_manager: StorageManager,
    ) -> None:
        """Test regenerating preference profile."""
        # Add some completed items
        items = [
            ContentItem(
                id="test1",
                title="Dune",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["sci-fi"]},
            ),
            ContentItem(
                id="test2",
                title="Foundation",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["sci-fi"]},
            ),
        ]
        for item in items:
            storage_manager.save_content_item(item, user_id=1)

        # Regenerate profile
        response = test_client.post("/api/profile/regenerate?user_id=1")

        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == 1
        assert "science fiction" in data["genre_affinities"]
        assert data["genre_affinities"]["science fiction"] >= 4.0

    def test_get_profile_after_regenerate(
        self,
        test_client: TestClient,
        storage_manager: StorageManager,
    ) -> None:
        """Test that profile persists after regeneration."""
        # Add items and regenerate (need 2+ per genre for affinity)
        storage_manager.save_content_item(
            ContentItem(
                id="test1",
                title="Fantasy Book 1",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["fantasy"]},
            ),
            user_id=1,
        )
        storage_manager.save_content_item(
            ContentItem(
                id="test2",
                title="Fantasy Book 2",
                content_type=ContentType.BOOK,
                status=ConsumptionStatus.COMPLETED,
                rating=5,
                metadata={"genres": ["fantasy"]},
            ),
            user_id=1,
        )
        test_client.post("/api/profile/regenerate?user_id=1")

        # Get profile
        response = test_client.get("/api/profile?user_id=1")

        assert response.status_code == 200
        data = response.json()
        assert "fantasy" in data["genre_affinities"]
