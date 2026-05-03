"""langgraph checkpoint tables — managed by langgraph-checkpoint-postgres setup()

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-03

This migration is a deliberate NO-OP. The `checkpoints`, `checkpoint_writes`,
and `checkpoint_blobs` tables used by AsyncPostgresSaver are owned by the
`langgraph-checkpoint-postgres` package, not by Alembic. They are created at
agent-runtime boot via `AsyncPostgresSaver.setup()` (idempotent).

We register this revision so the alembic head stays linear and a future
migration that DOES need to coexist with langgraph state has a known parent.
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intentionally empty — langgraph-checkpoint-postgres owns its DDL.
    pass


def downgrade() -> None:
    pass
