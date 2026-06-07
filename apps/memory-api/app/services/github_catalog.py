"""GitHub repo-catalog service — one searchable brain item per repo.

Enumerates each team's reachable GitHub repos via the App installation token,
generates a Claude Haiku summary of the README (fail-soft to description), and
upserts each repo as a ``MemoryItem`` with the full 7-field tagging contract.

Storage is BRAIN ONLY — ``provider.upsert(MemoryItem)``, deterministic uuid5 id,
no new table, no new migration.

Public API
----------
build_catalog_item_id(team_scope, full_name) -> str
build_catalog_content(full_name, summary, language, topics, visibility) -> str
build_catalog_item(repo_obj, team_scope, *, summary, readme_summarized, ...) -> MemoryItem
summarize_readme(readme_text, repo_full_name, team_scope) -> str | None
fetch_readme(session, owner, name, installation_id_or_token) -> str | None
enumerate_install_repos(installation_id) -> list[dict]
upsert_catalog_item(session, provider, repo_obj, team_scope, ...) -> None
soft_delete_catalog_item(session, provider, team_scope, full_name) -> None
index_team_catalog(team_scope, github_org, *, session_factory) -> dict
index_orgs_catalog(*, org_logins, user_id, session_factory) -> None
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from app.config import settings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Fixed namespace — NEVER change (production idempotency key).
# A fresh UUID distinct from GITHUB_SYNC_NS so catalog IDs don't collide with
# per-file sync chunks.  Changing this would invalidate all existing catalog
# item idempotency keys and cause re-indexing to produce duplicates.
# ---------------------------------------------------------------------------
GITHUB_CATALOG_NS = uuid.UUID("c1a7e3f9-4b82-5d0e-a2c6-8f1d3b9e7a04")

CATALOG_SOURCE = "github:repo-catalog"

# System prompt for Haiku README summarization.
# Prompt-caching is a nice-to-have for a one-time backfill; we include the
# cache_control block as per relevance_filter.py but rely on Anthropic's daily
# ephemeral cache (activated when ≥ 4096 tokens); this prompt is short by
# design (repo summaries, not classification).
SUMMARY_SYSTEM_PROMPT = (
    "You summarize GitHub repository README files in 1-3 sentences. "
    "State what the repository does, its primary purpose, and any key "
    "technologies it uses. Be factual and concise — no preamble, no commentary."
)

# GitHub API headers — mirror github_installation._GH_HEADERS
_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Lazy Anthropic client (mirrors relevance_filter._get_client pattern)
_catalog_client: Any | None = None


def _get_client() -> Any | None:
    """Lazy-init the Anthropic async client for README summarization.

    Returns None when GITHUB_CATALOG_ENABLED is False, RELEVANCE_HAIKU_ENABLED
    is False, or ANTHROPIC_API_KEY is absent — all cases are treated as
    'summarization disabled, fall back to description'.
    """
    global _catalog_client
    if _catalog_client is not None:
        return _catalog_client
    if (
        not settings.GITHUB_CATALOG_ENABLED
        or not settings.ANTHROPIC_API_KEY
        or not settings.RELEVANCE_HAIKU_ENABLED
    ):
        return None
    try:
        from anthropic import AsyncAnthropic  # noqa: PLC0415
    except ImportError:
        log.warning("github_catalog.anthropic_not_installed")
        return None
    _catalog_client = AsyncAnthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        timeout=settings.RELEVANCE_HAIKU_TIMEOUT_S,
    )
    return _catalog_client


# ---------------------------------------------------------------------------
# Pure helpers (no I/O — unit-testable)
# ---------------------------------------------------------------------------


def build_catalog_item_id(team_scope: str, full_name: str) -> str:
    """Return a deterministic uuid5 string keyed by (team_scope, full_name).

    Same inputs → same id. Different team_scope → different id (critical for
    cross-team isolation when the same repo is indexed into multiple teams).
    """
    return str(uuid.uuid5(GITHUB_CATALOG_NS, f"repo-catalog:{team_scope}:{full_name}"))


def build_catalog_content(
    full_name: str,
    summary: str,
    language: str | None,
    topics: list[str] | None,
    visibility: str | None,
) -> str:
    """Build the human-readable, Qdrant-embedded content string.

    Locked shape:
        "{full_name} — {summary}\\nLanguage: {lang}. Topics: {topics}. Visibility: {vis}."
    """
    lang_str = language or "unknown"
    topics_str = ", ".join(topics) if topics else "none"
    vis_str = visibility or "unknown"
    return (
        f"{full_name} — {summary}\n"
        f"Language: {lang_str}. Topics: {topics_str}. Visibility: {vis_str}."
    )


def build_catalog_item(
    repo_obj: dict,
    team_scope: str,
    *,
    summary: str,
    readme_summarized: bool,
    installation_id: int | None = None,
) -> Any:
    """Build a fully-tagged MemoryItem for one repo catalog entry.

    Reads keys from repo_obj: full_name, name, description, default_branch,
    language, visibility, html_url, topics.

    project_scope is the SHORT repo name (name, not full_name) clamped to 64 chars
    so it fits the VARCHAR(64) column — full_name is preserved in metadata.
    """
    from xbrain_memory.types import (  # noqa: PLC0415
        MemoryItem,
        TruthLevel,
        ValidationStatus,
        Visibility,
    )

    full_name: str = repo_obj.get("full_name", "")
    short_name: str = repo_obj.get("name", full_name.split("/")[-1] if "/" in full_name else full_name)
    language: str | None = repo_obj.get("language")
    topics: list[str] = repo_obj.get("topics") or []
    vis: str | None = repo_obj.get("visibility")

    item_id = build_catalog_item_id(team_scope, full_name)
    content = build_catalog_content(full_name, summary, language, topics, vis)

    now = datetime.now(timezone.utc)
    metadata: dict = {
        "full_name": full_name,
        "html_url": repo_obj.get("html_url", ""),
        "default_branch": repo_obj.get("default_branch", ""),
        "primary_language": language,
        "topics": topics,
        "visibility": vis,
        "readme_summarized": readme_summarized,
    }
    if installation_id is not None:
        metadata["installation_id"] = installation_id

    return MemoryItem(
        id=item_id,
        team_scope=team_scope,
        project_scope=short_name[:64],
        content=content,
        metadata=metadata,
        visibility=Visibility.TEAM,
        truth_level=TruthLevel.WORKING,
        confidence=0.6,
        source=CATALOG_SOURCE,
        validation_status=ValidationStatus.PENDING,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Haiku README summarization (fail-soft — never raises to caller)
# ---------------------------------------------------------------------------


async def summarize_readme(
    readme_text: str,
    repo_full_name: str,
    team_scope: str,
) -> str | None:
    """Return a 1-3 sentence Haiku summary of *readme_text*, or None on any failure.

    Failure cases (all return None):
    - GITHUB_CATALOG_ENABLED=False / RELEVANCE_HAIKU_ENABLED=False / no API key
    - Daily budget exhausted for this team
    - Timeout or any Anthropic exception
    - Empty response

    Input is capped at GITHUB_CATALOG_README_CHARS (default 8000) before calling.
    """
    # Import budget helpers from relevance_filter to share the daily token cap.
    from app.services.relevance_filter import _check_budget, _record_tokens  # noqa: PLC0415

    client = _get_client()
    if client is None:
        return None

    # Budget check — estimate ~400 tokens per call (README input + system prompt)
    if not _check_budget(team_scope, 400):
        log.info(
            "github_catalog.summarize_readme.budget_exceeded",
            team_scope=team_scope,
            repo=repo_full_name,
        )
        return None

    capped_text = readme_text[: settings.GITHUB_CATALOG_README_CHARS]

    try:
        msg = await client.messages.create(
            model=settings.RELEVANCE_HAIKU_MODEL,
            max_tokens=200,
            system=[
                {
                    "type": "text",
                    "text": SUMMARY_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": capped_text}],
        )
        usage = getattr(msg, "usage", None)
        tokens_in = getattr(usage, "input_tokens", 400) if usage else 400
        _record_tokens(team_scope, tokens_in)

        summary = msg.content[0].text.strip() if msg.content else None
        return summary or None

    except Exception as exc:  # noqa: BLE001 — fail-soft, never abort backfill
        log.warning(
            "github_catalog.summarize_readme.failed",
            repo=repo_full_name,
            team_scope=team_scope,
            error=str(exc),
        )
        return None


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


async def fetch_readme(
    session: Any,
    owner: str,
    name: str,
    installation_id: int,
) -> str | None:
    """Fetch the README for owner/name via the dedicated GitHub README endpoint.

    Uses GET /repos/{owner}/{name}/readme (case-insensitive, returns the
    repo's default README regardless of filename case).

    Returns the decoded text (base64 → utf-8 / latin-1 fallback), capped at
    GITHUB_CATALOG_README_CHARS, or None on 404 / 403 / any error (fail-soft).
    """
    from app.services.github_installation import get_installation_token  # noqa: PLC0415

    try:
        token = await get_installation_token(installation_id)
    except Exception as exc:
        log.warning(
            "github_catalog.fetch_readme.token_error",
            owner=owner,
            name=name,
            error=str(exc),
        )
        return None

    url = f"https://api.github.com/repos/{owner}/{name}/readme"
    headers = {"Authorization": f"Bearer {token}", **_GH_HEADERS}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=headers)

        if r.status_code == 404:
            return None
        if r.status_code == 403:
            log.info(
                "github_catalog.fetch_readme.forbidden",
                owner=owner,
                name=name,
                status=r.status_code,
            )
            return None
        if r.status_code != 200:
            log.warning(
                "github_catalog.fetch_readme.unexpected_status",
                owner=owner,
                name=name,
                status=r.status_code,
            )
            return None

        body = r.json()
        encoded = body.get("content", "")
        # GitHub embeds newlines inside the base64 string — strip them before decode.
        encoded_clean = encoded.replace("\n", "")
        raw_bytes = base64.b64decode(encoded_clean)
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = raw_bytes.decode("latin-1")

        return text[: settings.GITHUB_CATALOG_README_CHARS]

    except Exception as exc:  # noqa: BLE001 — fail-soft
        log.warning(
            "github_catalog.fetch_readme.error",
            owner=owner,
            name=name,
            error=str(exc),
        )
        return None


async def enumerate_install_repos(installation_id: int) -> list[dict]:
    """Return all repos accessible to the given GitHub App installation.

    Paginates GET /installation/repositories (per_page=100) until exhausted.
    Uses the installation access token (ghs_...) from the cache.
    """
    from app.services.github_installation import get_installation_token  # noqa: PLC0415

    token = await get_installation_token(installation_id)
    repos: list[dict] = []
    page = 1

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            url = (
                f"https://api.github.com/installation/repositories"
                f"?per_page=100&page={page}"
            )
            headers = {"Authorization": f"Bearer {token}", **_GH_HEADERS}
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            body = r.json()
            page_repos = body.get("repositories") or []
            repos.extend(page_repos)
            if len(page_repos) < 100:
                break
            page += 1

    log.info(
        "github_catalog.enumerate_install_repos.done",
        installation_id=installation_id,
        count=len(repos),
    )
    return repos


async def upsert_catalog_item(
    session: Any,
    provider: Any,
    repo_obj: dict,
    team_scope: str,
    *,
    installation_id: int | None = None,
) -> None:
    """Upsert one repo as a catalog memory_item.

    Flow:
    1. Split full_name → owner, name.
    2. fetch_readme (fail-soft → None).
    3. summarize_readme if README available (fail-soft → None).
    4. Effective summary = Haiku summary OR description OR stub.
    5. build_catalog_item → provider.upsert.
    """
    full_name: str = repo_obj.get("full_name", "")
    parts = full_name.split("/", 1)
    owner = parts[0] if len(parts) == 2 else ""
    name = parts[1] if len(parts) == 2 else full_name

    readme_text: str | None = None
    if owner and name and installation_id is not None:
        readme_text = await fetch_readme(session, owner, name, installation_id)

    haiku_summary: str | None = None
    if readme_text:
        haiku_summary = await summarize_readme(readme_text, full_name, team_scope)

    description = repo_obj.get("description") or ""
    effective_summary = (
        haiku_summary
        or description
        or f"{full_name} (no description available)"
    )
    readme_summarized = haiku_summary is not None

    item = build_catalog_item(
        repo_obj,
        team_scope,
        summary=effective_summary,
        readme_summarized=readme_summarized,
        installation_id=installation_id,
    )
    await provider.upsert(item)
    log.debug(
        "github_catalog.upsert_catalog_item.ok",
        repo=full_name,
        team_scope=team_scope,
        summarized=readme_summarized,
    )


async def soft_delete_catalog_item(
    session: Any,
    provider: Any,
    team_scope: str,
    full_name: str,
) -> None:
    """Soft-delete the catalog item for *full_name* in *team_scope*.

    Two-phase:
    1. UPDATE memory_items SET deleted_at = now() WHERE id=$1 AND team_scope=$2
       (PostgreSQL side — hides item from the catalog SELECT).
    2. provider.mark_deleted(item_id, now) — flips Qdrant payload deleted_at_ts
       (vector search side).

    Idempotent: if the item does not exist, both operations are no-ops.
    """
    item_id = build_catalog_item_id(team_scope, full_name)
    now = datetime.now(timezone.utc)

    # PG side — update deleted_at so GET /catalog's 'deleted_at IS NULL' filter hides it.
    pool = await provider._ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE memory_items SET deleted_at = $1 WHERE id = $2::uuid AND team_scope = $3",
            now,
            item_id,
            team_scope,
        )

    # Qdrant side — flip the vector payload.
    try:
        await provider.mark_deleted(item_id, now)
    except Exception as exc:  # noqa: BLE001 — Qdrant is fail-soft
        log.warning(
            "github_catalog.soft_delete.qdrant_error",
            item_id=item_id,
            team_scope=team_scope,
            full_name=full_name,
            error=str(exc),
        )

    log.info(
        "github_catalog.soft_delete.ok",
        item_id=item_id,
        team_scope=team_scope,
        full_name=full_name,
    )


# ---------------------------------------------------------------------------
# Orchestration (bounded background — callers pass session_factory)
# ---------------------------------------------------------------------------


async def index_team_catalog(
    team_scope: str,
    github_org: str,
    *,
    session_factory: Any,
) -> dict:
    """Enumerate all repos for a team's GitHub org and upsert each as a catalog item.

    Concurrency is bounded by ``asyncio.Semaphore(settings.GITHUB_CATALOG_CONCURRENCY)``
    so ~74 Haiku calls don't saturate the event loop or API rate limits.

    Returns a summary dict: {team_scope, indexed, skipped}.
    Skips entirely if GITHUB_CATALOG_ENABLED=False.
    """
    if not settings.GITHUB_CATALOG_ENABLED:
        log.info("github_catalog.index_team_catalog.disabled", team_scope=team_scope)
        return {"team_scope": team_scope, "indexed": 0, "skipped": 0}

    from app.deps import get_memory_provider  # noqa: PLC0415
    from app.services.github_installation import (  # noqa: PLC0415
        find_installation_for_org,
        find_user_installation,
    )

    indexed = 0
    skipped = 0

    async with session_factory() as session:
        installation_id = await find_installation_for_org(session, github_org)

    # Fallback: ``github_org`` may be a USER account (personal repos), which
    # find_installation_for_org (orgs-only, /orgs/{org}/installation) can't
    # resolve. Try the user-account install endpoint.
    if installation_id is None:
        try:
            installation_id = await find_user_installation(github_org)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "github_catalog.index_team_catalog.user_install_lookup_failed",
                github_org=github_org,
                error=str(exc),
            )

    if installation_id is None:
        log.info(
            "github_catalog.index_team_catalog.no_installation",
            team_scope=team_scope,
            github_org=github_org,
        )
        return {"team_scope": team_scope, "indexed": 0, "skipped": 0}

    try:
        repos = await enumerate_install_repos(installation_id)
    except Exception as exc:
        log.warning(
            "github_catalog.index_team_catalog.enumerate_failed",
            team_scope=team_scope,
            github_org=github_org,
            error=str(exc),
        )
        return {"team_scope": team_scope, "indexed": 0, "skipped": 0}

    sem = asyncio.Semaphore(settings.GITHUB_CATALOG_CONCURRENCY)

    async def _upsert_one(repo_obj: dict) -> bool:
        nonlocal indexed, skipped
        async with sem:
            try:
                async with session_factory() as bg_session:
                    provider = get_memory_provider()
                    await upsert_catalog_item(
                        bg_session,
                        provider,
                        repo_obj,
                        team_scope,
                        installation_id=installation_id,
                    )
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "github_catalog.index_team_catalog.upsert_error",
                    repo=repo_obj.get("full_name", "?"),
                    team_scope=team_scope,
                    error=str(exc),
                )
                return False

    tasks = [asyncio.create_task(_upsert_one(r)) for r in repos]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    indexed = sum(1 for ok in results if ok)
    skipped = len(results) - indexed

    summary = {"team_scope": team_scope, "indexed": indexed, "skipped": skipped}
    log.info("github_catalog.index_team_catalog.done", **summary)
    return summary


async def index_orgs_catalog(
    *,
    org_logins: list[str],
    user_id: Any,
    session_factory: Any,
    user_login: str | None = None,
) -> None:
    """Sign-in backfill entrypoint — index all orgs (and the user's personal repos).

    For each org in *org_logins*, resolves the team(s) that have ``github_org``
    matching that org (via ``teams_repo.get_teams_with_github_org``), and calls
    ``index_team_catalog`` for each matched team.

    When *user_login* is given (the signing-in user's GitHub login), ALSO indexes
    that user's PERSONAL-account repos into every team the user belongs to —
    index_team_catalog falls back to the user-account install for a non-org login.
    This is what makes a member's personal repos appear in their team catalog
    (org installs alone don't cover them).

    Skips orgs with no matching installation. Owns its sessions (never raises
    to the caller — this is a fire-and-forget background task).
    """
    if not settings.GITHUB_CATALOG_ENABLED:
        return

    from app.repos import teams as teams_repo  # noqa: PLC0415

    try:
        async with session_factory() as session:
            org_teams = await teams_repo.get_teams_with_github_org(session)
        # Map org_login -> list of team slugs
        org_to_teams: dict[str, list[str]] = {}
        for t in org_teams:
            if t.github_org:
                org_to_teams.setdefault(t.github_org, []).append(t.slug)
    except Exception as exc:
        log.warning(
            "github_catalog.index_orgs_catalog.teams_lookup_failed",
            user_id=str(user_id),
            error=str(exc),
        )
        return

    for org in org_logins:
        teams_for_org = org_to_teams.get(org)
        if not teams_for_org:
            log.debug(
                "github_catalog.index_orgs_catalog.no_team_for_org",
                org=org,
                user_id=str(user_id),
            )
            continue

        for team_scope in teams_for_org:
            try:
                await index_team_catalog(
                    team_scope,
                    org,
                    session_factory=session_factory,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "github_catalog.index_orgs_catalog.team_failed",
                    org=org,
                    team_scope=team_scope,
                    error=str(exc),
                )

    # Personal-account repos (User install): index the signing-in user's own
    # repos into every team the user belongs to. index_team_catalog falls back
    # to the user-account install (/users/{login}/installation) for a non-org
    # login. org_logins deliberately excludes the personal account, so this is
    # the only path that covers a member's personal repos on sign-in.
    if user_login:
        try:
            from app.repos.teams import list_teams_for_user  # noqa: PLC0415

            async with session_factory() as session:
                user_teams = await list_teams_for_user(session, user_id=user_id)
            for team in user_teams:
                try:
                    await index_team_catalog(
                        team.slug,
                        user_login,
                        session_factory=session_factory,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "github_catalog.index_orgs_catalog.personal_team_failed",
                        user_login=user_login,
                        team_scope=team.slug,
                        error=str(exc),
                    )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "github_catalog.index_orgs_catalog.personal_failed",
                user_login=user_login,
                error=str(exc),
            )
