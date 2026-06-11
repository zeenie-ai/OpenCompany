terraform {
  required_version = ">= 1.3"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project
  region  = var.region
}

locals {
  # The instance is always named "machinaos" (one deployment per project).
  use_local   = var.source_mode == "local"
  res_name    = "machinaos"
  bucket_name = "${var.project}-machinaos"
  object_name = "machinaos.tgz"
}

# --- Artifact bucket + object (local source only) --------------------------

resource "google_storage_bucket" "artifact" {
  count                       = local.use_local ? 1 : 0
  name                        = local.bucket_name
  location                    = var.region
  force_destroy               = true
  uniform_bucket_level_access = true
}

resource "google_storage_bucket_object" "pkg" {
  count  = local.use_local ? 1 : 0
  name   = local.object_name
  bucket = google_storage_bucket.artifact[0].name
  source = var.pack_tarball
}

# --- VM service account (reads the artifact bucket) ------------------------

resource "google_service_account" "vm" {
  account_id   = local.res_name
  display_name = "MachinaOs VM"
}

resource "google_storage_bucket_iam_member" "vm_read" {
  count  = local.use_local ? 1 : 0
  bucket = google_storage_bucket.artifact[0].name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.vm.email}"
}

# --- Firewall: app port + SSH ----------------------------------------------

resource "google_compute_firewall" "app" {
  name    = "${local.res_name}-app"
  network = "default"

  allow {
    # CF-proxied front door (80 http-redirected, 443 TLS via nginx) + SSH +
    # the app port for direct-IP access.
    protocol = "tcp"
    ports    = ["80", "443", tostring(var.port), "22"]
  }

  source_ranges = [var.allow_cidr]
  target_tags   = [local.res_name]
}

# --- Instance --------------------------------------------------------------

resource "google_compute_instance" "vm" {
  name         = local.res_name
  machine_type = var.machine_type
  zone         = var.zone
  tags         = [local.res_name]

  boot_disk {
    initialize_params {
      # Ubuntu 24.04 ships Python 3.12 natively — required by the package's
      # install (server needs >=3.11,<3.13; the CLI needs >=3.12). 22.04
      # ships 3.10 and the npm postinstall hard-fails on it.
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = 40
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = "default"
    access_config {} # ephemeral external IP
  }

  service_account {
    email  = google_service_account.vm.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    startup-script = templatefile("${path.module}/startup.sh.tftpl", {
      app_env     = var.app_env
      source_mode = var.source_mode
      version     = var.machinaos_version
      bucket      = local.use_local ? google_storage_bucket.artifact[0].name : ""
      object      = local.object_name
    })
  }

  # The startup script reads the bucket object; ensure it exists + is readable first.
  depends_on = [
    google_storage_bucket_object.pkg,
    google_storage_bucket_iam_member.vm_read,
  ]
}
