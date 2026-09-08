#!/usr/bin/env bash
# Stage the AgentCore Runtime artifact: the packages the loop is made of, the
# launcher AgentCore executes, and the dependencies the platform does not have.
#
# Deliberately outside terraform. A data source that shelled out would run on
# every plan as well, and an ephemeral runner has nothing staged for terraform
# to read unless something builds it first — so the build is a step, and it is
# named in both .github/workflows/deploy.yml and the README.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
stage="$root/infrastructure/build/runtime"
requirements="$root/infrastructure/runtime_requirements.txt"
python=${PYTHON:-python3}

rm -rf "$stage"
mkdir -p "$stage"

# --no-compile: bytecode compiled here is compiled for this machine's
# interpreter, and it would also change the archive's hash on every build.
"$python" -m pip install --quiet --no-compile --disable-pip-version-check \
  --only-binary=:all: --target "$stage" --requirement "$requirements"

cp -R "$root/src/todo_runtime" "$root/src/todo_logging" "$stage/"
cp "$root/src/main.py" "$stage/"

find "$stage" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$stage" -name '*.pyc' -delete

# Console scripts pip generates for the dependencies. Nothing executes them
# here, and they are the one part of the tree that names this machine's paths.
rm -rf "$stage/bin"

# The assumption the pins rest on: nothing here is compiled, so nothing here is
# built for the wrong architecture. AgentCore runs arm64 Linux, and a wheel with
# an extension module built on this machine would fail there rather than here.
if [ -n "$(find "$stage" \( -name '*.so' -o -name '*.pyd' \) -print -quit)" ]; then
  echo "a dependency ships a compiled extension; it has to be built for arm64 Linux" >&2
  exit 1
fi

echo "staged $(du -sh "$stage" | cut -f1) in $stage"
