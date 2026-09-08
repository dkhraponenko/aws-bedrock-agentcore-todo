##############################################################################
# Python artifacts
#
# src/ is a source root holding one package per deployable. Each zip carries
# exactly its own package: a single shared archive would redeploy the Lambda
# every time the agent loop changed, and the agent loop every time a tool did.
##############################################################################

locals {
  source_root  = "${path.module}/../src"
  source_files = fileset(local.source_root, "**")

  # The one package both artifacts carry: the JSON log formatter each entry
  # point installs at import. Duplicating it per deployable is the alternative,
  # and a second copy is a thing that drifts.
  shared_package = "todo_logging/"

  # archive_file takes literal paths, not globs, so the exclusion lists are
  # computed. Bytecode a local test run left behind has to stay out, or the zip
  # hash — and therefore the deployment — changes without the source changing.
  bytecode = [
    for file in local.source_files :
    file if length(regexall("(^|/)__pycache__/", file)) > 0 || endswith(file, ".pyc")
  ]

  lambda_excludes = toset(concat(
    local.bytecode,
    [
      for file in local.source_files : file
      if !startswith(file, "todo_agent/") && !startswith(file, local.shared_package)
    ],
  ))
}

# The Lambda's image ships boto3 and nothing here imports anything else beyond
# the standard library, so this artifact is still the source tree zipped as it
# stands.
data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = local.source_root
  output_path = "${path.module}/build/lambda.zip"
  excludes    = local.lambda_excludes
}

# The runtime's is not, because AgentCore's image has no boto3 in it — the first
# invoke found that out, with a ModuleNotFoundError that never reached port 8080.
# scripts/build_runtime.sh stages the two packages, the launcher AgentCore runs
# and the pinned dependencies into one directory, and that directory is what is
# zipped here. It has to have run before a plan reads this: the deploy workflow
# runs it as a step, and the README names it beside `terraform apply`.
data "archive_file" "runtime" {
  type        = "zip"
  source_dir  = "${path.module}/build/runtime"
  output_path = "${path.module}/build/runtime.zip"
}
