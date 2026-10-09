# Shared deployment variable interface: every provider module declares the
# same set (see ../gcp/variables.tf), so `company deploy` writes one tfvars
# shape whatever the provider. `region` comes from the aws adapter; the last
# three variables are AWS-only and optional.

variable "region" {
  type        = string
  description = "AWS region."
}

variable "machine_type" {
  type        = string
  description = "EC2 instance type. t3.micro (2 vCPU, 1 GiB) is the smallest size the app runs on; 512 MB instances OOM-loop."
}

variable "port" {
  type        = number
  description = "Public port the app binds and the security group opens. The server rejects ports below 1024; 80/443 are left for a TLS front door."
}

variable "allow_cidr" {
  type        = string
  description = "Security group source range (e.g. 0.0.0.0/0 or <your-ip>/32)."
}

variable "source_mode" {
  type        = string
  description = "Install source. This module installs a published release with install.sh, so only 'release' is accepted."

  validation {
    condition     = var.source_mode == "release"
    error_message = "The aws module installs a published release with install.sh: set source_mode = \"release\" (company deploy --source release)."
  }
}

variable "opencompany_version" {
  type        = string
  description = "opencompany version to install from npm: the release tag without the v, or latest."
}

variable "resource_name" {
  type        = string
  description = "Durable id: the instance's Name tag, the security group's name prefix, and the systemd unit, env file and data directory names on the VM."
}

variable "pack_tarball" {
  type        = string
  default     = ""
  description = "Unused here: the shared interface's tarball for source_mode = 'local', which this module does not support."
}

variable "app_env" {
  type        = map(string)
  sensitive   = true
  description = "KEY=VALUE map rendered into the VM's systemd EnvironmentFile (company deploy builds it with build_app_env in cli/commands/deploy/_secrets.py)."
}

variable "root_volume_gb" {
  type        = number
  default     = 10
  description = "Root gp3 volume size in GiB. A fresh install uses about 4.5 GiB."
}

variable "key_name" {
  type        = string
  default     = ""
  description = "Existing EC2 key pair name for SSH. Empty = no key pair; EC2 Instance Connect still works."
}

variable "install_sh_url" {
  type        = string
  default     = "https://opencompany.sh/install.sh"
  description = "URL of the OpenCompany install script the first boot runs. Override to test an unreleased installer."
}
