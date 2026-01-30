# Security Considerations

This document outlines security considerations for Personal Recommendations.

## Data Privacy

### Local-First Design

- All data is stored locally (SQLite database)
- LLM processing uses local Ollama (no cloud AI)
- No telemetry or analytics
- No data leaves your machine unless you configure external APIs

### Sensitive Data

The following contain sensitive information:

| File | Contains |
|------|----------|
| `config/config.yaml` | API keys, Steam ID |
| `data/recommendations.db` | Personal consumption history |
| `data/chroma/` | Vector embeddings of your content |

**Never commit these files to version control.**

## API Key Security

### Steam API Key

- Obtain from: https://steamcommunity.com/dev/apikey
- Store only in `config/config.yaml` (git-ignored)
- Key allows read access to your Steam library
- Rotate if compromised

### Best Practices

```yaml
# config/config.yaml - NEVER COMMIT THIS FILE
steam:
  api_key: "YOUR_ACTUAL_KEY_HERE"  # Keep secret!
  steam_id: "YOUR_STEAM_ID"
```

## Network Security

### External Connections

The application may connect to:

| Service | Purpose | When |
|---------|---------|------|
| Ollama (localhost) | LLM and embeddings | Always when AI enabled |
| Steam API | Game library sync | When Steam source enabled |
| Sonarr/Radarr | Media library sync | When configured |

### Localhost Binding

By default, the web interface binds to `localhost`:

```bash
# Safe: Only accessible from local machine
python -m src.web --host 127.0.0.1

# Caution: Accessible from network
python -m src.web --host 0.0.0.0
```

### Docker Network Isolation

When using Docker, services communicate over an internal network:

```yaml
networks:
  recommendations-net:
    # Isolated from host network by default
```

## Input Validation

### CSV/JSON Import

- Files are parsed with standard libraries
- No code execution from imported data
- Invalid data is skipped, not executed

### Custom Rules

- Rules are parsed by pattern matching or LLM
- No code execution from rule text
- Sanitized before storage

## Database Security

### SQLite

- No authentication (local file)
- Protect file permissions:

```bash
chmod 600 data/recommendations.db
```

### Backup

```bash
# Backup your data
cp data/recommendations.db data/recommendations.db.backup

# Encrypt backups if storing off-machine
gpg -c data/recommendations.db.backup
```

## Dependencies

### Known Considerations

- **ChromaDB**: Vector database, stores embeddings locally
- **FastAPI**: Web framework, no known critical vulnerabilities
- **Ollama**: Local LLM, network access to localhost only

### Keeping Updated

```bash
# Check for outdated packages
pip list --outdated

# Update dependencies
pip install -U -r requirements.txt
```

## Deployment Checklist

- [ ] `config/config.yaml` is git-ignored
- [ ] API keys are not in code or logs
- [ ] Database file has restricted permissions
- [ ] Web interface bound to localhost (or behind reverse proxy)
- [ ] Docker containers run as non-root user
- [ ] Ollama only accessible internally

## Reporting Security Issues

If you discover a security vulnerability:

1. **Do not** open a public GitHub issue
2. Contact the maintainer privately
3. Allow reasonable time for a fix before disclosure
