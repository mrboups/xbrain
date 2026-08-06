"""Every recalled fact says who — or what — introduced it.

Owner's requirement, 2026-08-06. The data was already stored (`brain_ingest`
writes `metadata.author_sub`) and already reached the model, but as a raw
`team-chat:github:someone` with nothing telling the model what it meant.

The distinction these tests defend is between a PERSON and a CONNECTOR. A Drive
file has no author in this table, and a line that reads "added by Alice" when
Alice merely owns the folder invents a claim the model will repeat as fact.

No database: the attribution is a pure function of one row mapping, and that is
exactly what is being asserted.
"""
from __future__ import annotations

from app.services import team_context_cache as tcc


def _row(**over):
    row = {
        "content": "the venue is in Seoul",
        "truth_level": "WORKING",
        "source": "team-chat:github:mrboups",
        "preferred_name": None,
        "display_name": None,
        "email": None,
        "author_source_user_id": None,
    }
    row.update(over)
    return row


def test_a_human_author_is_named():
    line = tcc._attribution(
        _row(author_source_user_id="github:mrboups", display_name="Alice Martin")
    )
    assert line == "added by Alice Martin"


def test_the_preferred_name_wins_over_the_display_name():
    """The only name a user can write about themselves outranks the imported one."""
    line = tcc._attribution(
        _row(
            author_source_user_id="github:mrboups",
            display_name="Alice Martin",
            preferred_name="Ali",
        )
    )
    assert line == "added by Ali"


def test_an_item_with_no_author_names_its_connector():
    line = tcc._attribution(_row(source="google-drive", author_source_user_id=None))
    assert line == "from google-drive"
    assert "added by" not in line


def test_a_connector_item_never_borrows_a_person():
    """The LEFT JOIN can return name columns; without an author they must not win."""
    line = tcc._attribution(
        _row(source="granola", author_source_user_id=None, display_name="Alice Martin")
    )
    assert "Alice Martin" not in line
    assert line == "from granola"


def test_an_unknown_origin_says_so_rather_than_guessing():
    assert tcc._attribution(_row(source=None, author_source_user_id=None)) == "from unknown"


def test_the_attribution_reaches_the_rendered_line():
    line = tcc._format_item("the venue is in Seoul", "WORKING", "added by Alice Martin")
    assert "(added by Alice Martin)" in line
    assert "the venue is in Seoul" in line


def test_the_legend_tells_the_model_what_the_parenthetical_means():
    """Without this the model sees a parenthetical and does not attribute."""
    legend = tcc._LEGEND
    assert "added by" in legend
    assert "from <connector>" in legend
    assert "never" in legend.lower()


def test_the_legend_stays_deterministic():
    """It is inlined into a cached prompt block — a varying legend busts the cache."""
    assert tcc._LEGEND == tcc._LEGEND
    assert "{" not in tcc._LEGEND
