output "data_bucket" {
  description = "Set as GitHub prod environment variable JOBPULSE_BUCKET."
  value       = aws_s3_bucket.data.bucket
}

output "github_ingest_role_arn" {
  description = "Set as GitHub prod environment variable AWS_ROLE_ARN."
  value       = aws_iam_role.github_ingest.arn
}

output "budget_name" {
  value = aws_budgets_budget.monthly.name
}
