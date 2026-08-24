"""Teradata adapter — UNVERIFIED.

Written against DBC.TablesV / ColumnsV / IndicesV / StatsV and the type codes
the catalog documents, unit-tested against canned rows, and never yet run
against a live Teradata system. :attr:`TeradataAdapter.verified` is False,
which flags every bundle this adapter produces UNVERIFIED until the session-6
dry run on the workplace dev system clears it.

Three Teradata specifics matter more than the rest:

* **Primary Index.** ``IndexType = 'P'`` is the distribution key. With no
  declared FKs — the normal legacy case — it is the strongest join-intent
  signal in the system, so it gets its own flag on the index rather than
  being flattened into "an index".
* **Index identity is IndexNumber.** Teradata indexes are routinely unnamed,
  so ``(DatabaseName, TableName, IndexNumber)`` is the identity and
  ``IndexName`` is a label.
* **Padded dictionary text.** DBC returns CHAR columns space-padded. Every
  string is trimmed on the way in; an untrimmed ``'ACCOUNT   '`` would fail
  the allow-list and silently drop the table from profiling.
"""

from __future__ import annotations

from ..results import Column, ColumnStats, Constraint, Index, Table, TableStats
from ..types import canonical_type
from .base import (
    Adapter,
    Parsed,
    ParseWarning,
    Rows,
    as_bool,
    as_date,
    as_int,
    as_text,
    group_ordered,
)

#: DBC.TablesV.TableKind values the A1-TD block selects.
TABLE_KINDS = {
    "T": "BASE TABLE",   # ordinary table
    "O": "BASE TABLE",   # table without a primary index (NoPI)
    "V": "VIEW",
}

#: DBC.ColumnsV.ColumnType codes -> canonical type names. The catalog lists
#: CV/CF/I/D/DA/TS explicitly and abbreviates the rest with "etc."; the rest
#: are the standard Teradata codes.
COLUMN_TYPES = {
    "CF": "CHAR",
    "CV": "VARCHAR",
    "CO": "CLOB",
    "BF": "BYTE",
    "BV": "VARBYTE",
    "BO": "BLOB",
    "I1": "BYTEINT",
    "I2": "SMALLINT",
    "I": "INT",
    "I8": "BIGINT",
    "D": "DECIMAL",
    "N": "NUMBER",
    "F": "FLOAT",
    "DA": "DATE",
    "AT": "TIME",
    "TS": "TIMESTAMP",
    "TZ": "TIME WITH TIME ZONE",
    "SZ": "TIMESTAMP WITH TIME ZONE",
}

#: Types whose declared size is a length rather than a precision.
LENGTH_CODES = ("CF", "CV", "BF", "BV")

#: Types that carry precision and scale.
DECIMAL_CODES = ("D", "N")

#: IndexType values from DBC.IndicesV.
PRIMARY_INDEX = "P"
INDEX_CONSTRAINT_KINDS = {"K": "PRIMARY KEY", "U": "UNIQUE"}

#: DBC.StatsV rows for multi-column statistics list the columns in one field.
MULTI_COLUMN_SEPARATOR = ","


def teradata_type(code, length=None, total_digits=None, fractional_digits=None) -> str:
    """Canonical type for one DBC.ColumnsV row."""
    code = (as_text(code) or "").upper()
    name = COLUMN_TYPES.get(code)
    if name is None:
        return f"UNKNOWN({code})" if code else "UNKNOWN"
    if code in LENGTH_CODES:
        return canonical_type(name, length=as_int(length))
    if code in DECIMAL_CODES:
        return canonical_type(
            name, precision=as_int(total_digits), scale=as_int(fractional_digits)
        )
    return canonical_type(name)


class TeradataAdapter(Adapter):
    engine = "teradata"
    #: No live Teradata system has run this. See BUILD-PLAN session 6.
    verified = False

    # -- A1-TD: table inventory -------------------------------------------

    def parse_tables(self, rows: Rows) -> Parsed:
        tables, warnings = [], []
        for schema, name, table_kind in rows:
            code = (as_text(table_kind) or "").upper()
            kind = TABLE_KINDS.get(code)
            if kind is None:
                warnings.append(
                    ParseWarning(
                        f"{as_text(schema)}.{as_text(name)}: unexpected "
                        f"TableKind {code!r}; cataloged as BASE TABLE",
                        schema=as_text(schema),
                    )
                )
                kind = "BASE TABLE"
            tables.append(
                Table(schema=as_text(schema), name=as_text(name), kind=kind)
            )
        return tables, warnings

    # -- A2-TD: column inventory ------------------------------------------

    def parse_columns(self, rows: Rows) -> Parsed:
        columns = []
        for row in rows:
            (
                schema, table, name, column_id, column_type, column_length,
                total_digits, fractional_digits, nullable, default,
            ) = row
            code = (as_text(column_type) or "").upper()
            length = as_int(column_length) if code in LENGTH_CODES else None
            precision = as_int(total_digits) if code in DECIMAL_CODES else None
            scale = as_int(fractional_digits) if code in DECIMAL_CODES else None
            columns.append(
                Column(
                    schema=as_text(schema),
                    table=as_text(table),
                    name=as_text(name),
                    ordinal=as_int(column_id),
                    type=teradata_type(
                        column_type, column_length, total_digits, fractional_digits
                    ),
                    raw_type=code,
                    nullable=as_bool(nullable, default=True),
                    length=length,
                    precision=precision,
                    scale=scale,
                    default=as_text(default),
                )
            )
        return columns, []

    # -- A3-TD: constraints ------------------------------------------------

    def parse_constraints(self, rows: Rows) -> Parsed:
        constraints, warnings = [], []
        prepared = []
        for schema, table, index_name, index_number, index_type, column, position in rows:
            code = (as_text(index_type) or "").upper()
            kind = INDEX_CONSTRAINT_KINDS.get(code)
            if kind is None:
                warnings.append(
                    ParseWarning(
                        f"{as_text(schema)}.{as_text(table)}: A3-TD returned "
                        f"IndexType {code!r}, which the block does not "
                        "select; skipped",
                        schema=as_text(schema),
                    )
                )
                continue
            prepared.append(
                {
                    "schema": as_text(schema),
                    "table": as_text(table),
                    "name": as_text(index_name),
                    "number": as_int(index_number),
                    "kind": kind,
                    "column": as_text(column),
                    "position": as_int(position),
                }
            )
        grouped = group_ordered(
            prepared,
            key=lambda r: (r["schema"], r["table"], r["number"]),
            position=lambda r: r["position"],
        )
        for (schema, table, _number), group, _duplicates in grouped:
            constraints.append(
                Constraint(
                    kind=group[0]["kind"],
                    schema=schema,
                    table=table,
                    columns=tuple(r["column"] for r in group),
                    name=group[0]["name"],
                )
            )
        constraints.sort(key=lambda c: (c.schema, c.table, c.kind, c.columns))
        return constraints, warnings

    # -- A4-TD: indexes, including the Primary Index -----------------------

    def parse_indexes(self, rows: Rows) -> Parsed:
        prepared = [
            {
                "schema": as_text(schema),
                "table": as_text(table),
                "name": as_text(index_name),
                "number": as_int(index_number),
                "type": (as_text(index_type) or "").upper(),
                "unique": as_bool(unique_flag, default=False),
                "column": as_text(column),
                "position": as_int(position),
            }
            for (
                schema, table, index_name, index_number, index_type, unique_flag,
                column, position,
            ) in rows
        ]
        indexes = []
        grouped = group_ordered(
            prepared,
            key=lambda r: (r["schema"], r["table"], r["number"]),
            position=lambda r: r["position"],
        )
        for (schema, table, _number), group, _duplicates in grouped:
            indexes.append(
                Index(
                    schema=schema,
                    table=table,
                    name=group[0]["name"],
                    unique=bool(group[0]["unique"]),
                    columns=tuple(r["column"] for r in group),
                    primary_index=group[0]["type"] == PRIMARY_INDEX,
                )
            )
        indexes.sort(key=lambda i: (i.schema, i.table, i.name or "", i.columns))
        return indexes, []

    # -- A6-TD: dictionary statistics --------------------------------------

    def parse_table_stats(self, rows: Rows) -> Parsed:
        """Row counts from DBC.StatsV.

        One row per collected statistic, so a table appears as many times as
        it has statistics and each row carries the row count as of *its*
        collection. The most recent collection wins.
        """
        latest: dict[tuple[str, str], tuple] = {}
        for schema, table, _column, row_count, _distinct, _nulls, collected in rows:
            key = (as_text(schema), as_text(table))
            count = as_int(row_count)
            if count is None:
                continue
            collected_on = as_date(collected)
            previous = latest.get(key)
            if previous is None or _later(collected_on, previous[1]):
                latest[key] = (count, collected_on)
        stats = [
            TableStats(
                schema=schema,
                table=table,
                row_count=count,
                source="stats",
                stats_date=collected_on,
            )
            for (schema, table), (count, collected_on) in sorted(latest.items())
        ]
        return stats, []

    def parse_column_stats(self, rows: Rows) -> Parsed:
        """Per-column statistics from DBC.StatsV.

        A column with no collected statistic is absent from StatsV, which
        means no statistics — never zero distinct values. Multi-column
        statistics arrive as one row with a comma-separated ColumnName and
        describe a combination, not a column, so they are skipped here.
        """
        stats = []
        for schema, table, column, _row_count, distinct, nulls, collected in rows:
            name = as_text(column)
            if not name or MULTI_COLUMN_SEPARATOR in name:
                continue
            distinct_count = as_int(distinct)
            null_count = as_int(nulls)
            stats.append(
                ColumnStats(
                    schema=as_text(schema),
                    table=as_text(table),
                    column=name,
                    distinct_count=distinct_count if _counted(distinct_count) else None,
                    null_count=null_count if _counted(null_count) else None,
                    stats_date=as_date(collected),
                )
            )
        stats.sort(key=lambda s: (s.schema, s.table, s.column))
        return stats, []

    # -- A5-TD: reconciliation ---------------------------------------------

    def parse_reconciliation(self, rows: Rows) -> tuple[int | None, list]:
        rows = list(rows)
        if not rows or not rows[0]:
            return None, [
                ParseWarning("A5 returned no row; visible table count unknown")
            ]
        return as_int(rows[0][0]), []


def _counted(value) -> bool:
    """True for a real count. Negative means never collected, not minus one."""
    return value is not None and value >= 0


def _later(candidate, current) -> bool:
    if candidate is None:
        return False
    return current is None or candidate > current
