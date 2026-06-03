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
            for chunk_idx, chunk_text in enumerate(windows):
                if chunks_upserted >= MAX_CHUNKS_TOTAL:
                    log.info(
                        "github_sync.cap_chunks",
                        repo=repo,
                        cap=MAX_CHUNKS_TOTAL,
                    )
                    capped = True
                    break

                item_id = str(
                    uuid.uuid5(
                        GITHUB_SYNC_NS,
                        f"{repo}:{epath}:{esha}:{chunk_idx}",
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

    summary = {
        "repo": repo,
        "ref": ref,
        "files_indexed": files_indexed,
        "chunks_upserted": chunks_upserted,
        "skipped": skipped,
        "capped": capped,
    }
    log.info("github_sync.done", **summary)
    return summary
