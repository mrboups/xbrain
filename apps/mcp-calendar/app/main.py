"""MCP Calendar sidecar — Google Calendar read-only.

Standalone FastMCP, port 8102. Single worker only.
Use case: agent contextualizes responses with upcoming meetings.
"""
from __future__ import annotations

import json
import asyncio
import structlog
from mcp.server.fastmcp import FastMCP
from app.calendar_client import list_user_events

log = structlog.get_logger(__name__)
mcp = FastMCP("xbrain-calendar", host="0.0.0.0", port=8102)


@mcp.tool()
async def list_events(date_range: str = "today+7days") -> str:
    """List Google Calendar events for the given date range.

    Args:
        date_range: When to look for events. Formats:
            - "today" — events today only
            - "today+7days" — events from today for the next 7 days (DEFAULT)
            - "today+30days" — events for the next 30 days
            - "2026-05-04" — events on a specific date
            - "2026-05-04:2026-05-10" — explicit date range

    Returns:
        JSON string with a list of events.
        Each event: {id, summary, start, end, attendees: [email, ...]}.
        Returns '[]' if no events found.
    """
    events = await asyncio.get_event_loop().run_in_executor(
        None, list_user_events, date_range
    )
    return json.dumps(events, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
