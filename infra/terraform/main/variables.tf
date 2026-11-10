variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "github_repo" {
  description = "owner/repo, used in the role description."
  type        = string
  default     = "akshaygovindd/jobpulse"
}

variable "github_environment" {
  description = "GitHub environment that runs the ingest job, used in the role description."
  type        = string
  default     = "prod"
}

# This repo uses GitHub's immutable OIDC subject format, which embeds the owner and repo
# numeric IDs: repo:<owner>@<owner_id>/<repo>@<repo_id>:environment:<env>. The plain
# `repo:akshaygovindd/jobpulse:environment:prod` form never matches the real token (the first
# ingest run failed with AssumeRoleWithWebIdentity denied). The value below was confirmed from a
# decoded token on 2026-11-10 and via GET /repos/akshaygovindd/jobpulse/actions/oidc/customization/sub
# (use_default=true, use_immutable_subject=true). The IDs survive renames, so a renamed or
# re-created repo with the same name cannot assume this role.
variable "github_oidc_subject" {
  description = "Exact OIDC `sub` claim allowed to assume the ingest role (StringEquals)."
  type        = string
  default     = "repo:akshaygovindd@97725846/jobpulse@1451951419:environment:prod"
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
