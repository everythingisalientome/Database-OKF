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

from ..results import CodeObject, ColumnStats, ExternalReference, TableStats
from .ansi import AnsiAdapter
from .base import Parsed, Rows, as_date, as_int, as_text, read_indexes

#: sys.index_columns.key_ordinal is 0 for INCLUDE columns — they are payload,
#: not key columns, and must not be read as join-intent evidence.
INCLUDED_COLUMN_ORDINAL = 0

#: sys.objects.type codes the A7 block selects.
OBJECT_TYPES = {
    "V": "VIEW",
    "P": "PROCEDURE",
    "FN": "FUNCTION",   # scalar
    "TF": "FUNCTION",   # table-valued
    "IF": "FUNCTION",   # inline table-valued
    "TR": "TRIGGER",
}


class SqlServerAdapter(AnsiAdapter):
    engine = "sqlserver"
    verified = True

    # -- A4: indexes -------------------------------------------------------

    def parse_indexes(self, rows: Rows) -> Parsed:
        return read_indexes(
            rows,
            is_included=lambda row: as_int(row[5]) == INCLUDED_COLUMN_ORDINAL,
        )

    # -- A7: code-object definitions ---------------------------------------

    def parse_code_objects(self, rows: Rows, *, key: str = "A7") -> Parsed:
        """Views, procedures, functions and triggers from sys.sql_modules.

        ``definition`` is NULL for an encrypted module; the object is still
        recorded and the extractor counts it unparsed (``no-definition``)
        rather than reading nothing silently.
        """
        objects = [
            CodeObject(
                schema=as_text(schema),
                name=as_text(name),
                kind=OBJECT_TYPES.get(
                    (as_text(object_type) or "").upper(), "OBJECT"
                ),
                definition=definition if isinstance(definition, str) else None,
            )
            for schema, name, object_type, definition in rows
        ]
        return objects, []

    # -- A8: cross-database references -------------------------------------

    def parse_external_references(self, rows: Rows, *, key: str = "A8") -> Parsed:
        """Linked servers (``A8``) and synonyms (``A8-synonyms``).

        The synonyms block cannot filter for cross-database targets in SQL
        the way Oracle's ``WHERE db_link IS NOT NULL`` does, so the reader
        does it: only a base object of three or more parts —
        ``db.schema.object`` or ``server.db.schema.object`` — is recorded
        lineage. A one- or two-part base object is a local alias, which A8
        is not in the business of cataloging.
        """
        references = []
        if key == "A8-synonyms":
            for schema, name, base_object in rows:
                target = as_text(base_object) or ""
                parts = [p for p in target.replace("[", "").replace("]", "").split(".")]
                if len(parts) < 3:
                    continue
                references.append(
                    ExternalReference(
                        target=target,
                        kind="synonym",
                        source=f"{as_text(schema)}.{as_text(name)}",
                    )
                )
        else:
            for name, data_source, _provider in rows:
                references.append(
                    ExternalReference(
                        target=as_text(name) or "",
                        kind="linked-server",
                        source="sys.servers",
                        detail=as_text(data_source),
                    )
                )
        return references, []

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
