# Docker Deployment

The official image, `ghcr.io/therealahall/recommendinator:latest`, covers
`linux/amd64` and `linux/arm64`, so x86 servers, Apple Silicon, Synology DSM 7+,
QNAP and Raspberry Pi 4/5 all work. `linux/arm/v7` is unsupported, since the
Python 3.11 wheel ecosystem is too thin there. It lives in
[GHCR](https://github.com/therealahall/recommendinator/pkgs/container/recommendinator).

## Quick start

```bash
mkdir -p recommendinator/{config,data,inputs}
cd recommendinator

docker run -d \
  --name recommendinator \
  -p 127.0.0.1:18473:8000 \
  -v "$(pwd)/config:/app/config" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/inputs:/app/inputs:ro" \
  --restart unless-stopped \
  ghcr.io/therealahall/recommendinator:latest
```

The container copies the bundled `example.yaml` to `config/config.yaml` on first
run and starts serving. Open <http://localhost:18473> and create your account —
see [First run](#first-run).

Under Docker that file matters for the `storage` paths and `web.debug`, because
the image passes `--host` and `--port` on its command line and CLI flags beat
`config.yaml`. **Publish a different port with the `-p` mapping, not
`web.port`.** Sources, settings and API keys live in the database and are
managed from inside the app.

## Docker Compose

Compose is the cleaner path on a busy host: the volumes, the port and the
hardening options live in a file rather than in your shell history.

```bash
mkdir -p recommendinator/{config,data,inputs}
cd recommendinator
curl -L https://github.com/therealahall/recommendinator/releases/latest/download/docker-compose.yml \
  -o docker-compose.yml
docker compose up -d
```

That first `up` writes `./config/config.yaml` and starts serving.

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
      - "${APP_BIND_PREFIX-127.0.0.1:}${APP_PORT:-18473}:8000"
    volumes:
      - ./config:/app/config
      - ./data:/app/data
      - ./inputs:/app/inputs:ro
      - ./private:/app/private:ro   # optional
    restart: unless-stopped
```

With no private plugins, leave `./private` empty or drop that volume.

The service also carries `cap_drop: [ALL]` and
`security_opt: [no-new-privileges:true]`. Every `/api` route already requires a
session cookie ([SECURITY.md](SECURITY.md#web-sign-in)); this is the other
half, limiting what a compromised dependency reaches once it is inside. Nothing
here needs a capability: the image runs as an unprivileged user and binds a port
above 1024.

## Parameters

### Volume mounts

| Path | Mode | Holds |
|------|------|-------|
| `/app/config` | `rw` | `config.yaml`, created from `example.yaml` on first run and never overwritten. Edit it on the host. |
| `/app/data` | `rw` | SQLite database, credential key, cache. **This is the volume to back up.** |
| `/app/inputs` | `ro` | Directories a source scans. Imports are uploaded, not mounted. |
| `/app/private` | `ro` | Optional private plugin code and themes. |

### Ports

The app listens on `8000` inside the container, published on `127.0.0.1:18473` —
**this host only**. Change the port with `APP_PORT` and the interface with
`APP_BIND_PREFIX`, though anything wider than loopback puts your password and
session cookie on the network in cleartext: prefer a
[reverse proxy](#reverse-proxy).

### Environment variables

| Variable | Default | Effect |
|----------|---------|--------|
| `TZ` | unset, so UTC | Timezone the container runs in. Any IANA name resolves, the image carries `tzdata`. Completions are dated by the calendar day in this zone, so west of UTC an evening watch is dated a day forward until you set it. |
| `IMAGE_TAG` | `latest` | Tag the compose file pulls. |
| `APP_PORT` | `18473` | Host port for the web UI. |
| `APP_BIND_PREFIX` | `127.0.0.1:`, so this host only | Host interface to publish on, written **with a trailing colon**: `APP_BIND_PREFIX=192.168.1.5:`. Set it empty for every interface. See [Ports](#ports). |

The service carries no memory ceiling on purpose: a limit guessed for a sync
turns a long but healthy one into an OOM kill.

**Setting `TZ` does not correct dates already stored, and a re-sync will not
either.** The corrected local date is the earlier one, and a sync keeps the later
of two dates. Only new completions get the right day.

## First run

The entrypoint copies the image's `/app/example.yaml` to `config.yaml` when none
exists and never overwrites an existing file, so restarts are safe. That seed
sits outside `/app/config` on purpose: your `./config` mount covers that
directory, so a copy kept inside it would be hidden on the run that reads it.

Then open the published port. A new instance has no account and opens on a setup
screen asking for a username, a display name and a password of at least 12
characters; finishing it claims the instance and signs that browser in. **Until
someone does, whoever reaches the container first can** — publishing on
`127.0.0.1` is what bounds that. If you lose the password later, there is no
reset link, so set a new one from the host:

```bash
docker compose exec app python -m src.cli account set-password
```

The UI comes up with no sources, so ingestion does nothing until you add them
from the **Data** tab or `source create`, with API keys from the **Settings**
page or `settings set-secret`. Both write to the database and need no restart.
After editing `config.yaml` itself:

```bash
docker compose restart
```

## Updates

```bash
docker compose pull
docker compose up -d
```

To pin, set `IMAGE_TAG=X.Y.Z` and run the same two commands. A pin outranks the
`pull`, which then re-fetches the release you named rather than a newer one, so
moving forward means raising the pin.

**Coming from 0.32.0 or earlier?** Sign-in changed, one `config.yaml` key is
dead, and the upgraded instance is claimable until someone claims it. See
[Upgrading](../README.md#upgrading).

## Reverse proxy

The app speaks plain HTTP and expects TLS to terminate in front of it, which
beyond loopback is not optional: your password is on the wire. With
[Caddy](https://caddyserver.com/):

```caddyfile
recommendinator.example.com {
  reverse_proxy localhost:18473
}
```

nginx and Traefik are a conventional `proxy_pass` to the same host and port.

With the proxy on the same host, leave `APP_BIND_PREFIX` at its default so the
published port stays on loopback. The proxy still reaches it, nothing else on
the network does.

The session cookie authenticates the API, and the proxy can add its own layer
on top (forward auth, an IP allowlist, a VPN). The app does not trust
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

### I have forgotten the password

There is no reset link. Set a new one from the host and sign in again:

```bash
docker compose exec app python -m src.cli account set-password
```

### The app is unreachable from another machine

That is the default: the port is published on `127.0.0.1` only. See
[Ports](#ports).

### Port 18473 is taken

```bash
APP_PORT=8080 docker compose up -d
```

The container still listens on `8000` internally.

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

It also has to be a package: `private/__init__.py` and
`private/plugins/__init__.py` both have to be there, or the scan stops before it
reads a plugin and logs that only at debug level.

## Local development

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
docker compose up -d
```

Compose merges `docker-compose.override.yml` in on its own, and passing `-f`
suppresses it — including any mounts you added to it — so leave the flag off.

The override builds locally instead of pulling, bind-mounts `./src`,
`./templates` and `./pyproject.toml`, and runs uvicorn with `--reload`. Mounting
`pyproject.toml` keeps the runtime `__version__` in step with semantic-release
bumps without a rebuild. For frontend hot reload, run `pnpm dev` on the host:
Vite serves on 5173 and proxies API calls to the container. See
[CONTRIBUTING.md](../CONTRIBUTING.md).

The container serves the frontend bundle it built itself, so port 18473 works on
a fresh clone that has never run `pnpm`. That bundle sits in a volume, which is
what stops the `./src` mount hiding it — and a volume outlives the container, so
rebuild with `--build --renew-anon-volumes` after a frontend change or the old
bundle comes back.
