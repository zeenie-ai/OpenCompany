# Deployment — Current Self-Deploy CLI + Legacy Docker Reference

This document summarizes the current `company deploy` self-deploy CLI. Current
container deployment instructions live in [docker.md](docker.md), with general
setup in [Setup](SETUP.md). The retired multi-container topology and its removed
Compose files, Docker directory and wrapper are preserved in the separate
[historical Docker reference](ARCHIVE/legacy_docker_topology.md). The current
container path is a single self-hosting image built from source.

---

## Self-Deploy CLI (`company deploy`) — current path

One command provisions a login-gated OpenCompany VM on GCP or AWS. Two stages:

1. **Operator's cloud CLI** (`providers/gcp.py`, `providers/aws.py`): gcloud handles auth +
   project/region/zone resolution + ADC verification + API enablement; the aws CLI handles
   auth (`sts get-caller-identity`) + region, hands its resolved credentials to Terraform
   (`aws configure export-credentials`, so `aws login` sessions work), and checks the region
   has a default VPC. Each adapter also names its default `--machine-type` and the `--source`
   values its module supports.
2. **Terraform** (`cli/terraform/<provider>/`, one shared variable set) owns all resources.
   GCP: firewall, artifact bucket (local `bun pm pack` source), service account, and a
   startup script (`startup.sh.tftpl`, GCE metadata, re-run on every boot) that installs bun +
   uv (no Node, no npm), `bun add -g`s the package, provisions its Python side with
   `company provision`, and runs `company serve` under systemd. AWS: a security group and an
   EC2 instance whose cloud-init script (first boot only) runs `install.sh` as `ubuntu` with a
   published release (`--source release` only) and runs `company serve` under systemd as that
   user; a later apply never replaces or restarts the instance (`ignore_changes = [ami,
   user_data]`). See [cli/terraform/aws/README.md](../cli/terraform/aws/README.md).

> **Known issue in 0.2.0 and 0.2.1 ([errors.md #25 and #26](./errors.md))**: on a registry
> install, `company serve` stops with `Project not built. Run "company build" first.`, so the
> VM's systemd service fails and keeps restarting, and the package also lacks the JS executor
> sidecar bundle. #25 is fixed after 0.2.1; on those two releases run `company build` once on the VM as the
> service user, then `systemctl restart opencompany` (`machinaos` on a pre-rebrand deployment).

Login gate = built-in auth (`VITE_AUTH_ENABLED=true`, `AUTH_MODE=single`) with the owner
credential generated at deploy time and seeded on first boot. `build_app_env`
(`cli/commands/deploy/_secrets.py`) also sets `DEPLOYMENT_MODE=cloud` on the VM and mints
fresh `JWT_SECRET_KEY` / `SECRET_KEY` / `API_KEY_ENCRYPTION_KEY` per deploy. Temporal, the
event framework and `HOST` keep their `.env.template` values (Temporal on), as in the Docker
image, so chat, triggers and cron work on the VM.

`--name` (default `opencompany`) names a deployment: its state folder and its cloud resource
id. One machine drives one VM per name; a name stays with the provider that created it.

```bash
company deploy up --provider gcp --owner-email you@example.com   # provision + install + print URL/creds
company deploy up --provider aws --name acme-corp --owner-email owner@acme.example
company deploy status [--name acme-corp]                         # URL + /health
company deploy destroy [--name acme-corp]                        # terraform destroy + clear state
```

### Key files

| Path | Responsibility |
|------|----------------|
| `cli/commands/serve.py` | Single-port runtime: uvicorn fronts API + WS + built SPA. `serve` supervises exactly one process — the JS code-exec sidecar (on bun), WhatsApp and the Temporal dev server are backend-owned and spawn on demand |
| `cli/commands/deploy/` | Verbs (`up.py` / `status.py` / `destroy.py`), `_secrets.py`, `_state.py`, `_terraform.py` (Terraform driver), `providers/` (`gcp.py` / `aws.py` provider CLI adapters) |
| `cli/terraform/gcp/` | HCL module (`main.tf` / `variables.tf` / `outputs.tf`) + `startup.sh.tftpl` GCE startup-script template |
| `cli/terraform/aws/` | HCL module (`terraform.tf` / `main.tf` / `variables.tf` / `outputs.tf`) + `startup.sh.tftpl` cloud-init template |

### State + delinking

- Deployment state lives at `<DATA_DIR>/deploy/<name>/` (`~/.opencompany/deploy/opencompany/` by
  default); only `company deploy destroy` removes it. `company clean` keeps `.opencompany/deploy`
  inside the repo (see `_OPENCOMPANY_KEEP` in `cli/commands/clean.py`), which matters only when
  `DATA_DIR` points there.
- Upgrade compatibility is deliberate: the CLI discovers pre-rebrand
  `deploy/machinaos/` state under the configured data root, `~/.machina`, or a
  checkout-local `.machina` directory. It retains the `machinaos` cloud and
  systemd resource id for that deployment, preventing Terraform from replacing
  or orphaning live infrastructure. Fresh deployments use `opencompany`.
- The `machina` executable remains only as a deprecated legacy alias; use `company`
  in new commands and automation.
- The deploy code is fully delinked from `company build` / `company clean`: lazy verb stubs in
  `cli/cli.py` mean nothing in the build pipeline imports it.

The legacy `deploy.sh` (docker-compose images over SCP to a GCE box) was removed.

---

## Historical Docker topology

The retired multi-container Docker instructions are preserved in
[Legacy Docker topology](ARCHIVE/legacy_docker_topology.md). That historical
body is not an executable setup recipe for the current application. Use
[Docker deployment](docker.md) for the supported single-container image.
