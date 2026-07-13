"""``company deploy`` -- provision a fresh cloud VM running OpenCompany.

A thin wrapper over **Terraform**: the command generates secrets + a tfvars
file and drives ``terraform init/apply/destroy``. Terraform owns every cloud
resource (VM, firewall, optional artifact bucket) declaratively, with its own
state. Per-provider root modules live under ``cli/terraform/<provider>/`` and
share one variable interface, so a new provider is one new module + no CLI
change.

The Typer group is assembled in ``cli/cli.py`` (lazy-wrapped leaves), mirroring
the ``daemon`` group; this package just holds the verb implementations + shared
helpers (``_config`` / ``_secrets`` / ``_state`` / ``_terraform``).
"""
