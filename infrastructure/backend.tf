##############################################################################
# Remote state
#
# apply and destroy run on a GitHub runner, which starts empty every time. A
# local state file would mean every run began by believing nothing exists.
#
# The bucket is created by the bootstrap root next door and named there too:
# a backend block is read before variables exist, so the name is a literal in
# both places and nothing checks that the two agree.
##############################################################################

terraform {
  backend "s3" {
    bucket = "todo-agent-tfstate-791762"
    key    = "todo-agent/terraform.tfstate"
    region = "us-east-1"

    encrypt = true

    # Native S3 locking, Terraform 1.10 and later. The separate DynamoDB lock
    # table this replaced is deprecated, and it was a whole extra resource
    # whose only job was to hold one row.
    use_lockfile = true
  }
}
