"""Healthz + minimal metrics endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from app.pool import pool

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "active_sockets": pool.active_count()}


@router.get("/metrics")
async def metrics() -> dict:
    # Phase 9 uses JSON, not Prometheus textfile format. /metrics is for the
    # popup's debug pane + verify-phase9.sh test 6.
    return {"status": "ok", "active_sockets": pool.active_count()}
