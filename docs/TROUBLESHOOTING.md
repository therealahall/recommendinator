# Troubleshooting Guide

Common issues and solutions for Recommendinator.

## Installation Issues

### ChromaDB Installation Fails

**Error:** `Failed to build hnswlib` or similar

**Solution:** ChromaDB requires Python 3.11 or 3.12. If you're using 3.13+, either:
1. Use Python 3.11: `uv sync --locked --extra ai`
2. Run without AI features (ChromaDB is optional)

### Missing Dependencies

**Error:** `ModuleNotFoundError: No module named 'xxx'`

**Solution:**
```bash
uv sync --locked --extra ai
# or for development:
uv sync --locked --extra ai --extra dev
```

## Ollama Issues

### Ollama Not Running

**Error:** `Connection refused` or `Failed to connect to Ollama`

**Solution:**
1. Start Ollama: `ollama serve`
2. Check it's running: `curl http://localhost:11434/api/tags`
3. Verify the URL in your config matches

### Model Not Found

**Error:** `Model 'xxx' not found`

**Solution:**
```bash
# Pull the required models
ollama pull llama3.2
ollama pull nomic-embed-text

# List available models
ollama list
```

### Slow Embeddings

**Symptom:** Ingestion takes a very long time

**Solutions:**
1. Use a faster embedding model (e.g., `nomic-embed-text`)
2. Reduce batch size in config
3. Run without AI features for initial import, add embeddings later

## Database Issues

### Database Locked

**Error:** `database is locked`

**Solution:**
1. Ensure only one instance is running
2. Close any SQLite browsers (DB Browser, etc.)
3. Check for zombie processes: `ps aux | grep python`

### Schema Migration Failed

**Error:** `Schema version mismatch`

**Solution:**
1. Backup your data: `cp data/recommendations.db data/recommendations.db.backup`
2. Delete and recreate: `rm data/recommendations.db`
3. Re-import your data

### Data Not Persisting

**Symptom:** Items disappear after restart

**Solution:**
1. Check the database path in config
2. Ensure the `data/` directory exists and is writable
3. Verify you're using the same config file

## Recommendation Issues

### No Recommendations Generated

**Symptom:** "No recommendations available"

**Causes & Solutions:**
1. **No consumed items:** Add some content and mark as completed
2. **No unconsumed items:** Add items to your wishlist/library
3. **Wrong content type:** Ensure you have items of the requested type
4. **Series filtering:** If all items are excluded by series rules, check series order settings

### Poor Quality Recommendations

**Symptom:** Recommendations don't match preferences

**Solutions:**
1. Rate more items (need variety for good preferences)
2. Adjust scorer weights in preferences
3. Add custom rules for specific preferences
4. Check if AI features are enabled for better similarity

### Custom Rules Not Working

**Symptom:** Added rules but no change in recommendations

**Solutions:**
1. Verify rule was saved: `python3.11 -m src.cli preferences custom-rules list`
2. Test interpretation: `python3.11 -m src.cli preferences custom-rules interpret "your rule"`
3. Click "Save Preferences" in web UI
4. Rules influence but don't completely override scoring

## Import Issues

### Goodreads Import Fails

**Error:** `File not found` or `Invalid CSV format`

**Solution:**
1. Export from Goodreads: My Books → Import/Export → Export Library
2. Place file in `inputs/` directory
3. Update path in config
4. Ensure CSV has required columns (Title, Author, etc.)

### Steam Import Fails

**Error:** `Steam API error` or `Invalid API key`

**Solutions:**
1. Get API key: https://steamcommunity.com/dev/apikey
2. Find Steam ID: Profile → Edit Profile → Custom URL or use numeric ID
3. Set profile to public (required for API access)
4. Check rate limits (wait a few minutes if hitting limits)

### Duplicate Items

**Symptom:** Same items appear multiple times

**Solution:**
1. Check conflict strategy in config
2. Use `source_priority` to prefer one source over another
3. Items are matched by external ID + content type

## Web Interface Issues

### Page Won't Load

**Error:** Blank page or "Failed to connect"

**Solutions:**
1. Check server is running: `python3.11 -m src.web`
2. Verify port (default: 18473)
3. Check browser console for errors
4. Try different browser or incognito mode

### Changes Not Saving

**Symptom:** Preferences reset after refresh

**Solutions:**
1. Click "Save Preferences" button
2. Check browser network tab for errors
3. Verify API is responding: `curl http://localhost:18473/api/status`

## CLI Issues

### Command Not Found

**Error:** `No such command 'xxx'`

**Solution:**
```bash
# Run through module
python3.11 -m src.cli --help

# Or check if it's a subcommand
python3.11 -m src.cli preferences --help
```

### Config File Not Found

**Error:** `Configuration file not found`

**Solution:**
1. Copy example config: `cp config/example.yaml config/config.yaml`
2. Specify path: `python3.11 -m src.cli --config path/to/config.yaml`

## Docker Issues

### Container Won't Start

**Error:** `Container exited with code 1`

**Solutions:**
1. Check logs: `docker compose logs app` (or `docker compose logs app-ai` if using `--profile ai`)
2. Verify config file is mounted
3. Ensure data directory permissions are correct

### Ollama Sidecar Issues

**Error:** `Connection refused to ollama:11434`

**Solutions:**
1. Wait for Ollama to be ready (check health status)
2. Verify network configuration in docker compose config
3. Check Ollama logs: `docker compose logs ollama`

### AI Features Disabled Despite Being Enabled in Config

**Symptom:** `features.ai_enabled: true` is set but AI features don't work

**Solutions:**
1. Check logs for warnings about missing packages (`chromadb is not installed` or `ollama is not installed`)
2. Install AI packages: `pip install recommendinator[ai]`
3. For Docker: use `docker compose --profile ai up app-ai` instead of `docker compose up`

The application gracefully degrades when AI packages are missing — it logs a warning and continues with AI features disabled rather than crashing.

### GPU Not Working

**Symptom:** Ollama using CPU instead of GPU

**Solution:** Uncomment the `deploy` section under the `ollama` service in docker-compose.yml:
```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

## Performance Issues

### Slow Startup

**Symptom:** App takes long to start

**Solutions:**
1. Disable AI features if not needed
2. Use smaller embedding model
3. Pre-download Ollama models before first run

### High Memory Usage

**Symptom:** App uses excessive RAM

**Solutions:**
1. Limit vector DB cache size
2. Use pagination for large libraries
3. Consider running Ollama on separate machine

## Getting Help

If you can't resolve an issue:

1. Check existing GitHub issues
2. Include in your report:
   - Python version: `python3.11 --version`
   - OS and version
   - Error message (full traceback)
   - Steps to reproduce
   - Config file (remove secrets!)
3. Enable debug logging for more details
