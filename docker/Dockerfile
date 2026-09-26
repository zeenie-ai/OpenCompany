# OpenCompany self-hosting image. See docs-internal/docker.md.
#
# Build: the canonical cross-platform build, exactly as CI runs it
#        (bun install --frozen-lockfile, then bun run build), in a
#        throwaway stage.
# Run:   a slim stage holding only what the backend reads at run time, run
#        the way the desktop shell runs it: uvicorn directly, settings and
#        secrets in an env file on the data volume, readiness on
#        /health/ready.
#
# The default image carries no optional tools. EXTRAS adds them, space
# separated:
#   browser  Chromium, fonts and Xvfb for the browser node, and uv, which
#            installs the Browser node's browser-use CLI
#   git      git, for the Claude Code and Codex agent nodes (they work in git
#            worktrees)
#
# From the repo root:  docker compose up -d --build
# With extras:         set EXTRAS under build.args in docker-compose.yml

# bun matches the root package.json "packageManager"; uv matches
# desktop/runtimes.json. cli/tests/test_release_pipeline_config.py keeps the
# three in step.
FROM oven/bun:1.4.0-slim AS bun
FROM ghcr.io/astral-sh/uv:0.12.13 AS uv

# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
# glibc Debian with Python 3.12 as both `python` and `python3` (the root
# package.json scripts call bare `python`). The venv built here links to this
# interpreter, so the runtime stage uses the same base image.
FROM python:3.12-slim-trixie AS build

COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
COPY --from=uv /uv /usr/local/bin/

# The build fetches the Temporal CLI into DATA_DIR (company build step
# [6/6]); the runtime stage copies it into /data, so a new named volume starts
# with it (Docker copies the image's content into an empty named volume on
# first mount).
ENV DATA_DIR=/data

WORKDIR /app
COPY . .

# CI=true keeps the root install hooks inert, as on GitHub runners
# (scripts/preinstall.js, scripts/postinstall.js). company build syncs the
# venv with the dev group (pytest, ruff) for contributors; the second,
# --frozen sync removes it. The tests and the sidecar's TypeScript source
# stay behind in this stage.
RUN CI=true bun install --frozen-lockfile \
    && bun run build \
    && cd server \
    && uv sync --frozen --no-dev --extra docs \
    && rm -rf tests nodejs/src nodejs/node_modules

# Only the browser extra ships uv. Staged here because COPY has no condition;
# the runtime stage copies this directory whether or not it is empty.
ARG EXTRAS=""
RUN mkdir -p /extras/bin \
    && case " $EXTRAS " in *" browser "*) cp /usr/local/bin/uv /extras/bin/ ;; esac

# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim-trixie

ARG EXTRAS=""

COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun
COPY --from=build /extras/bin/ /usr/local/bin/

# The image has no Node. npm CLIs whose bin scripts start
# `#!/usr/bin/env node` (agent-browser, vercel, cf) run on bun under that
# name, as in the official oven/bun images.
RUN ln -s /usr/local/bin/bun /usr/local/bin/node

# browser: Debian's /usr/bin/chromium launcher takes extra flags only from
# /etc/chromium.d/ (it resets CHROMIUM_FLAGS from the environment) and
# already adds --disable-dev-shm-usage when /dev/shm is small; the drop-in
# adds --no-sandbox, without which Chrome refuses to run as root. The
# browser node runs headed by default and agent-browser starts a private Xvfb
# display when DISPLAY is unset.
RUN set -eu; \
    pkgs=""; \
    for extra in $EXTRAS; do \
        case "$extra" in \
            browser) pkgs="$pkgs chromium fonts-liberation xvfb" ;; \
            git) pkgs="$pkgs git" ;; \
            *) echo "unknown EXTRAS entry: $extra (known: browser git)" >&2; exit 1 ;; \
        esac; \
    done; \
    if [ -n "$pkgs" ]; then \
        apt-get update; \
        apt-get install -y --no-install-recommends $pkgs; \
        rm -rf /var/lib/apt/lists/*; \
    fi; \
    if [ -d /etc/chromium.d ]; then \
        echo 'export CHROMIUM_FLAGS="$CHROMIUM_FLAGS --no-sandbox"' > /etc/chromium.d/no-sandbox; \
    fi

# What the backend reads at run time (core/approot.py): the server tree with
# its venv, the built UI, the env template, the version, the seed workflows.
# The sidecar bundle is server/nodejs/dist and needs no node_modules.
WORKDIR /app
COPY --from=build /app/.env.template /app/package.json ./
COPY --from=build /app/.opencompany/workflows .opencompany/workflows
COPY --from=build /app/client/dist client/dist
COPY --from=build /app/docker/entrypoint.sh docker/entrypoint.sh
COPY --from=build /app/server server
COPY --from=build /data /data

# Runtime settings; everything else keeps its .env.template default.
# HOME lives on the volume so CLI logins (gh, stripe, cf, codex) and caches
# survive upgrades. Browser system mode selects the browser extra's Chromium;
# this server image has no desktop display, so use explicit headless mode.
ENV DATA_DIR=/data \
    HOME=/data/home \
    OPENCOMPANY_ENV_FILE=/data/opencompany.env \
    DEPLOYMENT_MODE=self_hosted \
    VITE_AUTH_ENABLED=true \
    NODEJS_EXECUTOR_HOST=127.0.0.1 \
    NODEJS_USER_PACKAGES_DIR=/data/nodejs-user-packages \
    BROWSER_RUNTIME=system \
    BROWSER_FAMILY=chromium \
    BROWSER_HEADLESS=true \
    PYTHONUNBUFFERED=1

WORKDIR /app/server

# The app port: PORT / PYTHON_BACKEND_PORT in .env.template.
EXPOSE 5678

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD ["/app/server/.venv/bin/python", "-c", "import urllib.request; from core.env_defaults import env_value; urllib.request.urlopen('http://127.0.0.1:%s/health/ready' % env_value('PORT'), timeout=4)"]

ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]
