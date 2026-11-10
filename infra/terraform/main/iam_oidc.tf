# GitHub Actions -> AWS via OIDC. No access keys exist anywhere.
# Trust is pinned to the `prod` environment of this repo; the GitHub `prod` environment is
# restricted to the `main` branch by a deployment-branch rule (configured in GitHub, not here).

locals {
  oidc_host         = "token.actions.githubusercontent.com"
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1
  url   = "https://${local.oidc_host}"
}

data "aws_iam_policy_document" "github_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["repo:${var.github_repo}:environment:${var.github_environment}"]
    }
  }
}

resource "aws_iam_role" "github_ingest" {
  name                 = "jobpulse-github-ingest"
  description          = "Assumed by GitHub Actions (${var.github_repo}, environment ${var.github_environment})"
  assume_role_policy   = data.aws_iam_policy_document.github_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "ingest_s3" {
  statement {
    sid     = "ReadWriteDataPrefixes"
    actions = ["s3:PutObject", "s3:GetObject"]
    resources = [
      "${aws_s3_bucket.data.arn}/raw/*",
      "${aws_s3_bucket.data.arn}/snapshots/*",
      "${aws_s3_bucket.data.arn}/ops/*",
    ]
  }
  statement {
    sid       = "ListBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }
}

resource "aws_iam_role_policy" "github_ingest_s3" {
  name   = "jobpulse-ingest-s3"
  role   = aws_iam_role.github_ingest.id
  policy = data.aws_iam_policy_document.ingest_s3.json
}
