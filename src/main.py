"""Zip-root launcher, and the reason this file is not inside the package.

`entry_point` is a command AgentCore Runtime runs, not an import path: it
executes the last element as a script against the root of the unpacked archive.
A path into a package would put that package's own directory first on
`sys.path` instead of the archive root, so the imports below would not resolve.
"""

from __future__ import annotations

from todo_runtime.server import serve


if __name__ == "__main__":
    serve()
