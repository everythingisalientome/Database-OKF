"""PostgreSQL's own blocks: pg_index (A4) and pg_stats (A6).

pg_stats is the one place in Tier A where the dictionary's numbers cannot be
copied across. ``n_distinct`` is signed — a count when positive, a negated
ratio when negative — and getting that wrong does not fail loudly: it puts
every wide column under every cardinality gate in the system, and step 2
quietly stops proposing the edges that matter.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from crawler.adapters import PostgresAdapter
from crawler.adapters.postgres import distinct_from_n_distinct


@pytest.fixture
def adapter() -> PostgresAdapter:
    return PostgresAdapter()


# -- A4: indexes -----------------------------------------------------------


def test_a4_groups_index_columns_in_key_order(adapter):
    rows = [
        ("public", "playlist_track", "pk_playlist_track", True, "track_id", 2, False),
        ("public", "playlist_track", "pk_playlist_track", True, "playlist_id", 1,
         False),
        ("public", "album", "ifk_album_artist_id", False, "artist_id", 1, False),
    ]
    indexes, warnings = adapter.parse_indexes(rows)
    assert warnings == []
    assert [(i.table, i.name, i.columns, i.unique) for i in indexes] == [
        ("album", "ifk_album_artist_id", ("artist_id",), False),
        ("playlist_track", "pk_playlist_track", ("playlist_id", "track_id"), True),
    ]


def test_a4_never_sets_the_teradata_primary_index_flag(adapter):
    rows = [("public", "album", "pk_album", True, "album_id", 1, False)]
    indexes, _warnings = adapter.parse_indexes(rows)
    assert indexes[0].primary_index is False


def test_a4_separates_include_columns_from_key_columns(adapter):
    """P8 adopted: ``k.ordinality > ix.indnkeyatts`` marks the payload half
    of a covering index. A4 is join-intent evidence, and an INCLUDE column is
    explicitly not something anyone searches on."""
    rows = [
        ("public", "invoice", "ix_invoice_customer", False, "customer_id", 1, False),
        ("public", "invoice", "ix_invoice_customer", False, "total", 2, True),
    ]
    indexes, warnings = adapter.parse_indexes(rows)
    assert warnings == []
    assert indexes[0].columns == ("customer_id",)
    assert indexes[0].included_columns == ("total",)


def test_a4_reads_the_include_flag_however_the_driver_types_it(adapter):
    """psycopg returns the comparison as a bool; other drivers hand back
    't'/'f' or 1/0."""
    rows = [
        ("public", "invoice", "ix_invoice_customer", "f", "customer_id", 1, "f"),
        ("public", "invoice", "ix_invoice_customer", "f", "total", 2, "t"),
    ]
    indexes, _warnings = adapter.parse_indexes(rows)
    assert indexes[0].columns == ("customer_id",)
    assert indexes[0].included_columns == ("total",)


# -- A6: pg_stats ----------------------------------------------------------


@pytest.mark.parametrize(
    ("n_distinct", "est_rows", "expected"),
    [
        (25.0, 3503, 25),          # a count, used as-is
        (-1.0, 3503, 3503),        # every value distinct
        (-0.9298, 3503, 3257),     # a ratio of rows
        (-0.5, 100, 50),
        (0, 3503, None),           # planner has no estimate
        (None, 3503, None),
        (-1.0, None, None),        # a ratio with nothing to multiply by
        (-1.0, -1, None),          # never analysed
    ],
)
def test_n_distinct_is_interpreted_not_copied(n_distinct, est_rows, expected):
    assert distinct_from_n_distinct(n_distinct, est_rows) == expected


def test_a6_reads_column_statistics(adapter, pg_a6_rows):
    stats, warnings = adapter.parse_column_stats(pg_a6_rows)
    assert warnings == []
    by_column = {(s.table, s.column): s for s in stats}

    album_id = by_column[("album", "album_id")]
    assert album_id.distinct_count == 347
    assert album_id.null_rate == 0.0
    assert album_id.null_count is None  # pg_stats gives a fraction, not a count
    assert album_id.approximate is True
    assert album_id.stats_date == date(2026, 8, 20)

    assert by_column[("album", "title")].distinct_count == 312
    assert by_column[("artist", "artist_id")].distinct_count == 275


def test_a6_reads_one_row_estimate_per_table(adapter, pg_a6_rows):
    stats, warnings = adapter.parse_table_stats(pg_a6_rows)
    assert warnings == []
    assert [(s.table, s.row_count, s.estimated) for s in stats] == [
        ("album", 347, True),
        ("artist", 275, True),
    ]


def test_a_table_with_no_column_statistics_keeps_its_row_estimate(adapter):
    """P9 adopted: the block drives from pg_class, so an empty table — which
    has no pg_stats rows at all — still reports its estimate. The junk-table
    filter looks for exactly this: row_count = 0."""
    rows = [("public", "media_type", None, None, None, 0, date(2026, 8, 20))]
    stats, warnings = adapter.parse_table_stats(rows)
    assert warnings == []
    assert [(s.table, s.row_count, s.estimated) for s in stats] == [
        ("media_type", 0, True),
    ]
    columns, _warnings = adapter.parse_column_stats(rows)
    assert columns == [], "a row with no column name describes the table"


def test_a_never_analysed_table_has_no_statistics(adapter):
    """reltuples = -1 is a missing-stats signal, not a row count of minus
    one. Recording it would make B1 skip the scan that is actually needed."""
    rows = [("public", "album", "album_id", -1.0, 0.0, -1, None)]
    stats, _warnings = adapter.parse_table_stats(rows)
    assert stats == []


def test_stats_timestamps_are_recorded_as_dates(adapter):
    rows = [
        ("public", "album", "album_id", 347.0, 0.0, 347,
         datetime(2026, 8, 20, 3, 14, 15)),
    ]
    stats, _warnings = adapter.parse_column_stats(rows)
    assert stats[0].stats_date == date(2026, 8, 20)


# -- A7/A8 — session 6b ------------------------------------------------------


def test_a7_views_and_routines_arrive_through_their_own_shapes(adapter):
    views, _ = adapter.parse_code_objects(
        [("public", "album_titles", "SELECT 1;")], key="A7"
    )
    assert [(v.kind, v.definition) for v in views] == [("VIEW", "SELECT 1;")]

    routines, _ = adapter.parse_code_objects(
        [
            ("public", "rig_dynamic_count", "FUNCTION", "BEGIN END"),
            ("public", "c_internal", "FUNCTION", None),
        ],
        key="A7-routines",
    )
    assert [(r.kind, r.definition) for r in routines] == [
        ("FUNCTION", "BEGIN END"),
        ("FUNCTION", None),  # opaque body: recorded, counted unparsed later
    ]


def test_a8_foreign_servers_are_recorded_lineage(adapter):
    references, _ = adapter.parse_external_references(
        [("files_srv", "postgres_fdw")]
    )
    [reference] = references
    assert reference.kind == "foreign-server"
    assert reference.target == "files_srv"
    assert reference.detail == "postgres_fdw"
    assert reference.source == "pg_foreign_server"
