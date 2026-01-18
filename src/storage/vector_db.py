"""ChromaDB vector database manager for embeddings."""

from pathlib import Path
from typing import List, Optional, Dict, Any
import chromadb
from chromadb.config import Settings
import numpy as np


class VectorDB:
    """ChromaDB vector database manager for content embeddings."""

    def __init__(self, db_path: Path, collection_name: str = "content_embeddings") -> None:
        """Initialize ChromaDB vector database manager.

        Args:
            db_path: Path to ChromaDB database directory
            collection_name: Name of the collection to use
        """
        self.db_path = db_path
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name

        # Initialize ChromaDB client
        self.client = chromadb.PersistentClient(
            path=str(db_path), settings=Settings(anonymized_telemetry=False)
        )

        # Get or create collection
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"description": "Content item embeddings for semantic search"},
        )

    def add_embedding(
        self,
        content_id: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add or update an embedding for a content item.

        Args:
            content_id: Unique identifier for the content item
            embedding: Vector embedding
            metadata: Optional metadata dictionary
        """
        # Prepare metadata
        doc_metadata = metadata or {}
        doc_metadata["content_id"] = content_id

        # Check if embedding exists and update or add accordingly
        try:
            existing = self.collection.get(ids=[content_id])
            if existing["ids"] and len(existing["ids"]) > 0:
                # Update existing embedding
                self.collection.update(
                    ids=[content_id],
                    embeddings=[embedding],
                    metadatas=[doc_metadata],
                )
            else:
                # Add new embedding
                self.collection.add(
                    ids=[content_id],
                    embeddings=[embedding],
                    metadatas=[doc_metadata],
                )
        except Exception:
            # If update fails, try adding (might not exist)
            try:
                self.collection.add(
                    ids=[content_id],
                    embeddings=[embedding],
                    metadatas=[doc_metadata],
                )
            except Exception:
                # If add fails (duplicate), delete and re-add
                try:
                    self.collection.delete(ids=[content_id])
                except Exception:
                    pass
                self.collection.add(
                    ids=[content_id],
                    embeddings=[embedding],
                    metadatas=[doc_metadata],
                )

    def get_embedding(self, content_id: str) -> Optional[List[float]]:
        """Get embedding for a content item.

        Args:
            content_id: Unique identifier for the content item

        Returns:
            Embedding vector if found, None otherwise
        """
        try:
            results = self.collection.get(ids=[content_id], include=["embeddings"])
            if results["ids"] and len(results["ids"]) > 0 and results["embeddings"] is not None:
                embedding = results["embeddings"][0]
                # Convert numpy array to list if needed
                if isinstance(embedding, np.ndarray):
                    return embedding.tolist()
                return list(embedding) if embedding else None
            return None
        except Exception:
            return None

    def search_similar(
        self,
        query_embedding: List[float],
        n_results: int = 10,
        content_type: Optional[str] = None,
        exclude_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Search for similar content using vector similarity.

        Args:
            query_embedding: Query embedding vector
            n_results: Number of results to return
            content_type: Optional filter by content type
            exclude_ids: Optional list of content IDs to exclude

        Returns:
            List of similar content items with scores
        """
        where_clause: Dict[str, Any] = {}
        if content_type:
            where_clause["content_type"] = content_type

        if exclude_ids:
            where_clause["content_id"] = {"$nin": exclude_ids}

        try:
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
                where=where_clause if where_clause else None,
            )

            # Format results
            formatted_results = []
            if results["ids"] and len(results["ids"]) > 0:
                for i, content_id in enumerate(results["ids"][0]):
                    formatted_results.append(
                        {
                            "content_id": content_id,
                            "score": 1.0 - results["distances"][0][i]
                            if results.get("distances")
                            else None,
                            "metadata": results["metadatas"][0][i]
                            if results.get("metadatas")
                            else {},
                        }
                    )

            return formatted_results
        except Exception:
            return []

    def delete_embedding(self, content_id: str) -> bool:
        """Delete an embedding for a content item.

        Args:
            content_id: Unique identifier for the content item

        Returns:
            True if deleted, False if not found
        """
        try:
            self.collection.delete(ids=[content_id])
            return True
        except Exception:
            return False

    def has_embedding(self, content_id: str) -> bool:
        """Check if an embedding exists for a content item.

        Args:
            content_id: Unique identifier for the content item

        Returns:
            True if embedding exists, False otherwise
        """
        try:
            results = self.collection.get(ids=[content_id])
            return len(results["ids"]) > 0
        except Exception:
            return False

    def count_embeddings(self) -> int:
        """Get the total number of embeddings in the collection.

        Returns:
            Number of embeddings
        """
        try:
            return self.collection.count()
        except Exception:
            return 0
