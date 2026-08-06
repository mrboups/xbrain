"""The brain tag's read predicate, and the argument that cannot be forgotten.

Migration 0034 adds `team_messages.private_to_user_id`. The whole safety of the
feature is that every read carries the matching predicate — and the failure mode
of forgetting it is one member's hidden notes rendered into another member's
chat.

So the argument is REQUIRED with no default, and that is a design property worth
a test of its own: a new read added in six months fails at call time instead of
returning everything. These run without a database on purpose — a signature and
a compiled WHERE clause are exactly what is being asserted, and a test that
needs Postgres to say so is a test nobody runs before pushing.
"""
from __future__ import annotations

import inspect
import uuid

import pytest

from app.models.team_message import TeamMessage
from app.repos import team_messages as tm_repo

READS = [
    tm_repo.list_messages,
    tm_repo.get_live_message,
    tm_repo.get_recent_messages_chronological,
]


@pytest.mark.parametrize("fn", READS, ids=lambda f: f.__name__)
def test_every_read_requires_a_viewer(fn):
    """No default. A caller that forgets it gets a TypeError, not everything."""
    param = inspect.signature(fn).parameters["viewer_user_id"]
    assert param.default is inspect.Parameter.empty, (
        f"{fn.__name__} gave viewer_user_id a default — a forgotten filter must "
        f"fail loudly, not silently widen the result set"
    )
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


def test_count_unread_takes_its_viewer_from_the_member_it_counts_for():
    """It has no separate viewer argument — `exclude_user_id` IS the viewer."""
    params = inspect.signature(tm_repo.count_unread_since).parameters
    assert "exclude_user_id" in params
    assert params["exclude_user_id"].default is inspect.Parameter.empty


def _sql(clause) -> str:
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


def test_an_identified_viewer_sees_team_rows_and_their_own():
    sql = _sql(tm_repo.visible_to(uuid.uuid4()))
    assert "private_to_user_id IS NULL" in sql
    assert " OR " in sql


def test_an_unidentified_viewer_sees_only_team_rows():
    """None is the CLOSED answer. A caller with no acting user must not widen."""
    sql = _sql(tm_repo.visible_to(None))
    assert "private_to_user_id IS NULL" in sql
    assert " OR " not in sql


def test_the_predicate_never_matches_someone_elses_row():
    """The only equality allowed is against the viewer."""
    viewer = uuid.UUID("11111111-1111-4111-8111-111111111111")
    other = uuid.UUID("22222222-2222-4222-8222-222222222222")
    sql = _sql(tm_repo.visible_to(viewer))
    # Rendered without hyphens by the literal binder — compare on .hex.
    assert viewer.hex in sql
    assert other.hex not in sql


def test_the_column_exists_on_the_model_and_is_nullable():
    """NULL is 'the team sees it' — every row that predates the tag."""
    col = TeamMessage.__table__.c.private_to_user_id
    assert col.nullable is True
    assert col.default is None or col.default.arg is None


def test_the_column_points_at_users_and_survives_their_deletion():
    """SET NULL turns a deleted member's hidden notes back into ordinary rows.

    That is the safe direction for a chat surface — the alternative is rows
    nobody can ever read — but it is a real behaviour, so it is pinned.
    """
    (fk,) = list(TeamMessage.__table__.c.private_to_user_id.foreign_keys)
    assert fk.column.table.name == "users"
    assert fk.ondelete == "SET NULL"
