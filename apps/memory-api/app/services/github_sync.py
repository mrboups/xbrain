"""GitHub repo sync service — index a repo's text files into the team brain.

On-demand sync (no poller, no new container). Walks the repo via
``list_repo_files`` / ``read_repo_file`` (Phase 1, already deployed),
chunks each file into line-windows, and upserts each chunk as a
``MemoryItem`` via the direct ``provider.upsert()`` path — same pattern
as ``brain_ingest.py`` (uuid5 + direct-upsert, no agent-runtime HITL).

Public API
----------
sync_repo(session, provider, *, repo, team_scope, project_scope, ref) -> dict

Constants
---------
GITHUB_SYNC_NS  — fixed uuid5 namespace for deterministic item IDs (never change).
MAX_FILES       — hard cap on files indexed per call (default 200).
MAX_CHUNKS_TOTAL — hard cap on chunks upserted per call (default 2000).
CHUNK_LINES     — window size in lines (default 120).
MAX_FILE_BYTES  — skip files larger than this (100 KB — matches read_repo_file cap).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_memory_provider
from app.services.github_contents import (
    GithubAppNotInstalled,
    GithubPermissionDenied,
    list_repo_files,
    read_repo_file,
)
from xbrain_memory.types import (
    MemoryItem,
    TruthLevel,
    ValidationStatus,
    Visibility,
)

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Fixed namespace — never change (used for deterministic item IDs in production).
# Changing this would invalidate all existing idempotency-derived IDs.
# ---------------------------------------------------------------------------
GITHUB_SYNC_NS = uuid.UUID("3f7a4c10-9b2e-5d84-a1f6-7c3e8b0d2a95")

# ---------------------------------------------------------------------------
# Caps and tunables
# ---------------------------------------------------------------------------
MAX_FILES: int = 200
MAX_CHUNKS_TOTAL: int = 2000
CHUNK_LINES: int = 120
MAX_FILE_BYTES: int = 100_000  # matches read_repo_file's _CONTENT_SIZE_CAP

# ---------------------------------------------------------------------------
# Extension / name allowlist
# ---------------------------------------------------------------------------
ALLOWED_EXTS: frozenset[str] = frozenset(
    {
        ".md",
        ".txt",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".sh",
        ".sql",
        ".go",
        ".rs",
        ".java",
        ".rb",
        ".php",
        ".c",
        ".h",
        ".cpp",
        ".css",
        ".html",
        ".env.example",
        ".gitignore",
    }
)

# Extensionless filenames that are still text / doc
ALLOWED_NAMES: frozenset[str] = frozenset(
    {
        "README",
        "LICENSE",
        "LICENCE",
        "CLAUDE.md",  # project instructions
        "Dockerfile",
        "Makefile",
        "CHANGELOG",
        "CONTRIBUTING",
        "NOTICE",
        "AUTHORS",
        "CODEOWNERS",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _should_index(name: str, size: int | None) -> bool:
    """Return True if the file should be indexed based on its name and size.

    Args:
        name: Filename (basename only, no directory component).
        size: File size in bytes as reported by the GitHub listing API.
              None is treated as 0 (unknown — let read_repo_file handle it).

    Returns:
        True when the extension is in ALLOWED_EXTS or the full name is in
        ALLOWED_NAMES AND the size is within MAX_FILE_BYTES.
    """
    if size is not None and size > MAX_FILE_BYTES:
        return False

    # Check exact name match first (e.g. "Dockerfile", "README")
    if name in ALLOWED_NAMES:
        return True

    # Split on ALL dots to handle compound extensions (.env.example, .gitignore)
    lower = name.lower()
    # Build the suffix progressively from the first dot
    dot_pos = lower.find(".")
    while dot_pos != -1:
        if lower[dot_pos:] in ALLOWED_EXTS:
            return True
        dot_pos = lower.find(".", dot_pos + 1)

    return False


def _line_windows(text: str, window: int = CHUNK_LINES) -> list[str]:
    """Split *text* into non-overlapping windows of *window* lines each.

    Empty / whitespace-only windows are dropped.
    """
    lines = text.splitlines(keepends=True)
    chunks = []
    for start in range(0, len(lines), window):
        chunk = "".join(lines[start : start + window])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def _stale_chunk_ids(
    session: AsyncSession,
    *,
    team_scope: str,
    repo: str,
    file_path: str | None,
    keep_ids: set[str],
    seen_paths: set[str] | None = None,
) -> list[str]:
    """Ids this sync superseded: same repo, written by this sync, not just written.

    Two shapes, one query. With `file_path`, it answers "what is left over from
    the previous version of THIS file" — which covers an edited file (new sha,
    new ids) and a file that shrank to fewer chunks. With `seen_paths`, it
    answers "what belongs to a file this walk never saw", i.e. deleted or
    renamed in the repo.

    Scoped to `ingestion_origin = 'github-sync'` so a note a person wrote about
    the same repo is never in scope, and to `team_scope` so one team's sync
    cannot reach another team's rows.
    """
    where = [
        "team_scope = :ts",
        "metadata->>'ingestion_origin' = 'github-sync'",
        "metadata->>'repo' = :repo",
        "deleted_at IS NULL",
    ]
    params: dict[str, Any] = {"ts": team_scope, "repo": repo}
    if file_path is not None:
        where.append("metadata->>'file_path' = :fp")
        params["fp"] = file_path
    if seen_paths is not None:
        # An empty walk must not delete the whole repo — the caller guards that
        # case before calling, and this keeps the SQL honest if it ever slips.
        if not seen_paths:
            return []
        where.append("NOT (metadata->>'file_path' = ANY(:paths))")
        params["paths"] = list(seen_paths)
    rows = (
        await session.execute(
            sa.text(f"SELECT id FROM memory_items WHERE {' AND '.join(where)}"),  # noqa: S608 — fragments are literals above
            params,
        )
    ).scalars().all()
    return [str(r) for r in rows if str(r) not in keep_ids]


async def sync_repo(
    session: AsyncSession,
    provider: Any,
    *,
    repo: str,
    team_scope: str,
    project_scope: str | None = None,
    ref: str = "HEAD",
) -> dict:
    """Walk *repo* and upsert all indexable text files as MemoryItems.

    Args:
        session:       AsyncSession — passed through to GitHub API helpers for
                       installation-token resolution (no direct DB writes here).
        provider:      MemoryProvider instance (from get_memory_provider()).
        repo:          ``"owner/name"`` — validated by list_repo_files.
        team_scope:    Team slug; embedded in every MemoryItem.
        project_scope: Optional project slug; defaults to *repo* when absent so
                       recall can be filtered to the repo with project_scope=repo.
        ref:           Git ref (branch, tag, SHA). Default "HEAD".

    Returns:
        dict with keys: repo, ref, files_indexed, chunks_upserted, skipped, capped.

    Raises:
        ValueError:             Invalid *repo* format.
        GithubAppNotInstalled:  App not installed on the owner.
        GithubPermissionDenied: GitHub 403 (missing contents:read).
        (Per-file errors are caught internally — they increment *skipped* and
        do NOT abort the overall sync.)
    """
    effective_project = project_scope or repo
    log.info(
        "github_sync.start",
        repo=repo,
        ref=ref,
        team_scope=team_scope,
        project_scope=effective_project,
    )

    files_indexed = 0
    chunks_upserted = 0
    skipped = 0
    capped = False
    walk_failed = False
    # Every path this walk actually saw, and the chunks it just wrote for the
    # file in hand. Both feed the pruning below; without them a re-sync leaves
    # the previous version's chunks in memory_items and Qdrant forever.
    seen_paths: set[str] = set()
    chunks_pruned = 0

    # BFS queue of directory paths to visit
    queue: list[str] = [""]  # start at repo root

    now = datetime.now(timezone.utc)
    source = f"github:{repo}"[:128]

    while queue:
        dir_path = queue.pop(0)
        try:
            entries = await list_repo_files(session, repo, path=dir_path, ref=ref)
        except (GithubAppNotInstalled, GithubPermissionDenied):
            raise  # propagate — caller maps these to HTTP errors
        except Exception as exc:
            log.warning(
                "github_sync.list_error",
                repo=repo,
                dir_path=dir_path,
                error=str(exc),
            )
            skipped += 1
            # A directory we could not list is a directory whose files we cannot
            # claim to have seen. That disqualifies the end-of-sync sweep — see
            # the guard below.
            walk_failed = True
            continue

        for entry in entries:
            etype = entry.get("type")
            ename = entry.get("name", "")
            epath = entry.get("path", "")
            esize = entry.get("size")
            esha = entry.get("sha", "")

            if etype == "dir":
                queue.append(epath)
                continue

            if etype != "file":
                # symlink, submodule — skip
                continue

            if not _should_index(ename, esize):
                log.debug(
                    "github_sync.skip_file",
                    repo=repo,
                    path=epath,
                    size=esize,
                )
                skipped += 1
                continue

            if files_indexed >= MAX_FILES:
                log.info(
                    "github_sync.cap_files",
                    repo=repo,
                    cap=MAX_FILES,
                )
                capped = True
                break

            # --- Read + chunk + upsert ---
            try:
                file_data = await read_repo_file(session, repo, epath, ref)
                text = file_data.get("content", "")
            except Exception as exc:
                log.warning(
                    "github_sync.read_error",
                    repo=repo,
                    path=epath,
                    error=str(exc),
                )
                skipped += 1
                continue

            windows = _line_windows(text)
            if not windows:
                skipped += 1
                continue

            file_upserted = 0
            seen_paths.add(epath)
            written_ids: set[str] = set()
            for chunk_idx, chunk_text in enumerate(windows):
                if chunks_upserted >= MAX_CHUNKS_TOTAL:
                    log.info(
                        "github_sync.cap_chunks",
                        repo=repo,
                        cap=MAX_CHUNKS_TOTAL,
                    )
                    capped = True
                    break

                # team_scope MUST be part of the idempotency key — otherwise the
                # same repo synced into two teams collides on one row (last write
                # flips team_scope), breaking team isolation.
                item_id = str(
                    uuid.uuid5(
                        GITHUB_SYNC_NS,
                        f"{team_scope}:{repo}:{epath}:{esha}:{chunk_idx}",
                    )
                )

                item = MemoryItem(
                    id=item_id,
                    team_scope=team_scope,
                    project_scope=effective_project,
                    content=chunk_text.strip(),
                    metadata={
                        "repo": repo,
                        "file_path": epath,
                        "sha": esha,
                        "ref": ref,
                        "chunk_idx": chunk_idx,
                        "ingestion_origin": "github-sync",
                    },
                    visibility=Visibility.TEAM,
                    truth_level=TruthLevel.WORKING,
                    confidence=0.6,
                    source=source,
                    validation_status=ValidationStatus.PENDING,
                    created_at=now,
                    updated_at=now,
                )

                try:
                    await provider.upsert(item)
                    written_ids.add(item_id)
                    chunks_upserted += 1
                    file_upserted += 1
                except Exception as exc:
                    log.warning(
                        "github_sync.upsert_error",
                        repo=repo,
                        path=epath,
                        chunk_idx=chunk_idx,
                        error=str(exc),
                    )
                    skipped += 1

                if capped:
                    break

            # Retire the previous version of THIS file. Runs only when the file
            # was actually rewritten: pruning after a file whose every chunk
            # failed to upsert would delete the good copy and leave nothing.
            if file_upserted > 0:
                for stale_id in await _stale_chunk_ids(
                    session,
                    team_scope=team_scope,
                    repo=repo,
                    file_path=epath,
                    keep_ids=written_ids,
                ):
                    try:
                        await provider.mark_deleted(stale_id, now)
                        chunks_pruned += 1
                    except Exception as exc:
                        log.warning(
                            "github_sync.prune_error",
                            repo=repo,
                            path=epath,
                            item_id=stale_id,
                            error=str(exc),
                        )

            if file_upserted > 0:
                files_indexed += 1
                log.debug(
                    "github_sync.file_ok",
                    repo=repo,
                    path=epath,
                    chunks=file_upserted,
                )

            if capped:
                break

        if capped:
            break

    # Files that vanished from the repo — deleted or renamed. Only safe after a
    # COMPLETE walk: a capped run stopped early and a failed listing never saw
    # its directory, so in either case "not seen" does not mean "not there", and
    # sweeping would delete a file that still exists.
    if not capped and not walk_failed and seen_paths:
        for stale_id in await _stale_chunk_ids(
            session,
            team_scope=team_scope,
            repo=repo,
            file_path=None,
            keep_ids=set(),
            seen_paths=seen_paths,
        ):
            try:
                await provider.mark_deleted(stale_id, now)
                chunks_pruned += 1
            except Exception as exc:
                log.warning(
                    "github_sync.sweep_error", repo=repo, item_id=stale_id, error=str(exc)
                )
    elif chunks_upserted:
        log.info(
            "github_sync.sweep_skipped",
            repo=repo,
            capped=capped,
            walk_failed=walk_failed,
            reason="an incomplete walk cannot tell a deleted file from an unseen one",
        )

    summary = {
        "repo": repo,
        "ref": ref,
        "files_indexed": files_indexed,
        "chunks_upserted": chunks_upserted,
        "chunks_pruned": chunks_pruned,
        "skipped": skipped,
        "capped": capped,
    }
    log.info("github_sync.done", **summary)
    return summary
