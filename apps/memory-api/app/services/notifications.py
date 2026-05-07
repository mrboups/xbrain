"""Email notifications for tasks (D6). Fail-soft when SMTP not configured."""

from email.message import EmailMessage

import structlog

from app.config import settings

log = structlog.get_logger(__name__)


async def send_task_notification_email(
    *,
    recipient_email: str | None,
    task_title: str,
    task_id: str,
    team_scope: str,
    dashboard_url: str | None = None,
) -> None:
    """Send a 'task assigned' notification email.

    Fail-soft contract:
    - If SMTP_HOST is empty (not configured), log warning and return.
    - If recipient_email is empty/None, log warning and return.
    - Any send error is caught and logged (never raised).
    """
    if not settings.SMTP_HOST:
        log.warning("email.smtp_not_configured", task_id=task_id, recipient=recipient_email)
        return
    if not recipient_email:
        log.warning("email.no_recipient", task_id=task_id, team_scope=team_scope)
        return

    try:
        # Lazy-import to avoid ImportError at module load when aiosmtplib missing
        import aiosmtplib

        msg = EmailMessage()
        msg["From"] = settings.SMTP_FROM
        msg["To"] = recipient_email
        msg["Subject"] = f"New task assigned: {task_title[:200]}"
        body_lines = [
            f"You have been assigned a new task in xbrain ({team_scope}):",
            "",
            f"  {task_title}",
            "",
            f"Task ID: {task_id}",
        ]
        if dashboard_url:
            body_lines.extend(["", f"View in dashboard: {dashboard_url}"])
        body_lines.extend(["", "—", "xbrain (sent by noreply — do not reply)"])
        msg.set_content("\n".join(body_lines))

        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER or None,
            password=settings.SMTP_PASSWORD or None,
            start_tls=settings.SMTP_TLS,
            timeout=20,
        )
        log.info("email.sent", task_id=task_id, recipient=recipient_email)
    except Exception as exc:
        log.warning(
            "email.send_failed",
            task_id=task_id,
            recipient=recipient_email,
            error=str(exc),
        )
