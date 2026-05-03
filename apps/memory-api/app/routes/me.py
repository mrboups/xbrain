"""/v1/me — return the current authenticated principal (user or bridge)."""

from typing import Any

from fastapi import APIRouter, Depends

from app.deps import get_current_principal

router = APIRouter()


@router.get("/me")
async def me(principal: dict[str, Any] = Depends(get_current_principal)) -> dict[str, Any]:
    if principal["kind"] == "user":
        u = principal["user"]
        return {
            "kind": "user",
            "id": str(u.id),
            "source_user_id": u.source_user_id,
            "email": u.email,
            "display_name": u.display_name,
        }
    return {
        "kind": "bridge",
        "sub": principal.get("sub"),
        "team_scope": principal.get("team_scope"),
        "iss": principal["claims"].get("iss"),
    }
