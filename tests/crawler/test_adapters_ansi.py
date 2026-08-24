"""ANSI / PostgreSQL row readers.

Rows are positional, exactly as the catalog block's SELECT list orders them,
because that is what a DB-API cursor hands back. If a catalog block's column
order ever changes, these tests are where it shows up.
"""

from __future__ import annotations

import pytest

from crawler.adapters import PostgresAdapter


@pytest.fixture
def adapter() -> PostgresAdapter:
    return PostgresAdapter()


def test_engine_is_verified(adapter):
    assert adapter.engine == "postgres"
    assert adapter.verified


def test_a1_reads_tables_and_views(adapter, pg_a1_rows):
    tables, warnings = adapter.parse_tables(pg_a1_rows)
    assert warnings == []
    assert [(t.schema, t.name, t.kind) for t in tables] == [
        ("public", "album", "BASE TABLE"),
        ("public", "artist", "BASE TABLE"),
        ("public", "album_titles", "VIEW"),
        ("pg_catalog", "pg_class", "BASE TABLE"),
    ]
    assert tables[0].catalog == "chinook"
    assert tables[0].is_base_table
    assert not tables[2].is_base_table


def test_a2_canonicalises_types_and_keeps_the_engine_spelling(adapter, pg_a2_rows):
    columns, warnings = adapter.parse_columns(pg_a2_rows)
    assert warnings == []
    by_name = {(c.table, c.name): c for c in columns}

    album_id = by_name[("album", "album_id")]
    assert album_id.type == "INT"
    assert album_id.raw_type == "integer"
    assert album_id.ordinal == 1
    assert album_id.nullable is False

    title = by_name[("album", "title")]
    assert title.type == "VARCHAR(160)"
    assert title.length == 160
    assert title.precision is None

    name = by_name[("artist", "name")]
    assert name.nullable is True


def test_a3_folds_column_rows_into_constraints(adapter, pg_a3_rows):
    constraints, warnings = adapter.parse_constraints(pg_a3_rows)
    assert warnings == []
    assert [(c.kind, c.table, c.columns, c.name) for c in constraints] == [
        ("FOREIGN KEY", "album", ("artist_id",), "fk_album_artist"),
        ("PRIMARY KEY", "album", ("album_id",), "pk_album"),
        ("PRIMARY KEY", "artist", ("artist_id",), "pk_artist"),
    ]


def test_a3_keeps_composite_key_column_order(adapter):
    """Ordinal position decides the order, not the order rows arrive in —
    a composite key's leading column is what step 2 reads as join intent."""
    rows = [
        ("PRIMARY KEY", "public", "playlist_track", "pk_playlist_track",
         "track_id", 2, None, None, None, None),
        ("PRIMARY KEY", "public", "playlist_track", "pk_playlist_track",
         "playlist_id", 1, None, None, None, None),
    ]
    constraints, warnings = adapter.parse_constraints(rows)
    assert warnings == []
    assert constraints[0].columns == ("playlist_id", "track_id")


def test_a3_resolves_the_foreign_key_target(adapter, pg_a3_rows):
    """The whole point of the adopted A3 block (P6): a foreign key comes back
    with the table AND columns it points at, which is what the OKF table
    format requires."""
    constraints, _warnings = adapter.parse_constraints(pg_a3_rows)
    fk = next(c for c in constraints if c.kind == "FOREIGN KEY")
    assert fk.referenced_constraint == "pk_artist"
    assert fk.referenced_table == "public.artist"
    assert fk.referenced_columns == ("artist_id",)


def test_a3_pairs_composite_foreign_key_columns_by_position(adapter):
    """The referenced columns are resolved here, not in SQL: SQL Server's
    information_schema has no position_in_unique_constraint."""
    rows = [
        ("PRIMARY KEY", "public", "playlist_track", "pk_playlist_track",
         "playlist_id", 1, None, None, None, None),
        ("PRIMARY KEY", "public", "playlist_track", "pk_playlist_track",
         "track_id", 2, None, None, None, None),
        ("FOREIGN KEY", "public", "play_log", "fk_play_log_pt",
         "log_playlist_id", 1, "public", "pk_playlist_track",
         "public", "playlist_track"),
        ("FOREIGN KEY", "public", "play_log", "fk_play_log_pt",
         "log_track_id", 2, "public", "pk_playlist_track",
         "public", "playlist_track"),
    ]
    constraints, warnings = adapter.parse_constraints(rows)
    assert warnings == []
    fk = next(c for c in constraints if c.kind == "FOREIGN KEY")
    assert fk.columns == ("log_playlist_id", "log_track_id")
    assert fk.referenced_columns == ("playlist_id", "track_id")


def test_two_same_target_foreign_keys_stay_separate(adapter):
    """What P6 fixed: with constraint_name selected, two foreign keys to the
    same table are two constraints, not one invented composite."""
    rows = [
        ("PRIMARY KEY", "public", "track", "pk_track", "track_id", 1,
         None, None, None, None),
        ("FOREIGN KEY", "public", "invoice_line", "fk_line_track", "track_id", 1,
         "public", "pk_track", "public", "track"),
        ("FOREIGN KEY", "public", "invoice_line", "fk_line_alt_track",
         "alt_track_id", 1, "public", "pk_track", "public", "track"),
    ]
    constraints, warnings = adapter.parse_constraints(rows)
    assert warnings == []
    fks = [c for c in constraints if c.kind == "FOREIGN KEY"]
    assert {c.columns for c in fks} == {("track_id",), ("alt_track_id",)}
    assert all(c.referenced_columns == ("track_id",) for c in fks)


def test_an_unreachable_target_is_reported_not_guessed(adapter):
    """The referenced table sits outside this account's grants, so A3 never
    returned its primary key. The target table is kept, the columns stay
    empty, and the crawl says so."""
    rows = [
        ("FOREIGN KEY", "public", "invoice", "fk_invoice_customer",
         "customer_id", 1, "billing", "pk_customer", "billing", "customer"),
    ]
    constraints, warnings = adapter.parse_constraints(rows)
    fk = constraints[0]
    assert fk.referenced_table == "billing.customer"
    assert fk.referenced_columns == ()
    assert "target columns unresolved" in warnings[0].message
    assert warnings[0].schema == "public"


def test_a5_reads_the_visible_table_count(adapter):
    count, warnings = adapter.parse_reconciliation([(11,)])
    assert count == 11
    assert warnings == []


def test_a5_with_no_row_is_reported_not_assumed(adapter):
    count, warnings = adapter.parse_reconciliation([])
    assert count is None
    assert [w.message for w in warnings] == [
        "A5 returned no row; visible table count unknown"
    ]
    assert warnings[0].schema is None


def test_postgres_now_has_every_tier_a_block(adapter):
    """P1 and P2 are adopted: index and statistics evidence is collected
    rather than reported missing."""
    for key in ("A1", "A2", "A3", "A4", "A6", "A5"):
        assert adapter.statement(key) is not None
