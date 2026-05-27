# Services package — submodules are imported directly by their consumers.
# Do NOT import submodules here to avoid circular dependencies at startup.
# (brain_ingest imports app.deps which imports app.auth which previously
#  imported this __init__.py before it finished loading — classic cycle.)
