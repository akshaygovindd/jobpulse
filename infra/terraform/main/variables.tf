variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "github_repo" {
  description = "owner/repo allowed to assume the ingest role."
  type        = string
  default     = "akshaygovindd/jobpulse"
}

variable "github_environment" {
  description = "GitHub environment whose OIDC subject may assume the role."
  type        = string
  default     = "prod"
}

variable "create_oidc_provider" {
  description = "Set false if the account already has the token.actions.githubusercontent.com provider."
  type        = bool
  default     = true
}

variable "budget_limit_usd" {
  description = "Monthly cost budget. This is an ALERT threshold, not a spending cap."
  type        = number
  default     = 10
}

variable "budget_alert_email" {
  description = "Email that receives AWS Budgets alerts. Set in terraform.tfvars (gitignored)."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_alert_email))
    error_message = "budget_alert_email must be an email address."
  }
}
