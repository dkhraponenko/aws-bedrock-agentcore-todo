"""Developer tooling.

A package only so `tests/e2e` can import the chat client rather than
reimplementing it: without this, mypy sees scripts/chat.py under two module
names at once and refuses to check anything.
"""
