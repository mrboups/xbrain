"""Shared pytest fixtures for mcp-brain.

app.config constructs a module-level `settings = Settings()` at import, and the OAuth identity
URLs fail-fast when empty (Phase 14, D-03). Supply neutral test values so the suite collects.
"""

import os

os.environ.setdefault("OAUTH_ISSUER_URL", "https://api.test.example")
os.environ.setdefault("OAUTH_RESOURCE_URL", "https://mcp.test.example/mcp")
