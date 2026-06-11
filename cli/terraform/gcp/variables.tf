# Shared deployment variable interface (every provider module declares the
# same set, so `machina deploy` writes one tfvars shape regardless of provider).

variable "project" {
  type        = string
  description = "GCP project id."
}

variable "region" {
  type        = string
  description = "GCP region."
}

variable "zone" {
  type        = string
  description = "GCP zone for the instance."
}

variable "machine_type" {
  type        = string
  description = "Compute Engine machine type."
}

variable "port" {
  type        = number
  description = "Public port the app binds and the firewall opens."
}

variable "allow_cidr" {
  type        = string
  description = "Firewall source range (e.g. 0.0.0.0/0 or <your-ip>/32)."
}

variable "source_mode" {
  type        = string
  description = "Install source: 'local' (npm pack tarball via bucket) or 'release' (npm registry)."
}

variable "machinaos_version" {
  type        = string
  description = "machinaos version to install when source_mode = 'release'."
}

variable "pack_tarball" {
  type        = string
  default     = ""
  description = "Absolute path to the npm pack tarball (source_mode = 'local')."
}

variable "app_env" {
  type        = map(string)
  sensitive   = true
  description = "KEY=VALUE map rendered into the VM's /etc/machinaos/machina.env."
}
