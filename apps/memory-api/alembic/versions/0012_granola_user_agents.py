"""granola_user_agents — granola_user_connections (per-user) + agent_definitions registry + seed meeting-recap (D1+D4+D5+D6 Phase 8).

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_MEETING_RECAP_SYSTEM_PROMPT = """Tu es un agent de résumé de réunion. À partir d'un transcript ou d'un résumé brut de meeting, génère un compte-rendu structuré au format Markdown contenant :

## Résumé exécutif
2-3 phrases sur l'essentiel discuté.

## Participants
Liste à puces des personnes présentes (nom, et email/rôle si mentionné).

## Décisions
Liste numérotée des décisions actées pendant la réunion.

## Action items
Liste à puces : "[Assignee] — Action à entreprendre — Échéance si mentionnée".

## Points ouverts
Sujets non tranchés à reprendre plus tard.

Reste factuel — n'invente pas de participants, de décisions ou d'actions absentes du texte source. Si une section est vide, écris "(aucun)" sous le titre."""


def upgrade() -> None:
    # ── Table: granola_user_connections (D1 — clé Granola per-user) ─────────────
    op.create_table(
        "granola_user_connections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("team_scope", sa.String(64), nullable=False),
        sa.Column("api_key_enc", sa.Text, nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", name="guc_user_uniq"),
    )
    op.create_index("idx_guc_user", "granola_user_connections", ["user_id"])

    # ── Table: agent_definitions (D4 — registry agents plateforme) ──────────────
    op.create_table(
        "agent_definitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("system_prompt", sa.Text, nullable=False),
        sa.Column(
            "model",
            sa.String(128),
            nullable=False,
            server_default="claude-haiku-4-5-20251001",
        ),
        sa.Column("tools_json", postgresql.JSONB, nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "auto_trigger",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("name", name="agent_definitions_name_uniq"),
    )
    op.create_index("idx_agent_definitions_enabled", "agent_definitions", ["enabled"])

    # ── Seed: agent meeting-recap (D5 — auto_trigger=true) ──────────────────────
    # Idempotent: ON CONFLICT (name) DO NOTHING — re-running does not duplicate.
    op.execute(
        sa.text(
            """
            INSERT INTO agent_definitions (name, description, system_prompt, model, auto_trigger, enabled)
            VALUES (:name, :desc, :prompt, :model, true, true)
            ON CONFLICT (name) DO NOTHING
            """
        ).bindparams(
            name="meeting-recap",
            desc="Génère un résumé structuré de réunion (résumé exécutif, participants, décisions, action items, points ouverts) depuis un transcript Granola.",
            prompt=_MEETING_RECAP_SYSTEM_PROMPT,
            model="claude-haiku-4-5-20251001",
        )
    )


def downgrade() -> None:
    op.drop_index("idx_agent_definitions_enabled", table_name="agent_definitions")
    op.drop_table("agent_definitions")
    op.drop_index("idx_guc_user", table_name="granola_user_connections")
    op.drop_table("granola_user_connections")
