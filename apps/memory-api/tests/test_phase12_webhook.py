"""Tests for POST /v1/webhooks/github/installation (Plan 12-05).

Covers:
  - HMAC signature verification (missing header → 401, wrong sig → 401,
    correct sig → 200).
  - installation.created → row UPSERT.
  - installation.deleted → revoked_at set.
  - installation.suspend / unsuspend cycle.
  - installation_repositories event → 200, no installations row touched.
  - ping event → 200.
  - Duplicate delivery is idempotent (UPSERT on conflict).

Mocking strategy: monkeypatch settings.GITHUB_APP_WEBHOOK_SECRET to a
known value per test so the HMAC roundtrip is deterministic and doesn't
depend on the operator's real secret. The client + session fixtures
already share a transaction (conftest.py::session + client), so all
state mutations are visible without an explicit refresh.

Note: marked `integration` because the route writes to Postgres via the
real Installation ORM — testcontainers spins up PG for these.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from app.models.installation import Installation

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


WEBHOOK_SECRET = "test_webhook_secret_for_phase12_unit_tests"


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    """Force a known webhook secret so signing is deterministic."""
    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "GITHUB_APP_WEBHOOK_SECRET", WEBHOOK_SECRET)
    # Also patch the imported reference in the route module — pydantic-settings
    # snapshots fields at import time, and the route reads via settings.X so
    # the runtime read picks the monkeypatched value.
    from app.routes import webhooks_github as route_mod

    monkeypatch.setattr(route_mod.settings, "GITHUB_APP_WEBHOOK_SECRET", WEBHOOK_SECRET)
    yield


def _sign(body: bytes) -> str:
    """Produce the X-Hub-Signature-256 header value for `body`."""
    return "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()


def _installation_payload(
    action: str,
    *,
    installation_id: int = 999,
    org_login: str = "testorg",
    org_id: int = 12345,
    permissions: dict | None = None,
) -> dict:
    return {
        "action": action,
        "installation": {
            "id": installation_id,
            "account": {"login": org_login, "type": "Organization", "id": org_id},
            "permissions": permissions if permissions is not None else {"members": "read"},
        },
        "sender": {"login": "admin", "id": 67890},
    }


async def _post(
    client: AsyncClient,
    payload: dict,
    *,
    event: str = "installation",
    sign: bool = True,
):
    raw = json.dumps(payload).encode("utf-8")
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": "test-delivery-uuid-0001",
        "Content-Type": "application/json",
    }
    if sign:
        headers["X-Hub-Signature-256"] = _sign(raw)
    return await client.post(
        "/v1/webhooks/github/installation",
        content=raw,
        headers=headers,
    )


# === Signature verification ==================================================


async def test_signature_missing_returns_401(client: AsyncClient):
    """No X-Hub-Signature-256 header → 401 (auth failure)."""
    r = await _post(client, _installation_payload("created"), sign=False)
    assert r.status_code == 401, r.text


async def test_signature_invalid_returns_401(client: AsyncClient):
    """Mangled signature → 401 with no body details leaked."""
    raw = json.dumps(_installation_payload("created")).encode("utf-8")
    bad_sig = "sha256=" + "00" * 32  # zeroed hex of correct length shape
    r = await client.post(
        "/v1/webhooks/github/installation",
        content=raw,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": bad_sig,
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401, r.text


async def test_signature_invalid_does_not_touch_db(client: AsyncClient, session):
    """Wrong signature MUST NOT create an installations row (Pitfall 5 lock).

    If the route parsed JSON before verifying HMAC, a downstream bug could
    leak the create path on unauthenticated payloads. We assert nothing was
    inserted as a safety net.
    """
    raw = json.dumps(_installation_payload("created", installation_id=42)).encode()
    r = await client.post(
        "/v1/webhooks/github/installation",
        content=raw,
        headers={
            "X-GitHub-Event": "installation",
            "X-Hub-Signature-256": "sha256=deadbeef",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 401
    row = (
        await session.execute(
            sa.select(Installation).where(Installation.installation_id == 42)
        )
    ).scalar_one_or_none()
    assert row is None, "row was inserted despite signature mismatch"


# === Event dispatch ==========================================================


async def test_installation_created_inserts_row(client: AsyncClient, session):
    """installation.created → new installations row with revoked_at NULL."""
    payload = _installation_payload(
        "created", installation_id=1111, org_login="dejavudev",
    )
    r = await _post(client, payload)
    assert r.status_code == 200, r.text
    row = (
        await session.execute(
            sa.select(Installation).where(Installation.installation_id == 1111)
        )
    ).scalar_one()
    assert row.github_org_login == "dejavudev"
    assert row.github_account_type == "Organization"
    assert row.installed_by_github_id == 67890  # from sender.id
    assert row.revoked_at is None
    assert row.suspended_at is None
    assert row.permissions == {"members": "read"}


async def test_installation_deleted_sets_revoked_at(client: AsyncClient, session):
    """installation.deleted on an existing row → revoked_at is populated."""
    session.add(Installation(
        installation_id=2222,
        github_org_login="someorg",
        github_account_type="Organization",
        permissions={},
        raw_payload={},
    ))
    await session.commit()

    r = await _post(client, _installation_payload("deleted", installation_id=2222))
    assert r.status_code == 200, r.text

    row = (
        await session.execute(
            sa.select(Installation).where(Installation.installation_id == 2222)
        )
    ).scalar_one()
    assert row.revoked_at is not None, "revoked_at should be set after delete event"


async def test_installation_suspend_unsuspend_cycle(client: AsyncClient, session):
    """suspend sets suspended_at; unsuspend clears it."""
    session.add(Installation(
        installation_id=3333,
        github_org_login="someorg-suspend",
        github_account_type="Organization",
        permissions={},
        raw_payload={},
    ))
    await session.commit()

    await _post(client, _installation_payload("suspend", installation_id=3333))
    row = (
        await session.execute(
            sa.select(Installation).where(Installation.installation_id == 3333)
        )
    ).scalar_one()
    assert row.suspended_at is not None, "suspended_at should be set"

    await _post(client, _installation_payload("unsuspend", installation_id=3333))
    await session.refresh(row)
    assert row.suspended_at is None, "suspended_at should be cleared after unsuspend"


async def test_installation_new_permissions_accepted_updates_permissions(
    client: AsyncClient, session,
):
    """new_permissions_accepted → permissions JSONB is refreshed."""
    session.add(Installation(
        installation_id=4040,
        github_org_login="perms-org",
        github_account_type="Organization",
        permissions={"members": "read"},
        raw_payload={},
    ))
    await session.commit()

    payload = _installation_payload(
        "new_permissions_accepted",
        installation_id=4040,
        permissions={"members": "read", "metadata": "read"},
    )
    r = await _post(client, payload)
    assert r.status_code == 200, r.text

    row = (
        await session.execute(
            sa.select(Installation).where(Installation.installation_id == 4040)
        )
    ).scalar_one()
    assert row.permissions == {"members": "read", "metadata": "read"}


async def test_installation_repositories_event_logged_only(client: AsyncClient, session):
    """installation_repositories event is logged but no row is inserted (v1 stub)."""
    payload = {
        "action": "added",
        "installation": {"id": 4444},
        "repositories_added": [{"name": "foo"}],
        "repositories_removed": [],
    }
    r = await _post(client, payload, event="installation_repositories")
    assert r.status_code == 200, r.text
    row = (
        await session.execute(
            sa.select(Installation).where(Installation.installation_id == 4444)
        )
    ).scalar_one_or_none()
    assert row is None, "installation_repositories must not touch installations table"


async def test_ping_event_returns_200(client: AsyncClient):
    """GitHub sends a `ping` event right after webhook config — must 200 it."""
    r = await _post(client, {"zen": "Approachable is better than simple."}, event="ping")
    assert r.status_code == 200, r.text


async def test_duplicate_delivery_idempotent(client: AsyncClient, session):
    """Replay of installation.created with same id → still one row (UPSERT path)."""
    payload = _installation_payload(
        "created", installation_id=5555, org_login="idempotent-org",
    )
    r1 = await _post(client, payload)
    r2 = await _post(client, payload)
    assert r1.status_code == 200 and r2.status_code == 200

    rows = (
        await session.execute(
            sa.select(Installation).where(Installation.installation_id == 5555)
        )
    ).scalars().all()
    assert len(rows) == 1, f"expected idempotent UPSERT, got {len(rows)} rows"


async def test_unknown_action_returns_200(client: AsyncClient, session):
    """An unhandled installation.action MUST return 200 (no GitHub retry)."""
    payload = _installation_payload("future_unknown_action", installation_id=6060)
    r = await _post(client, payload)
    assert r.status_code == 200, r.text
    # Nothing inserted (no `created` action handler hit).
    row = (
        await session.execute(
            sa.select(Installation).where(Installation.installation_id == 6060)
        )
    ).scalar_one_or_none()
    assert row is None
