locals {
  repo_slug = replace(lower(var.repo), "/", "-")
  name      = "blacksmith-${local.repo_slug}-issue-${var.issue_number}"

  # KEY=value files read by agent/bootstrap.sh. Values are written verbatim inside a YAML
  # block scalar, so quotes, slashes, '+', '=', '$' and '#' survive unchanged.
  # Values containing newlines are NOT supported: they would split into extra lines.
  env_map = {
    CLAUDE_CODE_OAUTH_TOKEN = var.claude_oauth_token
    BLACKSMITH_GITHUB_TOKEN = var.github_token
    FIGMA_API_KEY           = var.figma_api_key
  }
  config_map = {
    ISSUE          = tostring(var.issue_number)
    REPO           = var.repo
    BASE_BRANCH    = var.base_branch
    CONFIG_PATH    = var.config_path
    PREVIEW_ZONE   = var.preview_zone
    LIFETIME_HOURS = tostring(var.lifetime_hours)
    TEMPLATE_REF   = var.template_ref
    LABEL          = var.label
  }
  env_content    = join("\n", [for k, v in local.env_map : "${k}=${v}"])
  config_content = join("\n", [for k, v in local.config_map : "${k}=${v}"])

  preview_host = (
    var.preview_zone == ""
    ? "pr-${var.issue_number}-${replace(digitalocean_droplet.this.ipv4_address, ".", "-")}.nip.io"
    : "pr-${var.issue_number}.${var.preview_zone}"
  )
}

resource "tls_private_key" "ssh" {
  algorithm = "ED25519"
}

resource "digitalocean_ssh_key" "this" {
  name       = local.name
  public_key = tls_private_key.ssh.public_key_openssh
}

resource "digitalocean_droplet" "this" {
  name       = local.name
  image      = var.droplet_image
  region     = var.droplet_region
  size       = var.droplet_size
  monitoring = true
  ipv6       = false
  ssh_keys   = [digitalocean_ssh_key.this.id]
  tags       = ["blacksmith", "repo:${local.repo_slug}", "issue:${var.issue_number}"]

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    env_content    = local.env_content
    config_content = local.config_content
    template_repo  = var.template_repo
    template_ref   = var.template_ref
  })
}

resource "digitalocean_firewall" "this" {
  name        = local.name
  droplet_ids = [digitalocean_droplet.this.id]

  dynamic "inbound_rule" {
    for_each = ["22", "80", "443"]
    content {
      protocol         = "tcp"
      port_range       = inbound_rule.value
      source_addresses = ["0.0.0.0/0", "::/0"]
    }
  }

  dynamic "outbound_rule" {
    for_each = ["tcp", "udp"]
    content {
      protocol              = outbound_rule.value
      port_range            = "all"
      destination_addresses = ["0.0.0.0/0", "::/0"]
    }
  }

  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}

resource "digitalocean_record" "preview" {
  count  = var.preview_zone == "" ? 0 : 1
  domain = var.preview_zone
  type   = "A"
  name   = "pr-${var.issue_number}"
  value  = digitalocean_droplet.this.ipv4_address
  ttl    = 60
}
