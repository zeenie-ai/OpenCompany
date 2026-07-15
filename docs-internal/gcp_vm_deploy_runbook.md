# GCP VM Deploy Runbook (released npm package, manual gcloud + cf)

Step-by-step runbook for deploying the **released** `@zeenie-ai/opencompany` npm package on a fresh
GCP VM behind a Cloudflare-proxied domain, with the single-owner login gate enabled.
Written to be executable by an AI agent (or a human) with no other context.

This is the manual path — it does NOT use `company deploy` / Terraform
(`cli/commands/deploy/`, `cli/terraform/`). It was derived from a real deployment to
`demo.zeenie.xyz` (June 2026, before the scoped-package cutover) and encodes every pitfall hit on the way.

The unscoped `opencompany` package belongs to a different publisher. Do not install
or remove it while following this runbook.

## Parameters

Decide these up front; they appear in commands below as `<PLACEHOLDERS>`.

| Placeholder | Example | Notes |
|---|---|---|
| `<PROJECT>` | `zeenie` | GCP project id (`gcloud config get-value project`) |
| `<VM_NAME>` | `demo` | Instance name |
| `<REGION>` / `<ZONE>` | `asia-south1` / `asia-south1-a` | Mumbai. Any region works |
| `<IP_NAME>` | `opencompany-india-ip` | Name for the reserved static IP |
| `<DOMAIN>` | `demo.zeenie.xyz` | Must be a subdomain of a zone in the Cloudflare account |
| `<SUBDOMAIN>` | `demo` | The record name within the zone |
| `<OWNER_EMAIL>` | `rohith@zeenie.xyz` | Login email for the owner account |
| `<OWNER_PASSWORD>` | (generated) | >= 8 chars; generate 20-char alphanumeric if not supplied |
| `<JWT_KEY>` `<SECRET_KEY>` `<ENC_KEY>` | (generated) | Three independent 48-hex secrets |

Generate secrets (run locally):

```bash
python -c "import secrets,string; a=string.ascii_letters+string.digits; \
print('PW='+''.join(secrets.choice(a) for _ in range(20))); \
print('JWT='+secrets.token_hex(24)); print('SEC='+secrets.token_hex(24)); \
print('ENC='+secrets.token_hex(24))"
```

## Prerequisites (verify before starting)

1. `gcloud` installed and authenticated: `gcloud config get-value project` returns the
   project and `gcloud config get-value account` returns an account. On Windows, ignore
   the harmless `Test-Path ... bundledpython ... is denied` stderr noise — output after
   it is still valid.
2. `cf` (Cloudflare CLI, npm package) installed and authenticated:
   `cf auth whoami` shows `"authenticated": true` with `dns_records:edit` scope.
3. No Terraform, no ADC needed — plain gcloud credentials suffice.

## Known pitfalls (read first — these each cost a redeploy or a debugging loop)

1. **Debian 12 fails.** The npm package's `postinstall` hard-requires Python 3.12+;
   Debian 12 ships 3.11 and `npm install -g @zeenie-ai/opencompany` exits 1
   (`ERROR: Python 3.12+ is required.`). Use **Ubuntu 24.04** (`ubuntu-2404-lts-amd64`
   in `ubuntu-os-cloud`), which ships Python 3.12.
2. **The released package has no `company serve`.** Released CLI commands are only
   `start | dev | stop | build | clean | doctor | help | version`. The single-port
   `serve` command and `SERVE_STATIC_CLIENT` backend support exist only in unreleased
   source. Production shape for the release is the README's `company start`
   (static client on **:3000**, backend uvicorn on **:3010**) plus an nginx reverse
   proxy on :80/:443.
3. **`MACHINA_OWNER_*` env seeding is NOT honored by the release.** Setting
   `MACHINA_OWNER_EMAIL/PASSWORD` does nothing in 0.0.88. Instead: with
   `AUTH_MODE=single`, registration is open until the first user registers and that
   user becomes owner. Register the owner via `POST /api/auth/register` immediately
   after first boot (step 7). Registration auto-closes afterwards.
4. **Unknown env vars are fine, but don't set `PORT=80`.** The released backend does
   not read `PORT`; nginx owns :80. Keep the env file to the keys listed in step 2.
5. **Cloudflare proxied + "Full" SSL mode → 521 without origin TLS.** If the zone
   forces HTTPS and SSL mode is Full, Cloudflare connects to origin :443. Fix: nginx
   listens on 443 with a **self-signed** cert (Full mode accepts it; only
   Full-strict would reject) and the GCP firewall must allow **tcp:443** too.
6. **Startup script must have LF line endings.** When authoring on Windows, strip CRLF
   before passing to `--metadata-from-file` or bash on the VM fails obscurely.
7. **GCP startup scripts re-run on every boot.** Keep the script idempotent (it is,
   below). A stop/start re-provisions harmlessly.
8. **Renaming a VM requires stopping it** (`gcloud compute instances set-name`), and an
   *ephemeral* IP would be released on recreate. Reserve the static IP first (step 3)
   so the DNS record never goes stale.
9. **PowerShell quoting eats `|` and `\"` in long ssh `--command` strings.** Write a
   local `.sh` file, `gcloud compute scp` it, then `ssh --command="sudo bash /tmp/x.sh"`.
10. **WebSocket 403 without a cookie is correct behavior**, not a proxy bug. The
    backend rejects unauthenticated `/ws/status` upgrades; with a valid login cookie
    the same handshake returns `101 Switching Protocols`.

## Step 1 — Reserve a static IP

```bash
gcloud compute addresses create <IP_NAME> --region=<REGION> --project=<PROJECT>
gcloud compute addresses describe <IP_NAME> --region=<REGION> --project=<PROJECT> --format="value(address)"
```

Save the returned address as `<STATIC_IP>`.

(If the VM already exists with an ephemeral IP, promote it instead:
`gcloud compute addresses create <IP_NAME> --region=<REGION> --addresses=<CURRENT_IP>`.)

## Step 2 — Author the startup script (locally)

Write `opencompany-startup.sh` with the placeholders filled in. This is the complete,
corrected, idempotent script (toolchain + release install + env + nginx with TLS +
systemd):

```bash
#!/bin/bash
# OpenCompany VM startup script: released npm package via `company start`,
# nginx on :80/:443 (self-signed TLS for Cloudflare Full mode), login gate on.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "[opencompany] installing toolchain..."
apt-get update
apt-get install -y curl ca-certificates git build-essential pkg-config libffi-dev libssl-dev nginx
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y nodejs
corepack enable || true
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

echo "[opencompany] installing OpenCompany released package..."
npm install -g @zeenie-ai/opencompany@latest

echo "[opencompany] writing login-gate env..."
mkdir -p /etc/opencompany
cat > /etc/opencompany/opencompany.env <<'OPENCOMPANY_ENV_EOF'
HOST=0.0.0.0
DATA_DIR=/var/lib/opencompany
WORKSPACE_BASE_DIR=workspaces
VITE_AUTH_ENABLED=true
AUTH_MODE=single
JWT_COOKIE_SECURE=true
JWT_COOKIE_SAMESITE=lax
TEMPORAL_ENABLED=false
REDIS_ENABLED=false
LOG_FORMAT=text
JWT_SECRET_KEY=<JWT_KEY>
SECRET_KEY=<SECRET_KEY>
API_KEY_ENCRYPTION_KEY=<ENC_KEY>
OPENCOMPANY_ENV_EOF
chmod 600 /etc/opencompany/opencompany.env
mkdir -p /var/lib/opencompany

echo "[opencompany] self-signed TLS cert (Cloudflare Full mode accepts it)..."
mkdir -p /etc/nginx/certs
if [ ! -f /etc/nginx/certs/origin.crt ]; then
  openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
    -keyout /etc/nginx/certs/origin.key -out /etc/nginx/certs/origin.crt \
    -subj "/CN=<DOMAIN>"
fi

echo "[opencompany] nginx reverse proxy (SPA :3000, backend :3010)..."
cat > /etc/nginx/sites-available/opencompany <<'NGINX_EOF'
server {
    listen 80 default_server;
    listen 443 ssl default_server;
    ssl_certificate /etc/nginx/certs/origin.crt;
    ssl_certificate_key /etc/nginx/certs/origin.key;
    server_name <DOMAIN> _;
    client_max_body_size 50m;

    location /api/ {
        proxy_pass http://127.0.0.1:3010;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
    location /ws/ {
        proxy_pass http://127.0.0.1:3010;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
    location /webhook/ {
        proxy_pass http://127.0.0.1:3010;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    location = /health {
        proxy_pass http://127.0.0.1:3010;
    }
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
NGINX_EOF
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/opencompany /etc/nginx/sites-enabled/opencompany
nginx -t
systemctl enable --now nginx
systemctl reload nginx

echo "[opencompany] installing systemd service (README usage: company start)..."
OPENCOMPANY_BIN=$(command -v company)
OPENCOMPANY_PACKAGE_DIR="$(npm root -g)/@zeenie-ai/opencompany"
test -d "$OPENCOMPANY_PACKAGE_DIR"
cat > /etc/systemd/system/opencompany.service <<SERVICE_EOF
[Unit]
Description=OpenCompany (released, company start)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/opencompany/opencompany.env
WorkingDirectory=$OPENCOMPANY_PACKAGE_DIR
ExecStart=$OPENCOMPANY_BIN start
Restart=on-failure
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
SERVICE_EOF

systemctl daemon-reload
systemctl enable --now opencompany
systemctl restart opencompany
echo "[opencompany] done."
```

On Windows, force LF endings after writing:

```powershell
$p = "$env:TEMP\opencompany-startup.sh"
[IO.File]::WriteAllText($p, [IO.File]::ReadAllText($p).Replace("`r`n","`n"))
```

## Step 3 — Firewall + VM

```bash
gcloud compute firewall-rules create opencompany-allow-http \
  --project=<PROJECT> --direction=INGRESS --action=ALLOW \
  --rules=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 --target-tags=opencompany
```

```bash
gcloud compute instances create <VM_NAME> \
  --project=<PROJECT> --zone=<ZONE> \
  --machine-type=e2-standard-2 \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB --boot-disk-type=pd-balanced \
  --tags=opencompany --address=<STATIC_IP> \
  --metadata-from-file=startup-script=opencompany-startup.sh
```

Notes: `e2-standard-2` (2 vCPU / 8 GB) is comfortable; the old `e2-micro` is too small.
Updating an existing firewall rule uses `--rules=` (not `--allow=`):
`gcloud compute firewall-rules update opencompany-allow-http --rules="tcp:80,tcp:443"`.

## Step 4 — DNS via cf CLI

The `cf` CLI needs an account + zone context once, and `records create` takes a raw
JSON `--body` (individual flags like `--type/--name` are NOT supported):

```bash
cf zones list                          # find the zone; grab account id from the output
cf context set account-id <ACCOUNT_ID>
cf context set zone <ZONE_DOMAIN>      # e.g. zeenie.xyz
cf dns records list                    # check the name is free
cf dns records create --body '{"type":"A","name":"<SUBDOMAIN>","content":"<STATIC_IP>","proxied":true,"ttl":1,"comment":"OpenCompany VM <ZONE> (gcloud, opencompany@latest)"}'
```

Delete a record by id: `cf dns records delete <RECORD_ID>`.

`proxied: true` matches the zone's other app records; Cloudflare terminates public TLS
and (in Full SSL mode) connects to the origin's self-signed :443.

## Step 5 — Wait for provisioning

Provisioning takes ~5-8 minutes (apt + Node 22 + npm install with Python dep sync).
Poll until healthy:

```bash
until curl -sf -m 5 http://<STATIC_IP>/health; do sleep 10; done
```

If it never comes up, debug in this order:

```bash
# startup script progress / errors:
gcloud compute instances get-serial-port-output <VM_NAME> --zone=<ZONE> | grep -E "opencompany|error"
# service state:
gcloud compute ssh <VM_NAME> --zone=<ZONE> --quiet --command="systemctl is-active opencompany nginx; sudo journalctl -u opencompany -n 40 --no-pager"
```

## Step 6 — Register the owner (this IS the login setup)

Because the release ignores `MACHINA_OWNER_*` env (pitfall 3), create the owner via the
API the moment health is green. First user in `single` mode becomes owner and closes
registration:

```bash
curl -s -X POST http://<STATIC_IP>/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"<OWNER_EMAIL>","password":"<OWNER_PASSWORD>","display_name":"Owner"}'
# expect: {"success":true,"user":{...,"is_owner":true}}
```

Do this promptly — until it runs, anyone reaching the IP could register as owner.
(Optionally create the VM with `--allow-cidr` style source-range restriction on the
firewall first, then widen after registering.)

## Step 7 — Verify end-to-end

All of these must pass before declaring success:

```bash
# 1. health via domain (Cloudflare -> origin TLS):
curl -s https://<DOMAIN>/health                    # 200, {"status":"healthy",...}
# 2. SPA serves:
curl -s https://<DOMAIN>/ | grep -o "<title>[^<]*</title>"   # <title>OpenCompany</title>
# 3. auth gate on, registration closed:
curl -s https://<DOMAIN>/api/auth/status
# expect: {"auth_enabled":true,"auth_mode":"single","can_register":false,...}
# 4. login works (200 + HttpOnly machina_token cookie):
curl -s -i -X POST https://<DOMAIN>/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"<OWNER_EMAIL>","password":"<OWNER_PASSWORD>"}' | head -12
# 5. WebSocket: 403 without cookie (correct), 101 with it:
cookie=$(curl -s -i -X POST https://<DOMAIN>/api/auth/login -H "Content-Type: application/json" \
  -d '{"email":"<OWNER_EMAIL>","password":"<OWNER_PASSWORD>"}' \
  | grep -i '^set-cookie' | sed 's/^[Ss]et-[Cc]ookie: //' | cut -d';' -f1)
curl -s -i -N -m 8 -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" -H "Sec-WebSocket-Version: 13" \
  -H "Cookie: $cookie" https://<DOMAIN>/ws/status | head -3   # HTTP/1.1 101
```

If `https://<DOMAIN>` returns **521**: origin :443 is unreachable — confirm the
firewall allows tcp:443 and nginx is listening (`sudo ss -tlnp | grep :443`).
If **526**: the zone is Full (strict); replace the self-signed cert with a Cloudflare
Origin CA cert.

## Step 8 — Hygiene

- Delete any local temp scripts containing the secrets after the deploy.
- Report the URL, owner email, and password to the operator once; the password is not
  recoverable from the VM (only its bcrypt hash is stored).

## Teardown

```bash
gcloud compute instances delete <VM_NAME> --zone=<ZONE> --project=<PROJECT> --quiet
gcloud compute addresses delete <IP_NAME> --region=<REGION> --project=<PROJECT> --quiet
gcloud compute firewall-rules delete opencompany-allow-http --project=<PROJECT> --quiet
cf dns records list   # find the record id
cf dns records delete <RECORD_ID>
```

## Operations cheat sheet

```bash
# logs:
gcloud compute ssh <VM_NAME> --zone=<ZONE> --quiet --command="sudo journalctl -u opencompany -f"
# restart app:
gcloud compute ssh <VM_NAME> --zone=<ZONE> --quiet --command="sudo systemctl restart opencompany"
# upgrade to a new release:
gcloud compute ssh <VM_NAME> --zone=<ZONE> --quiet --command="sudo npm install -g @zeenie-ai/opencompany@latest && sudo systemctl restart opencompany"
# env (secrets live here):
#   /etc/opencompany/opencompany.env   (chmod 600; service reads it via EnvironmentFile)
# data:
#   /var/lib/opencompany           (DATA_DIR: DBs, workspaces, packages)
```
