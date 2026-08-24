"""The allow-list — what may and may not reach a SQL template.

The rejection cases matter more than the acceptance ones. An allow-list that
accepts everything it is asked about is not an allow-list, and the failure it
prevents (an identifier from somewhere other than this crawl being formatted
into catalog SQL) is the one failure mode the whole no-dynamic-SQL rule
exists to stop.
"""

from __future__ import annotations

import pytest

from crawler import AllowList, AllowListError, Column, Table, is_bare_identifier

TABLES = [
    Table("CORE", "album", "BASE TABLE"),
    Table("CORE", "artist", "BASE TABLE"),
    Table("SALES", "invoice", "BASE TABLE"),
]

COLUMNS = [
    Column("CORE", "album", "album_id", 1, "INT", "integer", False),
    Column("CORE", "album", "title", 2, "VARCHAR(160)", "character varying", False),
    Column("CORE", "artist", "artist_id", 1, "INT", "integer", False),
    Column("SALES", "invoice", "invoice_id", 1, "INT", "integer", False),
]


@pytest.fixture
def allowlist() -> AllowList:
    return AllowList.from_inventory(TABLES, COLUMNS)


# -- what the crawl observed is allowed ------------------------------------


def test_observed_tables_and_columns_are_admitted(allowlist):
    assert allowlist.table("CORE", "album") == ("CORE", "album")
    assert allowlist.column("CORE", "album", "title") == ("CORE", "album", "title")
    assert allowlist.qualify("SALES", "invoice") == "SALES.invoice"


def test_membership_test_covers_tables_and_columns(allowlist):
    assert "CORE.album" in allowlist
    assert ("CORE", "album", "title") in allowlist
    assert "CORE.ghost" not in allowlist
    assert ("CORE", "album", "ghost") not in allowlist


def test_case_insensitive_lookup_returns_the_observed_spelling(allowlist):
    """Engines disagree about case; the allow-list still answers in the
    dictionary's own spelling, never the caller's."""
    assert allowlist.table("core", "ALBUM") == ("CORE", "album")
    assert allowlist.column("core", "album", "TITLE")[2] == "title"


def test_inventory_is_reported_sorted(allowlist):
    assert allowlist.schemas == ("CORE", "SALES")
    assert allowlist.table_names() == ("CORE.album", "CORE.artist", "SALES.invoice")
    assert allowlist.column_names("CORE", "album") == ("album_id", "title")
    assert len(allowlist) == 3


# -- everything else is rejected -------------------------------------------


def test_unknown_schema_is_rejected(allowlist):
    with pytest.raises(AllowListError, match="schema 'PAYROLL' was not observed"):
        allowlist.table("PAYROLL", "album")


def test_unknown_table_is_rejected(allowlist):
    with pytest.raises(AllowListError, match="CORE.salary was not observed"):
        allowlist.table("CORE", "salary")


def test_unknown_column_is_rejected(allowlist):
    with pytest.raises(AllowListError, match="CORE.album.ssn was not observed"):
        allowlist.column("CORE", "album", "ssn")


def test_a_column_of_another_table_is_rejected(allowlist):
    """Observed somewhere is not observed here."""
    with pytest.raises(AllowListError):
        allowlist.column("CORE", "artist", "title")


@pytest.mark.parametrize(
    "hostile",
    [
        'album"; DROP TABLE artist; --',
        "album WHERE 1=1",
        "album'",
        "al bum",
        "album;",
        "",
        "1album",
        "x" * 200,
    ],
)
def test_hostile_identifiers_are_never_admitted(allowlist, hostile):
    assert not is_bare_identifier(hostile)
    with pytest.raises(AllowListError):
        allowlist.table("CORE", hostile)
    with pytest.raises(AllowListError):
        allowlist.column("CORE", "album", hostile)


def test_non_bare_observed_names_are_recorded_not_admitted():
    """A real dictionary can hold a name that needs quoting. It is cataloged,
    reported, and kept out of every template — not quietly interpolated."""
    tables = [*TABLES, Table("CORE", "order detail", "BASE TABLE")]
    columns = [
        *COLUMNS,
        Column("CORE", "order detail", "qty", 1, "INT", "integer", False),
        Column("CORE", "album", "unit price", 3, "INT", "integer", False),
    ]
    allowlist = AllowList.from_inventory(tables, columns)

    assert "CORE.order detail" not in allowlist
    assert ("CORE", "album", "unit price") not in allowlist
    reasons = {r.identifier: r.reason for r in allowlist.rejected}
    assert "CORE.order detail" in reasons
    assert "not a bare identifier" in reasons["CORE.order detail"]
    assert "CORE.album.unit price" in reasons
    # the rest of the table is unaffected
    assert allowlist.column("CORE", "album", "title")[2] == "title"


def test_columns_of_tables_a1_never_returned_are_rejected():
    """A2 seeing a table A1 did not is a grant oddity, not a licence to
    profile it."""
    allowlist = AllowList.from_inventory(
        [Table("CORE", "album", "BASE TABLE")],
        [
            Column("CORE", "album", "album_id", 1, "INT", "integer", False),
            Column("CORE", "hidden", "secret", 1, "INT", "integer", False),
        ],
    )
    assert "CORE.hidden" not in allowlist
    assert any("A1 inventory did not return" in r.reason for r in allowlist.rejected)


def test_ambiguous_case_folding_is_an_error():
    """Two tables differing only in case: guessing which one is meant would
    be worse than refusing."""
    allowlist = AllowList.from_inventory(
        [Table("CORE", "Album", "BASE TABLE"), Table("CORE", "album", "BASE TABLE")],
        [],
    )
    with pytest.raises(AllowListError, match="matches 2 observed identifiers"):
        allowlist.table("CORE", "ALBUM")


def test_roundtrips_through_json(allowlist):
    restored = AllowList.from_obj(allowlist.to_obj())
    assert restored.tables == allowlist.tables
    assert restored.rejected == allowlist.rejected
    assert restored.column("CORE", "album", "title")[2] == "title"
