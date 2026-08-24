"""Teradata row readers — UNVERIFIED until the session-6 dry run.

These rows are what DBC.TablesV / ColumnsV / IndicesV are documented to
return, including the space padding DBC gives CHAR columns. Passing here is
not the same as working: the adapter has never met a live Teradata, and the
tests assert that it says so.
"""

from __future__ import annotations

from datetime import date

import pytest

from crawler.adapters import TeradataAdapter
from crawler.adapters.teradata import teradata_type


@pytest.fixture
def adapter() -> TeradataAdapter:
    return TeradataAdapter()


def test_the_adapter_declares_itself_unverified(adapter):
    assert adapter.engine == "teradata"
    assert adapter.verified is False


def test_a1_maps_table_kinds_and_trims_padding(adapter):
    rows = [
        ("FINANCE   ", "ACCOUNT   ", "T"),
        ("FINANCE   ", "ACCT_NOPI ", "O"),
        ("FINANCE   ", "V_ACCOUNT ", "V"),
    ]
    tables, warnings = adapter.parse_tables(rows)
    assert warnings == []
    assert [(t.schema, t.name, t.kind) for t in tables] == [
        ("FINANCE", "ACCOUNT", "BASE TABLE"),
        ("FINANCE", "ACCT_NOPI", "BASE TABLE"),
        ("FINANCE", "V_ACCOUNT", "VIEW"),
    ]


def test_a1_reports_an_unexpected_table_kind(adapter):
    tables, warnings = adapter.parse_tables([("FINANCE", "MACRO_X", "M")])
    assert tables[0].kind == "BASE TABLE"
    assert "unexpected TableKind" in warnings[0].message


@pytest.mark.parametrize(
    ("code", "length", "digits", "fraction", "expected"),
    [
        ("CV", 40, None, None, "VARCHAR(40)"),
        ("CF", 2, None, None, "CHAR(2)"),
        ("I", 4, None, None, "INT"),
        ("I1", 1, None, None, "BYTEINT"),
        ("I2", 2, None, None, "SMALLINT"),
        ("I8", 8, None, None, "BIGINT"),
        ("D", 8, 18, 0, "DECIMAL(18,0)"),
        ("DA", 4, None, None, "DATE"),
        ("TS", 10, None, None, "TIMESTAMP"),
        ("ZZ", 4, None, None, "UNKNOWN(ZZ)"),
    ],
)
def test_column_type_codes_map_to_canonical_types(
    code, length, digits, fraction, expected
):
    assert teradata_type(code, length, digits, fraction) == expected


def test_a2_reads_columns(adapter):
    rows = [
        ("FINANCE", "ACCOUNT", "ACCT_ID   ", 1, "D ", 8, 18, 0, "N", None),
        ("FINANCE", "ACCOUNT", "ACCT_STS_CD", 2, "CF", 2, None, None, "Y", None),
    ]
    columns, warnings = adapter.parse_columns(rows)
    assert warnings == []
    acct_id, status = columns
    assert (acct_id.name, acct_id.type, acct_id.nullable) == (
        "ACCT_ID", "DECIMAL(18,0)", False,
    )
    assert acct_id.precision == 18 and acct_id.scale == 0 and acct_id.length is None
    assert (status.name, status.type, status.nullable) == (
        "ACCT_STS_CD", "CHAR(2)", True,
    )
    assert status.length == 2


def test_a3_maps_index_types_to_constraint_kinds(adapter):
    rows = [
        ("FINANCE", "ACCOUNT", "PK_ACCOUNT", 1, "K", "ACCT_ID", 1),
        ("FINANCE", "ACCOUNT", "UQ_ACCT_NBR", 4, "U", "ACCT_NBR", 1),
    ]
    constraints, warnings = adapter.parse_constraints(rows)
    assert warnings == []
    assert [(c.kind, c.columns, c.name) for c in constraints] == [
        ("PRIMARY KEY", ("ACCT_ID",), "PK_ACCOUNT"),
        ("UNIQUE", ("ACCT_NBR",), "UQ_ACCT_NBR"),
    ]


def test_a4_flags_the_primary_index(adapter):
    """The PI is the distribution key the designers chose, and on a legacy
    Teradata with no FKs it is the strongest join-intent signal there is."""
    rows = [
        ("FINANCE", "ACCOUNT", None, 1, "P", "N", "ACCT_ID", 1),
        ("FINANCE", "ACCOUNT", "IX_ACCT_STS", 4, "S", "N", "ACCT_STS_CD", 1),
        ("FINANCE", "ACCOUNT", "UQ_ACCT_NBR", 8, "U", "Y", "ACCT_NBR", 1),
    ]
    indexes, warnings = adapter.parse_indexes(rows)
    assert warnings == []
    by_columns = {i.columns: i for i in indexes}
    assert by_columns[("ACCT_ID",)].primary_index is True
    assert by_columns[("ACCT_ID",)].name is None
    assert by_columns[("ACCT_STS_CD",)].primary_index is False
    assert by_columns[("ACCT_NBR",)].unique is True


def test_a4_keeps_composite_primary_index_column_order(adapter):
    rows = [
        ("FINANCE", "TXN", None, 1, "P", "N", "TXN_DT", 2),
        ("FINANCE", "TXN", None, 1, "P", "N", "ACCT_ID", 1),
    ]
    indexes, warnings = adapter.parse_indexes(rows)
    assert warnings == []
    assert indexes[0].columns == ("ACCT_ID", "TXN_DT")
    assert indexes[0].primary_index is True


def test_two_unnamed_indexes_are_told_apart_by_number(adapter):
    """What P7 bought. Teradata indexes are routinely unnamed, and before
    IndexNumber was selected these two folded into one composite index that
    does not exist."""
    rows = [
        ("FINANCE", "ACCOUNT", None, 4, "S", "N", "ACCT_STS_CD", 1),
        ("FINANCE", "ACCOUNT", None, 8, "S", "N", "OPEN_DT", 1),
    ]
    indexes, warnings = adapter.parse_indexes(rows)
    assert warnings == []
    assert len(indexes) == 2
    assert {i.columns for i in indexes} == {("ACCT_STS_CD",), ("OPEN_DT",)}
    assert all(i.name is None for i in indexes)


def test_two_unnamed_constraints_are_told_apart_by_number(adapter):
    rows = [
        ("FINANCE", "ACCOUNT", None, 4, "U", "ACCT_NBR", 1),
        ("FINANCE", "ACCOUNT", None, 8, "U", "EXT_REF", 1),
    ]
    constraints, warnings = adapter.parse_constraints(rows)
    assert warnings == []
    assert {c.columns for c in constraints} == {("ACCT_NBR",), ("EXT_REF",)}


def test_teradata_now_has_every_tier_a_block(adapter):
    """P4 and P5 are adopted: statistics and reconciliation are collected."""
    for key in ("A1", "A2", "A3", "A4", "A6", "A5"):
        assert adapter.statement(key) is not None


def test_a5_reads_the_visible_table_count(adapter):
    count, warnings = adapter.parse_reconciliation([(4812,)])
    assert count == 4812
    assert warnings == []


def test_a6_takes_the_row_count_from_the_latest_collection(adapter):
    """StatsV has one row per collected statistic, each carrying the row
    count as of its own collection."""
    rows = [
        ("FINANCE", "ACCOUNT", "ACCT_ID", 4811002, 4811002, 0, date(2026, 8, 20)),
        ("FINANCE", "ACCOUNT", "ACCT_STS_CD", 4700000, 14, 94000, date(2026, 1, 4)),
    ]
    stats, warnings = adapter.parse_table_stats(rows)
    assert warnings == []
    assert [(s.table, s.row_count, s.stats_date) for s in stats] == [
        ("ACCOUNT", 4811002, date(2026, 8, 20)),
    ]
    assert stats[0].estimated is False


def test_a6_reads_per_column_statistics(adapter):
    rows = [
        ("FINANCE", "ACCOUNT", "ACCT_STS_CD", 4811002, 14, 94000, date(2026, 8, 20)),
    ]
    stats, warnings = adapter.parse_column_stats(rows)
    assert warnings == []
    assert (stats[0].column, stats[0].distinct_count, stats[0].null_count) == (
        "ACCT_STS_CD", 14, 94000,
    )
    assert stats[0].approximate is False


def test_a6_ignores_multi_column_statistics(adapter):
    """A statistic over (A, B) describes a combination, not a column."""
    rows = [
        ("FINANCE", "ACCOUNT", "ACCT_ID,OPEN_DT", 4811002, 4811002, 0, None),
        ("FINANCE", "ACCOUNT", "ACCT_ID", 4811002, 4811002, 0, None),
    ]
    stats, _warnings = adapter.parse_column_stats(rows)
    assert [s.column for s in stats] == ["ACCT_ID"]


def test_a6_reads_an_uncollected_count_as_unknown(adapter):
    """Teradata reports -1 where nothing was collected. Storing that as a
    distinct count would put the column under every cardinality gate."""
    rows = [("FINANCE", "ACCOUNT", "ACCT_ID", 4811002, -1, -1, None)]
    stats, _warnings = adapter.parse_column_stats(rows)
    assert stats[0].distinct_count is None
    assert stats[0].null_count is None
