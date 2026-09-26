# Docker (self-hosting)

One container runs the whole app. uvicorn serves the API, the WebSocket and the built UI on one port, and the backend spawns the Temporal dev server, the bun code sidecar and plugin daemons itself when it needs them. There is no Redis; the cache falls back to SQLite.

| File | Role |
|---|---|
| `docker/Dockerfile` | Two stages. The build stage runs the canonical build, the same two commands CI runs (`bun install --frozen-lockfile`, then `bun run build`). The runtime stage keeps only what the backend reads at run time, and optional tools come back through the `EXTRAS` build argument. |
| `docker/entrypoint.sh` | Writes the server secrets on first start, then runs uvicorn directly, the way the desktop shell does. |
| `docker-compose.yml` | One service, one named volume, the port published on this machine only. |

## Quick start

From the repository root:

```bash
docker compose up -d --build
```

Open http://localhost:5678. The app requires a login: the first account you register becomes the owner, and registration closes after that. To create the owner at first start instead, uncomment `OPENCOMPANY_OWNER_EMAIL` and `OPENCOMPANY_OWNER_PASSWORD` in `docker-compose.yml`. The password needs at least 8 characters; a shorter one is only logged as an error, and the app then waits for you to register in the browser. The seed runs only while no account exists, so changing these values later does not change the owner's password. The port is published on `127.0.0.1` only; see [Serving other machines](#serving-other-machines) to open it up.

`docker compose ps` reports the container as `healthy` once `/health/ready` answers, meaning the database is up and the Temporal workers have started. `docker compose logs -f` follows the backend log.

## Optional tools (`EXTRAS`)

The default image has no browser, git or uv. Add them through the `EXTRAS` build argument in `docker-compose.yml` (for example `EXTRAS: "browser git"`), then run `docker compose up -d --build`.

| Extra | Installs | Needed by |
|---|---|---|
| `browser` | Chromium, fonts, Xvfb, uv | The current Browser node needs uv to install its pinned browser-use CLI; Chromium can be selected explicitly as described below |
| `git` | git | The Claude Code and Codex agent nodes, which work in git worktrees |

Without the `browser` extra, the runtime image has no uv for the Browser node's tool installer or system Chromium and its libraries. The default `BROWSER_RUNTIME=system` needs an installed Chrome/Edge/Chromium executable and never falls back to a download. Use `BROWSER_RUNTIME=testing` only when pinned Chrome for Testing is intended. Without the `git` extra, Claude Code and Codex tasks can fail with `working_directory_not_git_repo`.

The current runtime is documented in [browser.md](./browser.md), with live viewing and control in [browser_workspace.md](./browser_workspace.md). `browserHarness` is retired; browser-use is a dependency of the single `browser` node, not a second workflow node.

## Where state lives

Everything the app keeps is in the `opencompany-selfhost_data` volume, mounted at `/data`. The compose project is named `opencompany-selfhost` so it never mixes with another compose project called `opencompany`.

| Path | Contents |
|---|---|
| `opencompany.env` | The secrets generated on first start, plus any settings you add |
| `workflow.db`, `credentials.db`, `temporal.db` | App data, encrypted credentials, Temporal history |
| `packages/` | The Temporal CLI and the tools plugins install on first use |
| `workspaces/` | Per-workflow files |
| `claude/`, `whatsapp/` | Claude Code login, WhatsApp session |
| `home/` | `$HOME`: gh, stripe, cf and codex logins, bun and uv caches |

`opencompany.env` holds `API_KEY_ENCRYPTION_KEY`, the key that decrypts `credentials.db`. Back the two up together and keep the file private. If the key changes, stored credentials can no longer be decrypted.

Back up `/data` while the container is stopped. `temporal.db` runs in SQLite's WAL mode, so recent commits live in a `-wal` file next to it until SQLite folds them back in: a copy taken while the app runs needs the `-wal` and `-shm` files too, and a `-wal` file must never be deleted. SQLite needs working file locks, so keep `/data` on the named volume or a local disk, never NFS or SMB.

## Configuration

Settings resolve in this order, the later one winning: `.env.template` (baked into the image), then `/data/opencompany.env`, then the container environment (`environment:` in `docker-compose.yml`). To change a setting, add a `KEY=value` line to `/data/opencompany.env` or an entry under `environment:`, then restart the container.

A secret you pass under `environment:` before the first start is written into `/data/opencompany.env` instead of a random one, so removing it from `docker-compose.yml` later changes nothing.

The image sets these on top of the template:

| Variable | Value | Why |
|---|---|---|
| `DATA_DIR` | `/data` | All state on the volume |
| `OPENCOMPANY_ENV_FILE` | `/data/opencompany.env` | Secrets and your settings on the volume |
| `HOME` | `/data/home` | CLI logins and caches survive upgrades |
| `DEPLOYMENT_MODE` | `self_hosted` | Server posture: full Temporal concurrency, API docs not public |
| `VITE_AUTH_ENABLED` | `true` | Login required |
| `NODEJS_EXECUTOR_HOST` | `127.0.0.1` | The code sidecar binds IPv4 loopback, which exists even where container IPv6 is off |
| `NODEJS_USER_PACKAGES_DIR` | `/data/nodejs-user-packages` | Packages installed for code nodes persist |
| `BROWSER_RUNTIME` | `system` | Use the image's installed browser; no testing-browser fallback |
| `BROWSER_CHROME_PATH` | `/usr/bin/chromium` | Select Chromium supplied by the browser extra |
| `BROWSER_HEADLESS` | `true` | Explicit container override of the desktop-visible default |
| `PYTHONUNBUFFERED` | `1` | Log lines reach `docker compose logs` as they are written |

**Browser runtime configuration:** the Dockerfile sets `BROWSER_RUNTIME=system`, `BROWSER_CHROME_PATH=/usr/bin/chromium` and `BROWSER_HEADLESS=true` for the browser extra. These are already the image defaults; compose `environment:` can override them. The headless image setting explicitly overrides the application's desktop-visible default, which requires a display. Discovery can find installed Chromium, but the explicit path makes image selection deterministic. The former `AGENT_BROWSER_EXECUTABLE_PATH` has been removed from the Dockerfile and is not read by this runtime. `BROWSER_RUNTIME=testing` is the only mode that downloads pinned Chrome for Testing; it is never a missing-browser fallback. Validate the built image and Linux libraries before deployment.

The image has no Node. It links `/usr/local/bin/node` to bun, so the plugin CLIs whose entry scripts start `#!/usr/bin/env node` (vercel, cf) run on bun, as in the official oven/bun images. Browser automation installs browser-use through uv instead.

With the `browser` extra: Chrome refuses to run as root with its sandbox on, so the image adds `--no-sandbox` through a drop-in, `/etc/chromium.d/no-sandbox`. Debian's launcher reads flags only from that directory and ignores a `CHROMIUM_FLAGS` environment variable. Debian's own `dev-shm` drop-in already adds `--disable-dev-shm-usage` when Docker's `/dev/shm` is small.

Those drop-ins apply to Debian's Chromium launcher. OpenCompany supplies its runtime launch flags and detects sandbox/shared-memory constraints. The image already sets `BROWSER_HEADLESS=true` for displayless operation; do not rely on the former agent-browser/Xvfb startup path.

Leave `HOST` and `PORT` at their template values. The backend uses `HOST` to reach itself; the published bind comes from the entrypoint's `--host 0.0.0.0`. `PORT` is repeated in the Dockerfile's `EXPOSE` and the compose mapping, which do not follow a change made in `/data/opencompany.env`. Publish only the app port: the code sidecar, the WhatsApp bridge and Temporal listen on internal ports without authentication.

### Serving other machines

Compose publishes the app on `127.0.0.1` only, so only this machine reaches it. To serve other machines, drop the `127.0.0.1:` prefix from the `ports:` mapping, put a TLS proxy in front, and set `JWT_COOKIE_SECURE=true`. Until you do, the log warns at every start that the session cookie travels over plain HTTP.

The proxy should also refuse `/ws/internal`. That is where the backend's Temporal workers call back in; it skips the login check and runs any node a message names, `shell` included. The handshake requires a token derived from `SECRET_KEY` (see [authentication.md](./authentication.md)), and the workers reach it inside the container, so blocking it at the proxy costs nothing and adds a second layer. Keep `Host` intact through the proxy, or list the public origin in `CORS_ORIGINS`: every WebSocket handshake refuses an `Origin` that is neither the request's own host nor listed there. Compose keeps the `127.0.0.1` mapping by default because login is off until you turn it on.

## Upgrading

```bash
git pull
docker compose up -d --build
```

The volume keeps all state. An upgrade does not refresh the tools downloaded into `/data/packages`; delete a tool's directory there and the app fetches the current version the next time it needs it.

## Stopping

`docker compose stop` gives the backend up to 120 seconds to drain its Temporal workers and record a clean shutdown. If the container is killed before that, the next start treats it as a crash and pauses the running deployments; resume them from the canvas.

With plain `docker run`, pass `--init` and `--stop-timeout 120` to get the same behaviour.

## Limitations

- **Temporal Web UI.** The dev server binds loopback inside the container, so its UI is not reachable from outside.
- **Claude Code login.** The login button in the credentials panel waits for a browser callback on the container's own localhost, so it cannot finish. Log in from an interactive shell in the container (`docker compose exec opencompany sh`) with `CLAUDE_CONFIG_DIR=/data/claude` set, or supply `ANTHROPIC_API_KEY` through `environment:`. Codex has no login button at all; run its login from the same shell.
- **Owner-checked panels under login.** The image always requires a login, and the editor saves workflows through a REST route that records the placeholder owner `owner` rather than the logged-in account. The Canvas, Memory, Context and dataSource panels check that owner, so they can refuse the logged-in user for such a workflow. `company deploy` VMs, which also require a login, behave the same.
- **Browser image integration.** The image selects system Chromium with `BROWSER_CHROME_PATH=/usr/bin/chromium` and enables `BROWSER_HEADLESS=true` for displayless operation. The default system mode fails clearly when no installed browser exists. Testing mode is a separate opt-in download. Both modes install the pinned browser-use CLI with uv. Validate selected-browser version/profile compatibility and Linux libraries in the built image; adding the extra alone is not an end-to-end browser check.
- **Agent nodes need the `git` extra and a git repository.** Claude Code and Codex agents work in git worktrees, and the image contains no `.git`. Run `git init` in a workflow's workspace and make a first commit before pointing an agent at it. Set a commit identity once from a shell in the container (`git config --global user.name ...` and `user.email ...`); it is stored in `/data/home` and survives upgrades.
- **Bind mounts.** A bind mount works in place of the named volume, but the Temporal CLI is then downloaded on first start (a new named volume receives the copy baked into the image), and the directory must allow executables, symlinks and Unix sockets. Prefer the named volume on Windows and macOS hosts.
- **Memory.** Give the container at least 1 GB. At idle the backend holds about 180 MB of process memory and the Temporal dev server about 115 MB; `docker stats` shows up to about 450 MiB because it also counts the page cache of the code they run.
- **Disk.** The image is about 1.3 GB, and the `browser` extra adds about 1.2 GB. A build needs room for its layers on top of that. On Docker Desktop they land on the drive that holds Docker's disk image, which is C: on Windows unless you move it (Settings > Resources > Advanced > Disk image location). A build that fills that drive makes Docker's filesystem read-only and takes down every running container.
- Built and tested on linux/amd64.

## Optional external services

- Redis: `REDIS_ENABLED=true` and `REDIS_URL=redis://<host>:6379`.
- A Temporal cluster: `TEMPORAL_SERVER_ADDRESS=<host>:7233`. With a non-loopback address the backend connects to it instead of spawning its own dev server.

## Maintaining the image

No CI job builds the image. What keeps it in step with the rest of the repo:

- **Toolchain pins.** The `oven/bun` tag must match the root `package.json` `packageManager` (what CI's setup-bun reads), and the `ghcr.io/astral-sh/uv` tag must match `desktop/runtimes.json` (the desktop bundle). `test_docker_image_pins_match_the_bun_and_uv_pins` in `cli/tests/test_release_pipeline_config.py` fails when one moves without the other.
- **The port.** `EXPOSE` and the container side of the compose mapping repeat `PORT` from `.env.template`; `test_docker_port_matches_the_template_port` in the same file fails when the template changes without them.
- **Line endings.** `.gitattributes` pins LF for `*.sh` and `docker/Dockerfile`, so a Windows checkout builds the same image. A CRLF entrypoint fails under `sh` at the first line.
- **The build context.** Docker does not read `.gitignore`. When you add a local state directory or a secret file pattern to `.gitignore`, add it to `.dockerignore` too, or `COPY . .` bakes it into the image.
- **Build steps.** The build stage runs the same two commands as CI (`bun install --frozen-lockfile`, `bun run build`), then a `uv sync --no-dev` that drops the contributor tools. A new build step belongs in `company build`, not in the Dockerfile, so every channel gets it. Step `[6/6]` fetches the Temporal CLI into `DATA_DIR`, which is why a new named volume starts with it.
- **The runtime file list.** The runtime stage copies only what `core/approot.py` reads: `server/`, `client/dist`, `.env.template`, `package.json`, `.opencompany/workflows` and the entrypoint. A new path the backend reads outside `server/` needs a `COPY` line there.
- **Optional tools.** A system package that only some nodes need goes into an `EXTRAS` entry, not the default image.

## Why the container doesn't run `company serve`

`company serve` needs the CLI's own venv and the root `node_modules`, reads only `<app>/.env` (it ignores `OPENCOMPANY_ENV_FILE`), and its supervisor kills the backend 5 seconds after SIGTERM, which can cut the lifespan shutdown short before the clean-shutdown record is written. The entrypoint runs the uvicorn command the desktop shell uses instead; see [desktop_host_contract.md](./desktop_host_contract.md) for the backend's side of that contract.
