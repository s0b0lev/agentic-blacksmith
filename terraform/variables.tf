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

variable "telegram_bot_token" {
  description = "Telegram bot token for stage notifications (TELEGRAM_BOT_TOKEN on the droplet); empty disables them."
  type        = string
  sensitive   = true
  default     = ""
}

variable "telegram_chat_id" {
  description = "Telegram chat id that receives stage notifications (TELEGRAM_CHAT_ID on the droplet)."
  type        = string
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

variable "grafana_cloud_token" {
  description = "Grafana Cloud access policy token for pushing logs and metrics (GRAFANA_CLOUD_TOKEN on the droplet)."
  type        = string
  sensitive   = true
  default     = ""
}

variable "observability_enabled" {
  description = "Ship droplet logs and metrics to Grafana Cloud (OBSERVABILITY_ENABLED on the droplet)."
  type        = bool
  default     = false
}

variable "loki_url" {
  description = "Grafana Cloud Loki push URL (LOKI_URL on the droplet)."
  type        = string
  default     = ""
}

variable "loki_user" {
  description = "Grafana Cloud Loki user id (LOKI_USER on the droplet)."
  type        = string
  default     = ""
}

variable "prometheus_url" {
  description = "Grafana Cloud Prometheus remote-write URL (PROMETHEUS_URL on the droplet)."
  type        = string
  default     = ""
}

variable "prometheus_user" {
  description = "Grafana Cloud Prometheus user id (PROMETHEUS_USER on the droplet)."
  type        = string
  default     = ""
}

variable "agent_stream" {
  description = "Stream the agent session output as it runs (AGENT_STREAM on the droplet)."
  type        = bool
  default     = true
}

variable "branch" {
  description = "Branch the agent works on (BRANCH on the droplet)."
  type        = string
  default     = ""
}

variable "dashboard_url" {
  description = "Grafana dashboard URL for this run (DASHBOARD_URL on the droplet)."
  type        = string
  default     = ""
}
