# Quick Start Guide

Get up and running with Personal Recommendations in under 5 minutes.

## Prerequisites

- **Python 3.11+** installed
- Your data (Goodreads export, Steam account, etc.)

That's it. No AI, no external services required.

## Installation

```bash
# Clone the repository
git clone https://github.com/ahall/personal-recommendations.git
cd personal-recommendations

# Install dependencies (base only, no AI)
python3.11 -m pip install .

# Or install with AI features (ollama, chromadb)
python3.11 -m pip install ".[ai]"

# Set up configuration
cp config/example.yaml config/config.yaml
```

## Import Your Data

### Option A: Goodreads (Books)

1. Export your library from Goodreads: My Books → Import/Export → Export Library
2. Save to `inputs/goodreads_library_export.csv`
3. Enable in `config/config.yaml`:
   ```yaml
   inputs:
     goodreads:
       plugin: goodreads
       path: "inputs/goodreads_library_export.csv"
       enabled: true
   ```
4. Import: `python3.11 -m src.cli update --source goodreads`

### Option B: Steam (Games)

1. Get your Steam API key: https://steamcommunity.com/dev/apikey
2. Find your Steam ID (64-bit) from your profile URL
3. Configure in `config/config.yaml`:
   ```yaml
   inputs:
     steam:
       plugin: steam
       api_key: "your-api-key"
       steam_id: "your-steam-id"
       enabled: true
   ```
4. Import: `python3.11 -m src.cli update --source steam`

### Option C: Generic CSV/JSON/Markdown

Use the templates in `templates/` as a starting point:

```bash
# Copy a template
cp templates/movies.csv inputs/my_movies.csv

# Edit with your data, then configure and import
```

## Get Recommendations

```bash
# Books
python3.11 -m src.cli recommend --type book --count 5

# Movies
python3.11 -m src.cli recommend --type movie --count 5

# Video games
python3.11 -m src.cli recommend --type video_game --count 5

# TV shows
python3.11 -m src.cli recommend --type tv_show --count 5
```

## Use the Web Interface

```bash
python3.11 -m src.web
```

Open http://localhost:18473 in your browser.

## Customize Your Preferences

### Set scoring weights

```bash
# Emphasize genre matching
python3.11 -m src.cli preferences set-weight genre_match 3.0

# De-emphasize creator matching
python3.11 -m src.cli preferences set-weight creator_match 0.5
```

### Add custom rules

```bash
python3.11 -m src.cli preferences custom-rules add "avoid horror"
python3.11 -m src.cli preferences custom-rules add "prefer science fiction"
python3.11 -m src.cli preferences custom-rules add "only short books"
```

### Set length preferences

```bash
python3.11 -m src.cli preferences set-length book short
python3.11 -m src.cli preferences set-length movie any
python3.11 -m src.cli preferences set-length video_game long
```

## Optional: Enable AI Features

If you want semantic similarity and LLM-powered explanations:

1. Install Ollama: https://ollama.ai
2. Pull a model: `ollama pull mistral:7b`
3. Pull an embedding model: `ollama pull nomic-embed-text`
4. Enable in `config/config.yaml`:
   ```yaml
   features:
     ai_enabled: true
     embeddings_enabled: true
     llm_reasoning_enabled: true
   ```

See [docs/MODEL_RECOMMENDATIONS.md](docs/MODEL_RECOMMENDATIONS.md) for model guidance.

## Verify Your Setup

```bash
# Run the test suite
python3.11 -m pytest

# Check code quality
make check
```

## Next Steps

- [README.md](README.md) — Full feature overview
- [ARCHITECTURE.md](ARCHITECTURE.md) — How the system works
- [docs/CUSTOM_RULES.md](docs/CUSTOM_RULES.md) — Advanced preference rules
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — Common issues
