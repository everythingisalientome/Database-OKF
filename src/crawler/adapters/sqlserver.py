"""SQL Server adapter.

A1, A2, A3 and A5 come from information_schema through the ANSI blocks. A4
reads sys.indexes; A6 is answered by two blocks, one for table row counts
from sys.dm_db_partition_stats and one for per-column distinct estimates from
the statistics histograms.

The histogram numbers are approximations by construction — a sum over
histogram steps, on the leading column of each statistics object only — so
they are recorded as approximate and B2 still has to run wherever one lands
near a gate boundary.
"""

from __future__ import annotations

from ..results import ColumnStats, TableStats
from .ansi import AnsiAdapter
from .base import Parsed, Rows, as_date, as_int, as_text, read_indexes

#: sys.index_columns.key_ordinal is 0 for INCLUDE columns — they are payload,
#: not key columns, and must not be read as join-intent evidence.
INCLUDED_COLUMN_ORDINAL = 0


class SqlServerAdapter(AnsiAdapter):
    engine = "sqlserver"
    verified = True

    # -- A4: indexes -------------------------------------------------------

    def parse_indexes(self, rows: Rows) -> Parsed:
        return read_indexes(
            rows,
            is_included=lambda row: as_int(row[5]) == INCLUDED_COLUMN_ORDINAL,
        )

    # -- A6: dictionary statistics ----------------------------------------

    def parse_table_stats(self, rows: Rows) -> Parsed:
        """Row counts per table.

        dm_db_partition_stats reports one row per partition, so the counts
        are summed per table; taking the first row would undercount every
        partitioned table.
        """
        totals: dict[tuple[str, str], int] = {}
        for schema, table, row_count in rows:
            key = (as_text(schema), as_text(table))
            totals[key] = totals.get(key, 0) + (as_int(row_count) or 0)
        stats = [
            TableStats(schema=schema, table=table, row_count=count, source="stats")
            for (schema, table), count in sorted(totals.items())
        ]
        return stats, []

    def parse_column_stats(self, rows: Rows) -> Parsed:
        """Approximate distincts from the statistics histograms."""
        stats = []
        for row in rows:
            (
                schema, table, column, _rows, _rows_sampled, last_updated,
                approx_distinct,
            ) = row
            stats.append(
                ColumnStats(
                    schema=as_text(schema),
                    table=as_text(table),
                    column=as_text(column),
                    distinct_count=as_int(approx_distinct),
                    null_count=None,  # histograms do not carry a null count
                    null_rate=None,
                    stats_date=as_date(last_updated),
                    approximate=True,
                )
            )
        stats.sort(key=lambda s: (s.schema, s.table, s.column))
        return stats, []
