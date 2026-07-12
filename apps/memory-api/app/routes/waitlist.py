"""POST /v1/waitlist — proxy Resend email signup, no auth required."""

import html
import logging
import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
WAITLIST_TO = os.getenv("WAITLIST_TO", "")
WAITLIST_FROM = os.getenv("WAITLIST_FROM", "Example <waitlist@example.com>")


class WaitlistRequest(BaseModel):
    name: str
    email: str
    plan: str = "Early Access"


@router.post("/waitlist", status_code=200)
async def join_waitlist(body: WaitlistRequest):
    if not RESEND_API_KEY or not WAITLIST_TO:
        raise HTTPException(status_code=503, detail="Email service not configured")

    payload = {
        "from": WAITLIST_FROM,
        "to": [WAITLIST_TO],
        "reply_to": body.email,
        "subject": f"xbrain waitlist: {body.name} ({body.plan})",
        # This endpoint is unauthenticated, so name/email/plan are attacker-controlled. Escape
        # before interpolating into HTML, or a submitted name can inject arbitrary markup (links,
        # images, a fake message) into the notification email the team receives.
        "html": (
            f"<p><strong>Name:</strong> {html.escape(body.name)}</p>"
            f"<p><strong>Email:</strong> {html.escape(body.email)}</p>"
            f"<p><strong>Plan:</strong> {html.escape(body.plan)}</p>"
        ),
    }

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        )

    if r.status_code == 200:
        return {"ok": True}

    logger.error("Resend error %s: %s", r.status_code, r.text)
    raise HTTPException(status_code=502, detail=r.json().get("message", "Email failed"))
