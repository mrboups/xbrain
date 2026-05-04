"""Google Calendar v3 client helper for mcp-calendar sidecar.

Scope: calendar.readonly — read-only access to user's primary calendar.
Credentials: via GOOGLE_CALENDAR_ACCESS_TOKEN + GOOGLE_CALENDAR_REFRESH_TOKEN env vars.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any

import structlog
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials

log = structlog.get_logger(__name__)

MAX_EVENTS = 50


def _build_service():
    access_token = os.environ.get("GOOGLE_CALENDAR_ACCESS_TOKEN", "")
    refresh_token = os.environ.get("GOOGLE_CALENDAR_REFRESH_TOKEN", "")
    if not access_token:
        raise RuntimeError("GOOGLE_CALENDAR_ACCESS_TOKEN not set — Calendar tool requires OAuth setup")
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token or None,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _parse_date_range(date_range: str) -> tuple[str, str]:
    """Parse date_range string into (time_min_rfc3339, time_max_rfc3339).

    Supported formats:
    - "today" → today 00:00Z – 23:59Z
    - "today+7days" → today 00:00Z – today+7 23:59Z (DEFAULT)
    - "2026-05-04" → that day 00:00Z – 23:59Z
    - "2026-05-04:2026-05-10" → explicit range
    """
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def fmt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    if ":" in date_range and not date_range.startswith("today"):
        # Explicit range: "2026-05-04:2026-05-10"
        parts = date_range.split(":", 1)
        start = datetime.fromisoformat(parts[0]).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(parts[1]).replace(tzinfo=timezone.utc, hour=23, minute=59, second=59)
        return fmt(start), fmt(end)
    elif date_range.startswith("today+"):
        days_str = date_range.replace("today+", "").replace("days", "").strip()
        days = int(days_str)
        return fmt(today), fmt(today + timedelta(days=days, hours=23, minutes=59))
    elif date_range == "today":
        return fmt(today), fmt(today.replace(hour=23, minute=59, second=59))
    else:
        # Try ISO date
        try:
            d = datetime.fromisoformat(date_range).replace(tzinfo=timezone.utc)
            return fmt(d), fmt(d.replace(hour=23, minute=59, second=59))
        except ValueError:
            # Fallback: today + 7 days
            log.warning("calendar.invalid_date_range", input=date_range)
            return fmt(today), fmt(today + timedelta(days=7, hours=23, minutes=59))


def list_user_events(date_range: str = "today+7days") -> list[dict[str, Any]]:
    """List Google Calendar events for the given date range.

    Returns a list of event dicts with: id, summary, start, end, attendees.
    """
    service = _build_service()
    time_min, time_max = _parse_date_range(date_range)
    log.info("calendar.list_events", time_min=time_min, time_max=time_max)

    try:
        result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            maxResults=MAX_EVENTS,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
    except HttpError as exc:
        log.error("calendar.list_error", status=exc.resp.status)
        raise

    events = []
    for item in result.get("items", []):
        events.append({
            "id": item.get("id"),
            "summary": item.get("summary", "(no title)"),
            "start": item.get("start", {}).get("dateTime") or item.get("start", {}).get("date"),
            "end": item.get("end", {}).get("dateTime") or item.get("end", {}).get("date"),
            "attendees": [a.get("email") for a in item.get("attendees", []) if a.get("email")],
        })
    log.info("calendar.events_returned", count=len(events))
    return events
