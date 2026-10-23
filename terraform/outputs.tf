output "ip" {
  description = "Public IPv4 address of the droplet."
  value       = digitalocean_droplet.this.ipv4_address
}

output "droplet_id" {
  description = "DigitalOcean droplet ID."
  value       = digitalocean_droplet.this.id
}

output "preview_host" {
  description = "Preview hostname; mirrors blacksmith.preview.preview_host."
  value       = local.preview_host
}

output "preview_url" {
  description = "Preview URL."
  value       = "https://${local.preview_host}"
}

output "ssh_private_key" {
  description = "Private key (OpenSSH) for root on the droplet."
  value       = tls_private_key.ssh.private_key_openssh
  sensitive   = true
}

output "ssh_command" {
  description = "How to SSH into the droplet from this Terraform workspace."
  value       = "ssh -i <(terraform output -raw ssh_private_key) root@${digitalocean_droplet.this.ipv4_address}"
}
