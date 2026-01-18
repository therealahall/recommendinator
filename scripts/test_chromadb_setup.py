#!/usr/bin/env python
"""Test script to verify ChromaDB setup."""

import sys
from pathlib import Path

try:
    import chromadb

    print(f"✅ ChromaDB imported successfully")
    print(f"   Version: {chromadb.__version__}")
except ImportError as e:
    print(f"❌ Failed to import ChromaDB: {e}")
    print("\nTo install ChromaDB, run:")
    print("  pip install chromadb")
    sys.exit(1)

try:
    from chromadb.config import Settings

    print("✅ ChromaDB Settings imported successfully")
except ImportError as e:
    print(f"❌ Failed to import ChromaDB Settings: {e}")
    sys.exit(1)

# Test creating a client
try:
    test_path = Path("/tmp/test_chromadb")
    test_path.mkdir(exist_ok=True)

    client = chromadb.PersistentClient(
        path=str(test_path), settings=Settings(anonymized_telemetry=False)
    )
    print("✅ ChromaDB PersistentClient created successfully")

    # Test creating a collection
    collection = client.get_or_create_collection(
        name="test_collection", metadata={"description": "Test collection"}
    )
    print("✅ ChromaDB collection created successfully")

    # Test adding an embedding
    test_embedding = [0.1, 0.2, 0.3, 0.4, 0.5]
    collection.add(
        ids=["test_1"], embeddings=[test_embedding], metadatas=[{"test": "data"}]
    )
    print("✅ Successfully added test embedding")

    # Test querying
    results = collection.query(query_embeddings=[test_embedding], n_results=1)
    print("✅ Successfully queried embeddings")

    # Cleanup
    client.delete_collection("test_collection")
    print("✅ Cleanup successful")

    print("\n🎉 ChromaDB is working correctly!")

except Exception as e:
    print(f"❌ Error testing ChromaDB: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)
