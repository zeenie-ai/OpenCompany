output "external_ip" {
  value       = google_compute_instance.vm.network_interface[0].access_config[0].nat_ip
  description = "The VM's public IP."
}

output "url" {
  value       = "http://${google_compute_instance.vm.network_interface[0].access_config[0].nat_ip}:${var.port}"
  description = "The app URL (login gate)."
}
