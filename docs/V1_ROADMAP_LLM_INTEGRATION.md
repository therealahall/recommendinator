# Implementing AI workflow phases

Phase 1: Data Foundation Layer


1.1 Schema Extension

	•	Extend your existing content database to include:
	◦	Embedding vectors for each piece of content (books, games, shows, movies)
	◦	User preference snapshots (point-in-time captures of your taste profile)
	◦	Conversation history table for context continuity
	◦	“Core memories” table for significant preference signals (the “better than Pacific, not as good as Band of Brothers” type statements)


1.2 Embedding Generation Pipeline

	•	Create a batch process to generate embeddings for all existing content
	•	Use a local embedding model via Ollama (nomic-embed-text or mxbai-embed-large)
	•	Store embeddings in a vector-capable database (SQLite with sqlite-vec, PostgreSQL with pgvector, or a dedicated solution like ChromaDB)
	•	Build incremental embedding updates when new content is added


1.3 Content Metadata Enrichment

	•	Ensure each content item has rich metadata: genres, themes, tone, narrative style, setting, time period
	•	This feeds both your algorithmic weights AND gives the LLM context to reason about connections





Phase 2: Context Assembly Engine


2.1 Dynamic Context Builder

	•	Build a service that assembles relevant context for any LLM query
	•	Inputs: user query, current preferences, recent activity
	•	Outputs: a structured context block containing:
	◦	Top N most relevant completed items (via embedding similarity to query)
	◦	Recent completions with ratings and reviews
	◦	Active “core memories” (preference statements)
	◦	Current algorithmic recommendations from your weight system


2.2 Preference Profile Generator

	•	Create a summarization routine that distills your full history into a digestible profile
	•	Categories to track:
	◦	Genre affinities (with scores)
	◦	Theme preferences (exploration, narrative depth, action, etc.)
	◦	Anti-preferences (what you dislike and why)
	◦	Cross-media patterns (“loves sci-fi books but prefers fantasy games”)
	•	This profile gets regenerated periodically or after significant rating events


2.3 Retrieval Pipeline (RAG)

	•	Implement semantic search across your content library
	•	When you ask “what should I play next?”, retrieve:
	◦	Your top-rated items in relevant genres
	◦	Items similar to recent high-rated completions
	◦	Unplayed/unread items that match your profile
	•	Feed these as context to the LLM rather than expecting it to “know” your library





Phase 3: Conversation & Memory System


3.1 Conversation Manager

	•	Build a stateful conversation handler that:
	◦	Maintains current session context
	◦	Tracks what’s been recommended in this conversation (avoid repeats)
	◦	Handles multi-turn dialogue (“tell me more about that one”)
	◦	Preserves conversation history for future reference


3.2 Memory Extraction Pipeline

	•	After each conversation, run a secondary LLM pass to extract:
	◦	New preference signals (“I don’t like slow burns”)
	◦	Rating updates or completions mentioned
	◦	Feedback on recommendations (accepted, rejected, why)
	•	Store extracted memories with timestamps and source conversation


3.3 Core Memory System

	•	Implement a tiered memory structure:
	◦	Working memory: Current conversation context
	◦	Short-term memory: Recent sessions (last 5-10 conversations)
	◦	Long-term memory: Distilled preference profile + significant moments
	◦	Episodic memory: Specific impactful experiences (“the time I cried at the end of RDR2”)





Phase 4: LLM Integration Layer


4.1 Prompt Template System

	•	Create modular prompt templates for different query types:
	◦	Recommendation request (with media type filter)
	◦	Feedback processing (rating + review ingestion)
	◦	Comparison queries (“something like X but more Y”)
	◦	Exploration queries (“surprise me”)
	•	Each template has slots for: user profile, relevant context, recent history, available options


4.2 Ollama Service Wrapper

	•	Build an abstraction layer over Ollama that handles:
	◦	Model selection (maybe Mistral for quick acknowledgments, Qwen for deep recommendations)
	◦	Streaming responses for better UX
	◦	Retry logic and error handling
	◦	Token counting to stay within context limits


4.3 Response Post-Processor

	•	Parse LLM responses to extract structured data:
	◦	Recommended item IDs (match back to your database)
	◦	Confidence signals
	◦	Reasoning chains (why this recommendation)
	•	Update your algorithmic weights based on LLM reasoning (feedback loop)





Phase 5: Feedback Loop & Learning


5.1 Implicit Feedback Capture

	•	Track which recommendations get:
	◦	Clicked/expanded (“tell me more”)
	◦	Accepted (“I’ll try that”)
	◦	Rejected (“not in the mood for that”)
	◦	Completed (they actually finished it)
	•	Weight future recommendations based on this signal


5.2 Explicit Feedback Integration

	•	When you complete something and rate it:
	◦	Update the content’s embeddings with your personal sentiment overlay
	◦	Regenerate your preference profile
	◦	Store the rating + review as searchable context
	◦	If you provided comparison feedback (“better than X”), create a preference edge in your graph


5.3 Algorithm ↔ LLM Synchronization

	•	Your mathematical weights and the LLM should inform each other:
	◦	LLM recommendations that succeed → boost those algorithmic paths
	◦	Algorithmic top picks feed into LLM context
	◦	Disagreements get logged for analysis (“algorithm said X, LLM said Y, user chose Y”)





Phase 6: Interface Layer


6.1 Chat Interface

	•	Build a simple chat UI (CLI is fine to start, web later)
	•	Support natural language input
	•	Stream responses for better perceived performance
	•	Show “thinking” indicators during context assembly


6.2 Quick Actions

	•	Shortcut commands for common operations:
	◦	﻿/finished [title] [rating]﻿ - Quick completion logging
	◦	﻿/recommend [type]﻿ - Get a recommendation for specific media type
	◦	﻿/mood [description]﻿ - Mood-based recommendations
	◦	﻿/history﻿ - Show recent completions and ratings


6.3 Recommendation Cards

	•	When displaying a recommendation, show:
	◦	The item details
	◦	Why it’s being recommended (connection to your history)
	◦	Confidence level
	◦	Quick accept/reject buttons that feed back into the system