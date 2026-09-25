#!/bin/sh
# OpenCompany container entrypoint. See docs-internal/docker.md.
#
# 1. Write SECRET_KEY, JWT_SECRET_KEY and API_KEY_ENCRYPTION_KEY into the env
#    file on the data volume ($OPENCOMPANY_ENV_FILE) the first time they are
#    missing, so they survive container recreation. API_KEY_ENCRYPTION_KEY must
#    never change afterwards, or credentials.db can no longer be decrypted.
#    A key already in the file is kept. A missing one takes the value from the
#    container environment when set there (so dropping it from compose later
#    changes nothing), otherwise a fresh random one. The environment still
#    wins over the file at runtime.
# 2. Run the backend without the CLI supervisor: the uvicorn argv of
#    cli/commands/serve.py and desktop/src/main/backend.ts, bound to 0.0.0.0
#    so the published port reaches it. The port resolves through
#    core.env_defaults, so .env.template stays its single source of truth.
set -eu

cd /app/server

mkdir -p "$HOME"
touch "$OPENCOMPANY_ENV_FILE"
chmod 600 "$OPENCOMPANY_ENV_FILE"
# A hand-edited file can lack its final newline; appending would then glue a
# key onto its last line.
[ -z "$(tail -c 1 "$OPENCOMPANY_ENV_FILE")" ] || echo >>"$OPENCOMPANY_ENV_FILE"
for key in SECRET_KEY JWT_SECRET_KEY API_KEY_ENCRYPTION_KEY; do
    # The line shapes core/env_defaults reads (`KEY=v`, ` KEY = v`); an empty
    # value counts as missing, and the line appended below then wins.
    grep -Eq "^[[:space:]]*$key[[:space:]]*=[[:space:]]*[^[:space:]]" "$OPENCOMPANY_ENV_FILE" && continue
    value=$(printenv "$key" || true)
    if [ -z "$value" ]; then
        # Its own assignment, so a failure stops the start under `set -e`
        # instead of writing an empty key.
        value=$(python -c 'import secrets; print(secrets.token_hex(24))')
    fi
    echo "$key=$value" >>"$OPENCOMPANY_ENV_FILE"
done

port=$(.venv/bin/python -c 'from core.env_defaults import env_value; print(env_value("PORT"))')
exec .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port "$port" \
    --log-level warning --timeout-graceful-shutdown 5
