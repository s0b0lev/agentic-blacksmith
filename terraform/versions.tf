terraform {
  required_version = ">= 1.9"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.40"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Partial configuration: pass the rest with `terraform init -backend-config=backend.hcl`
  # (see backend.hcl.example). State lives in DigitalOcean Spaces, one workspace per issue.
  backend "s3" {}
}

provider "digitalocean" {
  token = var.do_token
}
