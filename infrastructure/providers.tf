terraform {
  # 1.10 is the floor for use_lockfile in backend.tf.
  required_version = ">= 1.10"

  required_providers {
    # 6.x is the floor for the aws_bedrockagentcore_* resources.
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.51"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.6"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}
