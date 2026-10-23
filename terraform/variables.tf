variable "do_token" {
  description = "DigitalOcean API token used to create the droplet, firewall, SSH key and DNS record."
  type        = string
  sensitive   = true
}

variable "repo" {
  description = "Target GitHub repository as owner/name."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.repo))
    error_message = "repo must be owner/name using only letters, digits, '_', '.' and '-'."
  }
}

variable "issue_number" {
  description = "GitHub issue number this droplet works on."
  type        = number

  validation {
    condition     = var.issue_number > 0 && floor(var.issue_number) == var.issue_number
    error_message = "issue_number must be a positive whole number."
  }
}

variable "base_branch" {
  description = "Branch the work branch and pull request are based on."
  type        = string
  default     = "main"
}

variable "label" {
  description = "Issue label that triggers blacksmith."
  type        = string
  default     = "blacksmith"
}

variable "preview_zone" {
  description = "DigitalOcean-managed DNS zone for preview hosts (pr-N.<zone>); empty uses nip.io."
  type        = string
  default     = ""
}

variable "config_path" {
  description = "Path of the blacksmith config file inside the target repository."
  type        = string
  default     = "blacksmith.yml"
}

variable "template_ref" {
  description = "Tag, branch or commit of the template repository the droplet downloads."
  type        = string
}

variable "template_repo" {
  description = "GitHub owner/name of the template repository (point forks at themselves)."
  type        = string
  default     = "s0b0lev/agentic-blacksmith"
}

variable "droplet_size" {
  description = "DigitalOcean droplet size slug."
  type        = string
  default     = "s-4vcpu-8gb"
}

variable "droplet_region" {
  description = "DigitalOcean region slug."
  type        = string
  default     = "fra1"
}

variable "droplet_image" {
  description = "DigitalOcean image slug; bootstrap.sh expects Ubuntu."
  type        = string
  default     = "ubuntu-24-04-x64"
}

variable "claude_oauth_token" {
  description = "Claude Code OAuth token (CLAUDE_CODE_OAUTH_TOKEN on the droplet)."
  type        = string
  sensitive   = true
}

variable "github_token" {
  description = "GitHub token the agent uses to clone, push and comment (BLACKSMITH_GITHUB_TOKEN)."
  type        = string
  sensitive   = true
}

variable "figma_api_key" {
  description = "Figma API key for design-driven issues; empty disables Figma."
  type        = string
  sensitive   = true
  default     = ""
}

variable "lifetime_hours" {
  description = "Hours after which the droplet stops working on the issue."
  type        = number
  default     = 168

  validation {
    condition     = var.lifetime_hours >= 1 && floor(var.lifetime_hours) == var.lifetime_hours
    error_message = "lifetime_hours must be a whole number of at least 1."
  }
}
