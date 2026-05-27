# Services package — lazy imports keep startup fast; consumers import directly.
# Listing submodules here for discoverability; no side-effects on import.
from app.services import (  # noqa: F401
    brain_ingest,
    relevance_filter,
    team_context_cache,
)
