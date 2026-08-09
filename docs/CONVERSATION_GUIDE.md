# Conversation and Chat Guide

Chat lets you ask for recommendations, mark items completed, rate content and
build a preference profile in natural language. **It requires AI.** The app
works fully without it.

## Prerequisites

1. Ollama installed and running, see
   [OLLAMA_SETUP_GUIDE.md](OLLAMA_SETUP_GUIDE.md).
2. AI enabled, from the **Settings** page or
   `settings set features.ai_enabled true`.
3. Optionally a separate chat model. Left unset it falls back to `ollama.model`:

```bash
python3.11 -m src.cli settings set ollama.model mistral:7b
python3.11 -m src.cli settings set ollama.conversation_model qwen2.5:3b
```

## Configuration

From the **Settings** page (Conversation section) or the `settings` CLI. Every
value is stored in the database, not a config file:

| Key | Default | Meaning |
|-----|---------|---------|
| `conversation.enabled` | true | Master toggle for chat |
| `conversation.max_history_messages` | 50 | Messages kept in context |
| `conversation.memory_extraction_enabled` | true | Auto-extract preferences |
| `conversation.profile_regeneration_interval` | 24 | Hours, 0 to disable |
| `conversation.llm.temperature` | 0.7 | Response creativity, 0.0 to 2.0 |
| `conversation.llm.max_tokens` | 2000 | Maximum response length |
| `conversation.llm.context_window_size` | 0 | Ollama's `num_ctx`, 0 uses the model's default |
| `conversation.context.max_relevant_items` | 10 | Items pulled in by semantic search |
| `conversation.context.max_unconsumed_items` | 20 | Backlog items in context |
| `conversation.context.include_algorithmic_recs` | true | Add ranked recommendations |
| `conversation.context.compact_mode` | false | Enable for 3B models |

```bash
python3.11 -m src.cli settings set conversation.llm.temperature 0.7
python3.11 -m src.cli settings list --section conversation   # current values
```

None of them need a restart: each chat turn reads the values in force when it
starts, so a save lands on the next message.

### Compact mode (small models)

On a 3B model such as `qwen2.5:3b`, compact mode cuts prompt size by 60-70%:

```bash
python3.11 -m src.cli settings set ollama.conversation_model qwen2.5:3b
python3.11 -m src.cli settings set conversation.llm.context_window_size 4096
python3.11 -m src.cli settings set conversation.context.compact_mode true
```

It swaps in a condensed system prompt with examples instead of detailed rules,
drops context to 5 completed and 5 backlog items, detects simple intents before
the LLM runs (so "I finished Book X" skips it entirely), and picks a single
recommendation rather than a list.

The Ollama sidecar pulls only the models named in your compose environment and
cannot see these settings. If `ollama.conversation_model` differs from
`ollama.model`, set `OLLAMA_CONVERSATION_MODEL` to match and recreate the
sidecar. Left unset it reuses `OLLAMA_MODEL`, mirroring the app's own fallback.
See [DOCKER.md](DOCKER.md).

## Using the chat

The web UI's **Chat** tab, or the `chat` CLI group below. What you can say:

| Intent | Example |
|--------|---------|
| Ask for a recommendation | "What should I read next?", "I'm in the mood for a short sci-fi book" |
| Mark something completed | "I just read Project Hail Mary, 5 out of 5" |
| Date a completion | "I finished Dune on 12 March" |
| Rate or re-rate | "Rate Dune 4 out of 5", then "Actually, make that a 3" |
| State a preference | "I don't enjoy first-person shooters" |
| Search your library | "Do I have any Dragon Age games?" |

A rating you state **replaces** the one on record, the same way the edit modal
and `library edit` do.

Chat is the only surface that can put a completion date on an item. The date you
name is written exactly as given, **including one earlier than the date already
stored**, which is how you fix a completion an import dated wrongly. It has to
resolve to a real calendar day that has happened — a date past tomorrow is
refused and reported rather than stored as a guess. Everywhere else the date is
decided for you, see [ARCHITECTURE.md](../ARCHITECTURE.md#user-owned-fields).

The system assembles context from your library, memories and preferences, sends
it to the local LLM with your message, lets the LLM call tools (mark completed,
save memory, search) and streams the reply back. A library change it makes shows
up in your recommendations immediately.

## Memories

Memories are preference signals that carry across conversations. **User-stated**
ones, things you tell the chat, carry full confidence. **Inferred** ones, pulled
from your history, score lower.

View, add, edit and delete them from the **Memories** panel in the web chat, or
the `memory` CLI below. They shape the recommendations chat gives you, so
deleting a wrong one ("dislikes psychological thrillers") improves them.

With `conversation.memory_extraction_enabled` on, a secondary LLM pass after a
conversation saves memories by itself. Say "I've been really into strategy games
lately" and it may record that.

## User profiles

The profile summarises your completed and rated items into the LLM's context. It
captures:

- **Genre affinities**, genres you rate highly, needing 2 or more rated items each
- **Theme preferences**, keywords from items you rated 4 or better
- **Anti-preferences**, genres where your average rating is low
- **Cross-media patterns**, "loves sci-fi in books but prefers fantasy in games"

It regenerates on `conversation.profile_regeneration_interval`, every 24 hours by
default. Rebuild it now from the web chat or with `profile regenerate`. It is
derived from your data, so it can mischaracterise a genre you have barely rated.
More ratings make it more accurate.

## CLI commands

```bash
python3.11 -m src.cli chat start                   # interactive REPL
python3.11 -m src.cli chat start --type book       # filter to a content type
python3.11 -m src.cli chat send --message "Recommend a sci-fi book"
python3.11 -m src.cli chat history --limit 10
python3.11 -m src.cli chat reset                   # clear history
```

```bash
python3.11 -m src.cli memory list
python3.11 -m src.cli memory add --text "I love hard sci-fi"
python3.11 -m src.cli memory edit --id 3 --text "..." --inactive  # text and state together
python3.11 -m src.cli memory toggle --id 3                        # flip active/inactive
python3.11 -m src.cli memory delete --id 3
```

`memory edit` matches `PUT /api/memories/{id}`, and takes `--text`, `--inactive`
or both.

## Troubleshooting

### Chat returns "LLM not configured"

Check all of these: `features.ai_enabled` true, `conversation.enabled` true,
Ollama reachable at `ollama.base_url`, and at least one model available
(`ollama.model` or `ollama.conversation_model`).

### Chat is slow

Models want GPU memory, so a CPU-bound host will crawl. Point
`ollama.conversation_model` at something smaller, turn on `compact_mode`, set
`context_window_size`, and lower `max_relevant_items` and
`max_unconsumed_items`.

### Chat recommendations differ from the web ones

Expected. Chat adds your conversation history and memories on top of the purely
algorithmic recommendations.

### Profile seems inaccurate

A genre needs 2 rated items to register at all, and your ratings in it may
genuinely sit below your average. Regenerate after rating more.
