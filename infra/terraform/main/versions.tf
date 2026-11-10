terraform {
  required_version = ">= 1.10.0" # S3 native state locking (use_lockfile)
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Partial configuration: bucket comes from backend.hcl (gitignored), see backend.hcl.example.
  backend "s3" {
    key          = "main/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      project    = "jobpulse"
      managed_by = "terraform"
      stack      = "main"
    }
  }
}
