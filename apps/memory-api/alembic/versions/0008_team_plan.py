"""team_plan — add plan column to teams (starter/team/enterprise)

Historical: this column was added to enforce a tiered product that was CANCELLED by locked decision Q6
(2026-07-11) — no product feature is paywalled, and requirement EDIT-03 is dropped. The column is now
VESTIGIAL. It is deliberately NOT dropped here: a migration is an immutable historical record, and
removing a live column is a schema change well outside Phase 15's scope. If it is ever removed, that
belongs in its own migration with its own plan.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "teams",
        sa.Column(
            "plan",
            sa.String(16),
            nullable=False,
            server_default="starter",
        ),
    )
    op.create_check_constraint(
        "teams_plan_check",
        "teams",
        "plan IN ('starter', 'team', 'enterprise')",
    )


def downgrade() -> None:
    op.drop_constraint("teams_plan_check", "teams", type_="check")
    op.drop_column("teams", "plan")
