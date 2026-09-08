#!/usr/bin/env bash
##############################################################################
# The state bucket is named in two files and nothing else notices when the two
# drift apart.
#
# A backend block is parsed before variables exist, so infrastructure/backend.tf
# cannot reference the bootstrap variable that also carries the name - it has to
# be a literal in both places. Out of step, terraform init either fails on a
# bucket that does not exist or, worse, silently starts a second state file.
##############################################################################
set -euo pipefail

backend_file=infrastructure/backend.tf
bootstrap_file=infrastructure/bootstrap/variables.tf

backend=$(sed -n 's/.*bucket *= *"\([^"]*\)".*/\1/p' "$backend_file")
bootstrap=$(sed -n '/variable "state_bucket_name"/,/^}/s/.*default *= *"\([^"]*\)".*/\1/p' "$bootstrap_file")

if [ -z "$backend" ] || [ -z "$bootstrap" ]; then
  echo "could not read a bucket name from $backend_file or $bootstrap_file" >&2
  exit 1
fi

if [ "$backend" != "$bootstrap" ]; then
  echo "the state bucket is named twice and the two disagree:" >&2
  echo "  $backend_file -> $backend" >&2
  echo "  $bootstrap_file -> $bootstrap" >&2
  exit 1
fi
