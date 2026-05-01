# Docker Deployment

Recommendinator ships official Docker images for both `linux/amd64` and `linux/arm64`,
so it runs on x86 servers, Apple Silicon, modern NAS hardware (Synology DSM 7+, QNAP),
and Raspberry Pi 4/5.

The images live in [GitHub Container Registry](https://github.com/therealahall/recommendinator/pkgs/container/recommendinator).
Two variants are published:

| Variant | Image | When to use |
|---------|-------|-------------|
| Default | `ghcr.io/therealahall/recommendinator:latest` | Recommendation engine without AI features. Smaller image; no Ollama / ChromaDB. |
| AI | `ghcr.io/therealahall/recommendinator:latest-ai` | Adds Ollama client and ChromaDB. Use when you want semantic search and LLM-powered explanations. |

The AI variant pairs with a published Ollama sidecar:

| Image | Purpose |
|-------|---------|
| `ghcr.io/therealahall/recommendinator-ollama:latest` | Ollama LLM server pre-configured to pull the models defined in your `config.yaml` on first start. |

## Quick start (CLI)

For a fast, no-AI try-out — single container, host-mounted data:

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

Then open <http://localhost:18473>. The container generates a starter `config/config.yaml`
on first run from the bundled `example.yaml` — edit it on the host with your API keys
and `docker restart recommendinator`.

## Docker Compose (recommended)

Compose is the only sensible way to run the AI variant (it needs the Ollama sidecar
and a private network). It's also the cleanest path for the default variant if you're
running multiple services on the same host.

Download the canonical compose file from the latest release:

```bash
mkdir -p recommendinator/{config,data,inputs}
cd recommendinator
curl -L https://github.com/therealahall/recommendinator/releases/latest/download/docker-compose.yml \
  -o docker-compose.yml
docker compose up -d
```

For the AI variant:

```bash
docker compose --profile ai up -d app-ai
```

Naming `app-ai` explicitly is required: the default `app` service has no profile
and would otherwise start alongside `app-ai`, with both fighting for the same
host port. Specifying the service name limits the up command to `app-ai` and its
declared dependencies (the Ollama sidecar).

To switch to a pinned version instead of `latest`, set `IMAGE_TAG` in your shell or in a
`.env` file next to the compose file:

```bash
IMAGE_TAG=0.7.0 docker compose up -d
```

### Adapt the compose file to your setup

Most users edit a few things — port, volume paths, restart policy. The file is a normal
Docker Compose document; no helper tooling required. The fields you'll typically touch:

```yaml
services:
  app:
    image: ghcr.io/therealahall/recommendinator:${IMAGE_TAG:-latest}
    ports:
      - "${APP_PORT:-18473}:8000"   # change 18473 if it collides
    volumes:
      - ./config:/app/config        # rw — entrypoint writes config.yaml on first run
      - ./data:/app/data            # persistent SQLite + ChromaDB
      - ./inputs:/app/inputs:ro     # mount your Goodreads exports etc. here
      - ./private:/app/private:ro   # optional; private plugin directory
    restart: unless-stopped
```

If you don't have private plugins, leave the `private/` directory empty or remove that
volume entry — the application is happy without it.

## Parameters

### Volume mounts

| Container path | Mode | Purpose |
|----------------|------|---------|
| `/app/config` | `rw` | Configuration. Container creates `config.yaml` from `example.yaml` on first run if missing; never overwrites an existing file. **Edit `./config/config.yaml` on the host.** |
| `/app/data` | `rw` | SQLite database, ChromaDB vectors (AI variant), credential keys, cache. Backed by your host filesystem so it survives container restarts and updates. |
| `/app/inputs` | `ro` | Source files for ingestion plugins (e.g., `goodreads_library_export.csv`). Read-only because the app shouldn't be modifying your exports. |
| `/app/private` | `ro` | Optional. Private/personal plugin code (gitignored from the repo). Leave the host directory empty if you don't have any. |

### Ports

| Container port | Default host port | Purpose |
|----------------|-------------------|---------|
| `8000` | `18473` | Web UI and HTTP API. Change the host side via `APP_PORT` env var or by editing the `ports:` mapping. |

The Ollama sidecar (AI variant only) runs on `11434` inside the network but is not
exposed to the host by default — only `app-ai` talks to it.

### Environment variables

| Variable | Default | Effect |
|----------|---------|--------|
| `IMAGE_TAG` | `latest` | Which image tag the compose file pulls. Set to a semver like `0.7.0` for pinned deployments. The `-ai` suffix is appended automatically for the AI service. |
| `APP_PORT` | `18473` | Host-side port for the web UI. |
| `COMPOSE_PROFILES` | (unset) | Optional fallback for `--profile ai`. If you set this instead of using the flag, you still need to name `app-ai` on the up command (`docker compose up -d app-ai`) to skip the default `app` service. |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Set inside the AI service automatically. Override only if you're pointing at a remote Ollama instance. |

## First run

The first `docker compose up` does three things:

1. Pulls the image from GHCR (or builds locally if you've layered the dev override).
2. Starts the container, which runs `docker/entrypoint.sh` before the application.
3. The entrypoint sees `/app/config/config.yaml` is missing and copies the bundled
   `example.yaml` into the mounted volume. It logs:

   ```
   [entrypoint] No config.yaml found; copied example.yaml as a starting point.
   [entrypoint] Edit ./config/config.yaml on the host with your settings, then restart.
   ```

4. The application starts and serves the UI, but most ingestion sources will be
   inert until you fill in API keys.

After editing `./config/config.yaml`:

```bash
docker compose restart
```

The entrypoint never overwrites an existing `config.yaml`, so restarts are safe.

## AI mode

```bash
docker compose --profile ai up -d app-ai
```

This starts two containers: `recommendinator-ai` (the app with AI extras) and
`recommendinator-ollama` (the LLM server, pulled in via `depends_on`).
Naming `app-ai` explicitly is required so the default `app` service does not
also start and grab the host port. The Ollama sidecar pulls the models named
in your `config.yaml` (`ollama.model` and `ollama.embedding_model`) on first start —
this can take 5–15 minutes for a 4 GB model on a typical home connection.

You can monitor model downloads with:

```bash
docker compose logs -f ollama
```

The `app-ai` service's `depends_on: ollama: condition: service_healthy` ensures the
recommendation server doesn't start until the models are ready.

### GPU support (NVIDIA)

For GPU-accelerated inference, uncomment the `deploy.resources.reservations.devices`
block in the `ollama` service of `docker-compose.yml`:

```yaml
ollama:
  # ...
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
```

This requires the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
on the host.

### Model storage

Downloaded models are persisted in a named Docker volume (`recommendinator-ollama-data`)
so they survive container restarts and updates. To inspect or relocate:

```bash
docker volume inspect recommendinator-ollama-data
```

## Updates

```bash
docker compose pull        # fetch the latest images
docker compose up -d       # recreate containers with the new image
```

To pin to a specific version, set `IMAGE_TAG=X.Y.Z` (in your shell or `.env`) and run
the same commands.

## Reverse proxy

Recommendinator exposes plain HTTP and assumes you'll terminate TLS in front of it.
For a typical NAS deployment with [Caddy](https://caddyserver.com/), an entry like:

```caddyfile
recommendinator.example.com {
  reverse_proxy localhost:18473
}
```

…is sufficient. nginx and Traefik configurations are conventional `proxy_pass` to
the same host:port.

The application is a single-user tool with **no authentication**, so any reverse
proxy in front of it must enforce its own access controls (basic auth, OAuth
forward-auth, IP allowlists, or a VPN). The application does not currently
trust `X-Forwarded-*` headers for URL generation — links it emits use the host
and port the request reached it on, which is correct for typical deployments
where the proxy preserves `Host`.

## Architectures

Both images are published as multi-arch manifests covering `linux/amd64` and `linux/arm64`.
`docker pull` automatically selects the right architecture for your host. To force a
specific architecture (e.g., to test arm64 on an x86 dev box with QEMU):

```bash
docker pull --platform linux/arm64 ghcr.io/therealahall/recommendinator:latest
```

`linux/arm/v7` (32-bit ARM, older Pi 2/3) is intentionally **not** supported — the
Python 3.11 wheel ecosystem for ChromaDB is too thin there.

## Troubleshooting

### Config changes don't take effect

The entrypoint only writes `config.yaml` on first run. After that, edit the file on
the host and run `docker compose restart` (or `docker restart recommendinator`).
Hot-reload of config is not supported.

### Port 18473 collides with another service

Set `APP_PORT` to a different host port:

```bash
APP_PORT=8080 docker compose up -d
```

The container always listens on `8000` internally — only the host-side mapping changes.

### Ollama models never download

Check the sidecar logs:

```bash
docker compose logs -f ollama
```

If you see `pull manifest unauthorized` or `connection reset`, the model name in your
`config.yaml` is likely wrong (typo, missing tag suffix). Run `ollama pull <model>`
manually inside the sidecar to confirm:

```bash
docker compose exec ollama ollama pull mistral:7b
```

### `permission denied` writing to `/app/data`

The container runs as a non-root user (UID derived from the image's `appuser`).
On hosts with restrictive umasks or SELinux, you may need to chown the host data
directory:

```bash
chown -R 1000:1000 ./data ./config
```

Adjust `1000:1000` to match your appuser's UID/GID inside the image — `docker exec`
into a running container and run `id appuser` to confirm.

### My private plugins aren't loading

Confirm the host `./private/` directory exists and contains your plugin files. The
volume mount is `:ro` so the container can read your plugins but not modify them.
If the directory doesn't exist on the host, Docker may auto-create it as `root`,
which the appuser can't read — `chown` it as above.

## Local development

If you're contributing to Recommendinator and want hot reload instead of rebuilding
the image on every change, layer the dev override on top of the production compose:

```bash
# default variant
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# AI variant — name app-ai so the default app service is skipped
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile ai up -d app-ai
```

This builds the image locally instead of pulling, bind-mounts `./src` and `./templates`
into the container, and runs uvicorn with `--reload` so Python changes trigger a
~1s restart. For frontend hot reload, run `pnpm dev` on the host (Vite serves on
port 5173 and proxies API calls to the container). See [CONTRIBUTING.md](../CONTRIBUTING.md)
for the full developer workflow.
