# OpenCompany on AWS (Terraform)

One login-gated EC2 instance running OpenCompany per deployment, driven by
`company deploy --provider aws` (`cli/commands/deploy/providers/aws.py`). The
adapter checks the aws CLI and its login, resolves the region (`--region`,
then `AWS_REGION` / `AWS_DEFAULT_REGION`, then the profile, then `us-east-1`),
hands the credentials the CLI resolved to Terraform (its aws provider cannot
read every source the CLI can, `aws login` sessions included), and checks
that the region has a default VPC. `up` then writes `terraform.tfvars.json`
into the deployment's own folder and runs `terraform apply`.

File layout follows the HashiCorp AWS get-started tutorial
(https://developer.hashicorp.com/terraform/tutorials/aws-get-started):
`terraform.tf` (providers), `main.tf`, `variables.tf` (the variable set every
provider module declares, plus `region` and three optional AWS settings),
`outputs.tf` (`external_ip`, `url`, `instance_id`), plus `startup.sh.tftpl`
(the cloud-init startup script template).

```bash
company deploy up --provider aws --name acme-corp --owner-email owner@acme.example
company deploy status --name acme-corp
company deploy destroy --name acme-corp
```

Each `--name` is a separate deployment: its own Terraform state under
`<DATA_DIR>/deploy/<name>/`, its own instance, security group (`<name>-app`),
systemd unit, env file and data directory. That is how one machine creates
one VM per user. Names are 6-30 lowercase letters, digits and hyphens
starting with a letter (GCP's service-account rule, so a name works on both
providers).

| Setting | Value | Why |
|---|---|---|
| Instance | `--machine-type`, default `t3.micro` (2 vCPU, 1 GiB) | Smallest size the app runs on (backend ~200 MB + Temporal ~150 MB). 512 MB OOM-loops. |
| Credit mode | `standard` | Caps compute at the hourly rate under continuous CPU load. |
| Root disk | 10 GiB gp3, deleted with the instance | A fresh install uses ~4.5 GiB. |
| Image | Ubuntu 24.04 LTS (Canonical), the newest when the instance is created | Python 3.12 inside the server's `<3.13` pin. |
| Install | `install.sh` as `ubuntu`, no sudo; a published release only | Root installs leave the venvs unusable by the login user (errors.md #16). The module has no artifact bucket, so `--source local` is refused. |
| bun | installed by `install.sh` (the official bun installer, into `~ubuntu/.bun`) when none is present; no Node, no npm | The published package runs on bun. systemd gets `$APP_HOME/.bun/bin` on PATH and `ExecStart=$APP_HOME/.bun/bin/company serve`. |
| Runtime | `company serve` under systemd as `ubuntu` on `--port` (default `PYTHON_BACKEND_PORT`) | The server rejects ports below 1024; 80/443 stay free for a TLS front door. |
| Temporal | on (the `.env.template` default, as in the Docker image) | Chat, chat and webhook triggers, and cron need it. |
| Security group | 22, 80, 443 and the app port from `--allow-cidr` | Same shape as the gcp module. |
| Re-apply | never replaces or restarts a running instance (`ignore_changes = [ami, user_data]`) | Replacing deletes the root volume and everything in `DATA_DIR`. The boot script runs only on the first boot, so a new image, script or `app_env` reaches new instances only. |

Releases up to 0.2.1 do not come up on a fresh instance: `company serve`
stops with `Project not built` and systemd restarts it in a loop
([errors.md #25](../../../docs-internal/errors.md), fixed after 0.2.1).
`--version` picks the release (the tag without the `v`); the default `latest`
installs the newest one.

`app_env` carries the owner login, fresh JWT/encryption keys, `PORT`,
`DATA_DIR=/var/lib/<name>` and `DEPLOYMENT_MODE=cloud`; `company deploy` builds
it with `build_app_env` (`cli/commands/deploy/_secrets.py`). To run the module
by hand, write `terraform.tfvars.json` with the variables in `variables.tf`
(the file `company deploy up` writes is the template) and run `terraform
init` / `apply` in a folder of its own. First boot takes about 10 minutes on a
t3.micro.

First boot fetches `https://opencompany.sh/install.sh` (override with
`install_sh_url`), and that script asks `https://opencompany.sh/version` for
the release unless `opencompany_version` is set. Both endpoints must be served
from the opencompany.sh site.

Cost at us-east-1 list prices: about $12/month per t3.micro deployment
(instance, disk, public IPv4), and CPU load cannot raise it. Egress past the
free 100 GB/month is the only open-ended line.
