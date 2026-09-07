##############################################################################
# Bootstrap: the three things CI cannot create for itself
#
# The deploy role, the identity provider behind it and the bucket holding the
# main stack's state are all prerequisites of the run that would create them.
# They live in their own root, applied once from a laptop, so that
# `terraform destroy` on the main stack can never take out the credentials or
# the state store that CI runs on.
#
# This root's own state is a local file. It is applied approximately never, and
# losing the file costs four `terraform import` calls rather than the resources.
##############################################################################

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.51"
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
