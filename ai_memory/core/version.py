"""The version, in one place.

pyproject reads it from here (`dynamic = ["version"]`), the MCP server reports it in
`serverInfo`, and `ai-memory --version` prints it. Before 1.0.0 the server sent an empty
version string to every client that connected, which is a thing a client cannot work
around: it cannot tell an old server from a new one, so it cannot warn about either.
"""
__version__ = "1.2.1"
