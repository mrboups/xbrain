"""team_plan — add plan column to teams (starter/team/enterprise) — D2 paid tier enforcement

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
