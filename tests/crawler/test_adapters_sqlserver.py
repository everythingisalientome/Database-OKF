"""SQL Server row readers — the two blocks information_schema cannot cover."""

from __future__ import annotations

from datetime import date

import pytest

from crawler.adapters import SqlServerAdapter


@pytest.fixture
def adapter() -> SqlServerAdapter:
    return SqlServerAdapter()


def test_engine_is_verified(adapter):
    assert adapter.engine == "sqlserver"
    assert adapter.verified


def test_ansi_blocks_are_inherited(adapter):
    """A1/A2/A3/A5 come from the same ANSI blocks PostgreSQL runs."""
    from crawler import catalog

    for key in ("A1", "A2", "A3", "A5"):
        assert adapter.statement(key).variant == "ansi"
    assert adapter.statement("A4") is catalog.A4_SQLSERVER
    assert adapter.statement("A6") is catalog.A6_SQLSERVER
    assert adapter.statement("A6-columns") is catalog.A6_COLUMNS_SQLSERVER


def test_a4_groups_index_columns_in_key_order(adapter):
    rows = [
        ("dbo", "playlist_track", "PK_playlist_track", True, "track_id", 2),
        ("dbo", "playlist_track", "PK_playlist_track", True, "playlist_id", 1),
        ("dbo", "invoice_line", "IFK_invoice_line_track", False, "track_id", 1),
    ]
    indexes, warnings = adapter.parse_indexes(rows)
    assert warnings == []
    assert [(i.table, i.name, i.columns, i.unique) for i in indexes] == [
        ("invoice_line", "IFK_invoice_line_track", ("track_id",), False),
        ("playlist_track", "PK_playlist_track", ("playlist_id", "track_id"), True),
    ]


def test_a4_never_sets_the_teradata_primary_index_flag(adapter):
    rows = [("dbo", "album", "PK_album", True, "album_id", 1)]
    indexes, _warnings = adapter.parse_indexes(rows)
    assert indexes[0].primary_index is False


def test_a4_separates_include_columns_from_key_columns(adapter):
    """key_ordinal 0 is an INCLUDE column: payload, not join intent. Counting
    it as a key column would invent evidence step 2 would then weigh."""
    rows = [
        ("dbo", "invoice", "IX_invoice_customer", False, "customer_id", 1),
        ("dbo", "invoice", "IX_invoice_customer", False, "total", 0),
    ]
    indexes, warnings = adapter.parse_indexes(rows)
    assert warnings == []
    assert indexes[0].columns == ("customer_id",)
    assert indexes[0].included_columns == ("total",)


def test_a4_reads_the_bit_column_as_a_boolean(adapter):
    """pymssql returns sys.indexes.is_unique as 0/1, pyodbc as True/False."""
    rows = [("dbo", "album", "PK_album", 1, "album_id", 1)]
    indexes, _warnings = adapter.parse_indexes(rows)
    assert indexes[0].unique is True


def test_a4_reports_a_nameless_index_row_rather_than_naming_it(adapter):
    rows = [("dbo", "album", None, False, "album_id", 1)]
    indexes, warnings = adapter.parse_indexes(rows)
    assert indexes == []
    assert "no index name" in warnings[0].message
    assert warnings[0].schema == "dbo"


def test_a6_sums_partition_row_counts(adapter):
    """dm_db_partition_stats returns one row per partition; the first row
    alone would undercount every partitioned table."""
    rows = [
        ("dbo", "invoice_line", 1000),
        ("dbo", "invoice_line", 1240),
        ("dbo", "album", 347),
    ]
    stats, warnings = adapter.parse_table_stats(rows)
    assert warnings == []
    assert [(s.table, s.row_count, s.source) for s in stats] == [
        ("album", 347, "stats"),
        ("invoice_line", 2240, "stats"),
    ]


def test_a6_columns_reads_histogram_distincts(adapter):
    """P3 adopted: per-column distincts, from the statistics histograms."""
    rows = [
        ("dbo", "track", "track_id", 3503, 3503, date(2026, 8, 20), 3503),
        ("dbo", "track", "genre_id", 3503, 3503, date(2026, 8, 20), 25),
    ]
    stats, warnings = adapter.parse_column_stats(rows)
    assert warnings == []
    assert [(s.column, s.distinct_count) for s in stats] == [
        ("genre_id", 25),
        ("track_id", 3503),
    ]
    assert all(s.approximate for s in stats), (
        "a histogram sum is an estimate; recording it as measured would let "
        "B2 be skipped on a number that was never counted"
    )
    assert stats[0].stats_date == date(2026, 8, 20)
    assert stats[0].null_count is None and stats[0].null_rate is None


def test_table_row_counts_are_not_estimates(adapter):
    """dm_db_partition_stats counts rows; it does not estimate them."""
    stats, _warnings = adapter.parse_table_stats([("dbo", "album", 347)])
    assert stats[0].estimated is False
