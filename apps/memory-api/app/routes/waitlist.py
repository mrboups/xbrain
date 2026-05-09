"""POST /v1/waitlist — proxy Resend email signup, no auth required."""

import logging
import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
WAITLIST_TO = os.getenv("WAITLIST_TO", "team@grooveos.app")
WAITLIST_FROM = os.getenv("WAITLIST_FROM", "GrooveOS <waitlist@grooveos.app>")


class WaitlistRequest(BaseModel):
    name: str
    email: str
    plan: str = "Early Access"


@router.post("/waitlist", status_code=200)
async def join_waitlist(body: WaitlistRequest):
    if not RESEND_API_KEY:
        raise HTTPException(status_code=503, detail="Email service not configured")

    payload = {
        "from": WAITLIST_FROM,
        "to": [WAITLIST_TO],
        "reply_to": body.email,
        "subject": f"GrooveOS waitlist: {body.name} ({body.plan})",
        "html": (
            f"<p><strong>Name:</strong> {body.name}</p>"
            f"<p><strong>Email:</strong> {body.email}</p>"
            f"<p><strong>Plan:</strong> {body.plan}</p>"
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
