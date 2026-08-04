# Docker Deployment

Official images cover `linux/amd64` and `linux/arm64`, so x86 servers, Apple
Silicon, Synology DSM 7+, QNAP and Raspberry Pi 4/5 all work. `linux/arm/v7` is
unsupported, since ChromaDB has no wheels there. They live in
[GHCR](https://github.com/therealahall/recommendinator/pkgs/container/recommendinator).

| Image | Contents |
|-------|----------|
| `ghcr.io/therealahall/recommendinator:latest` | Default. Smaller, no Ollama or ChromaDB. |
| `ghcr.io/therealahall/recommendinator:latest-ai` | Adds the Ollama client and ChromaDB, for semantic search and LLM explanations. |
| `ghcr.io/therealahall/recommendinator-ollama:latest` | The Ollama sidecar the AI variant needs. Pulls its models on first start. |

## Quick start, no AI

```bash
mkdir -p recommendinator/{config,data,inputs}
cd recommendinator

docker run -d \
  --name recommendinator \
  -p 18473:8000 \
  -v "$(pwd)/config:/app/config" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/inputs:/app/inputs:ro" \
  --restart unless-stopped \
  ghcr.io/therealahall/recommendinator:latest
```

Open <http://localhost:18473>. The container copies the bundled `example.yaml` to
`config/config.yaml` on first run. Under Docker that file only matters for the
`storage` paths and `web.debug`, because the image passes `--host` and `--port`
on its command line and CLI flags beat `config.yaml`. **Publish a different port
with the `-p` mapping, not `web.port`.** Sources, settings and API keys live in
the database and are managed from inside the app.

## Docker Compose

Compose is the only sensible way to run the AI variant, which needs the sidecar
and a private network, and the cleaner path for the default variant on a busy
host.

```bash
mkdir -p recommendinator/{config,data,inputs}
cd recommendinator
curl -L https://github.com/therealahall/recommendinator/releases/latest/download/docker-compose.yml \
  -o docker-compose.yml
docker compose up -d
```

Pin a version with `IMAGE_TAG`, in your shell or a `.env` file beside the compose
file:

```bash
IMAGE_TAG=0.7.0 docker compose up -d
```

It is an ordinary Compose document. Most people only touch the port, the volume
paths and the restart policy:

```yaml
services:
  app:
    image: ghcr.io/therealahall/recommendinator:${IMAGE_TAG:-latest}
    ports:
      - "${APP_BIND_PREFIX:-}${APP_PORT:-18473}:8000"
    volumes:
      - ./config:/app/config
      - ./data:/app/data
      - ./inputs:/app/inputs:ro
      - ./private:/app/private:ro   # optional
    restart: unless-stopped
```

With no private plugins, leave `./private` empty or drop that volume.

## Parameters

### Volume mounts

| Path | Mode | Holds |
|------|------|-------|
| `/app/config` | `rw` | `config.yaml`, created from `example.yaml` on first run and never overwritten. Edit it on the host. |
| `/app/data` | `rw` | SQLite database, ChromaDB vectors, credential key, cache. **This is the volume to back up.** |
| `/app/inputs` | `ro` | Source files for ingestion, such as `goodreads_library_export.csv`. |
| `/app/private` | `ro` | Optional private plugin code. |

### Ports

The app listens on `8000` inside the container, published on `18473`. Change the
host side with `APP_PORT` or the `ports:` mapping. The Ollama sidecar listens on
`11434` inside the network and is not published to the host at all.

### Environment variables

| Variable | Default | Effect |
|----------|---------|--------|
| `TZ` | unset, so UTC | Timezone both app containers run in. Any IANA name resolves, the image carries `tzdata`. Completions are dated by the calendar day in this zone, so west of UTC an evening watch is dated a day forward until you set it. |
| `IMAGE_TAG` | `latest` | Tag the compose file pulls. The `-ai` suffix is appended for the AI service. |
| `APP_PORT` | `18473` | Host port for the web UI. |
| `APP_BIND_PREFIX` | unset, so every interface | Host interface to publish on, written **with a trailing colon**: `APP_BIND_PREFIX=127.0.0.1:`. |
| `COMPOSE_PROFILES` | unset | Alternative to `--profile ai`. You still have to name `app-ai` on the up command. |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Set for you inside the AI service. Override only for a remote Ollama. |
| `OLLAMA_MODEL` | `mistral:7b` | Generation model the sidecar pulls. Must match the app's `ollama.model`. |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Embedding model the sidecar pulls. Must match `ollama.embedding_model`. |
| `OLLAMA_CONVERSATION_MODEL` | unset, so reuses `OLLAMA_MODEL` | Set only when `ollama.conversation_model` names a separate chat model. |

**Setting `TZ` does not correct dates already stored, and a re-sync will not
either.** The corrected local date is the earlier one, and a sync keeps the later
of two dates. Only new completions get the right day.

## First run

The entrypoint copies `example.yaml` to `config.yaml` when none exists, says so
in the log, and starts the app. It never overwrites an existing file, so restarts
are safe.

The UI comes up with no sources, so ingestion does nothing until you add them
from the **Data** tab or `source create`, with API keys from the **Settings**
page or `settings set-secret`. Both write to the database and need no restart.
After editing `config.yaml` itself:

```bash
docker compose restart
```

## AI mode

```bash
docker compose --profile ai up -d app-ai
```

**Name `app-ai` explicitly.** The default `app` service has no profile, so
leaving the name off starts it too and both fight over the same host port.

That brings up `recommendinator-ai` and, through `depends_on`, the
`recommendinator-ollama` sidecar. The sidecar pulls its models on first start,
which runs 5 to 15 minutes for a 4 GB model on a home connection, and `app-ai`
waits on its health check. Watch the download:

```bash
docker compose logs -f ollama
```

The sidecar reads only `OLLAMA_MODEL`, `OLLAMA_EMBEDDING_MODEL` and
`OLLAMA_CONVERSATION_MODEL`. It never sees `config.yaml` or the database, so keep
those in step with the `ollama.*` settings. **Point the app at a model the
sidecar never pulled and every Ollama request fails.** To switch models:

```bash
# in .env beside docker-compose.yml
OLLAMA_MODEL=llama3.1:8b
```

```bash
docker compose --profile ai up -d ollama   # recreate the sidecar and pull
```

Then set the matching value on the Settings page.

### GPU support (NVIDIA)

Uncomment the `deploy.resources.reservations.devices` block in the `ollama`
service of `docker-compose.yml`:

```yaml
ollama:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
```

This needs the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
on the host.

### Model storage

Models persist in the `recommendinator-ollama-data` named volume, so they survive
restarts and updates.

```bash
docker volume inspect recommendinator-ollama-data
```

## Updates

```bash
docker compose pull
docker compose up -d
```

To pin, set `IMAGE_TAG=X.Y.Z` and run the same two commands.

## Reverse proxy

The app speaks plain HTTP and expects TLS to terminate in front of it. With
[Caddy](https://caddyserver.com/):

```caddyfile
recommendinator.example.com {
  reverse_proxy localhost:18473
}
```

nginx and Traefik are a conventional `proxy_pass` to the same host and port.

With the proxy on the same host, set `APP_BIND_PREFIX=127.0.0.1:` (trailing colon
included) so the published port binds to loopback. The proxy still reaches it,
nothing else on the network does.

**The app has no authentication anywhere**, so the proxy has to enforce its own:
basic auth, forward auth, an IP allowlist or a VPN. It does not trust
`X-Forwarded-*` headers, so the links it emits use the host and port the request
arrived on. That is correct wherever the proxy preserves `Host`.

## Architectures

`docker pull` picks yours. Force another to test it:

```bash
docker pull --platform linux/arm64 ghcr.io/therealahall/recommendinator:latest
```

## Troubleshooting

### A settings change did nothing

Settings badged "restart required" need `docker compose restart`. Everything else
on the **Settings** page applies immediately. `config.yaml` is only written on
first run, so edit it on the host and restart.

### Port 18473 is taken

```bash
APP_PORT=8080 docker compose up -d
```

The container still listens on `8000` internally.

### Models never download

```bash
docker compose logs -f ollama
```

`pull manifest unauthorized` or `connection reset` usually means a typo or a
missing tag in one of the model variables. The sidecar logs the names it
resolved. Confirm one by hand:

```bash
docker compose exec ollama ollama pull mistral:7b
```

### `permission denied` writing to `/app/data`

The container runs as non-root. On hosts with a restrictive umask or SELinux,
chown the host directories:

```bash
chown -R 1000:1000 ./data ./config
```

If 1000 is wrong, `docker exec <container> id appuser` gives you the real UID.

### Private plugins do not load

The host `./private/` directory has to exist and hold your plugin files. If it
did not exist, Docker created it owned by root and the container user cannot read
it. Chown it as above.

## Local development

```bash
# default variant
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# AI variant, naming app-ai so the default service is skipped
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile ai up -d app-ai
```

The dev override builds locally instead of pulling, bind-mounts `./src`,
`./templates` and `./pyproject.toml`, and runs uvicorn with `--reload`. Mounting
`pyproject.toml` keeps the runtime `__version__` in step with semantic-release
bumps without a rebuild. For frontend hot reload, run `pnpm dev` on the host:
Vite serves on 5173 and proxies API calls to the container. See
[CONTRIBUTING.md](../CONTRIBUTING.md).
