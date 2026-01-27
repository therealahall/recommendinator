# Personal Recommendations

A personal recommendation system that analyzes your ratings, reviews, and consumption history across multiple media types (books, movies, TV shows, video games) to provide intelligent recommendations using a locally-running LLM via Ollama.

## Overview

This project consumes data files from various sources (Goodreads, game reviews, etc.) and uses a local LLM to understand your preferences and generate personalized recommendations. The system can:

- Ingest data from multiple sources (CSV, JSON, TXT, Markdown)
- Filter between consumed and unconsumed content
- Learn from your ratings and reviews
- Generate recommendations based on your preferences
- Update dynamically as you consume new content or update data files

## Features

- **Multi-source data ingestion**: Supports CSV, JSON, TXT, and Markdown files
- **Content type support**: Books, Movies, TV Shows, Video Games
- **Cross-content-type recommendations**: Preferences from all content types influence recommendations (e.g., sci-fi books can lead to sci-fi game/TV recommendations)
- **AI optional**: Full recommendation pipeline works without LLM/embeddings; AI enhances but is not required
- **Local LLM integration**: Uses Ollama for privacy-preserving recommendations (when AI enabled)
- **Dual interface**: CLI and web interface (internal network only)
- **Incremental updates**: Can process new data without full reprocessing
- **Rating analysis**: Understands your 1-5 star rating preferences

## Requirements

- Python 3.11+ (tested with 3.14.2)
- Ollama installed and running locally
- AMD architecture compatible (for Ollama models)

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd personal-recommendations
```

2. Install dependencies:
```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt  # For development
```

3. Ensure Ollama is running:
```bash
ollama serve
```

4. Pull a recommended model (if not already installed):
```bash
ollama pull mistral:7b
```

## Usage

### CLI Interface

```bash
# Get recommendations
python -m src.cli recommend --type books --count 5

# Update data from files
python -m src.cli update --source goodreads

# Mark content as completed
python -m src.cli complete --type book --title "Book Title" --rating 4
```

### Web Interface

Start the web server:
```bash
python -m src.web
```

Access the interface at `http://localhost:8000` (or your configured host/port).

## Project Structure

```
personal-recommendations/
├── src/
│   ├── cli/              # Command-line interface
│   ├── web/              # Web interface (Flask/FastAPI)
│   ├── ingestion/        # Data ingestion modules
│   ├── llm/              # LLM interaction and prompts
│   ├── storage/          # Data storage (vector DB, SQLite, etc.)
│   ├── models/           # Data models and schemas
│   └── utils/            # Utility functions
├── tests/                # Test suite
├── inputs/               # Input data files
├── config/               # Configuration files
└── docs/                 # Additional documentation
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines, including:
- Code style (Black, MyPy)
- Testing requirements
- Commit message conventions
- Architecture decisions

See [DEVELOPMENT.md](DEVELOPMENT.md) for a detailed development log tracking progress, decisions, and implementation steps.

### AI Development Tools

This project includes configuration for AI coding assistants:
- **Claude Code**: `CLAUDE.md` (primary)
- **Cursor**: `.cursorrules`

## Configuration

Configuration files are located in `config/`. See `config/example.yaml` for available options.

## License

[Your License Here]
