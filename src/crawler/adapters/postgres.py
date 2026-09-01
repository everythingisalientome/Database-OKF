"""PostgreSQL adapter.

A1, A2, A3 and A5 come from information_schema through the ANSI blocks. A4
reads pg_index and A6 reads pg_class outer-joined to pg_stats, both from
their own PostgreSQL catalog blocks. A6 drives from pg_class so that a table
with no column statistics — an empty one, most often — still reports its row
estimate; pg_stats alone holds nothing at all for such a table.

pg_stats needs interpreting rather than copying, and the catalog spells out
how (its "Interpretation rules (crawler MUST apply)"):

* ``n_distinct >= 0`` is a count; ``n_distinct < 0`` is the negated *ratio*
  of distinct values to rows and has to be multiplied by the row estimate.
  Storing -0.85 as a distinct count would put every wide column below every
  cardinality gate in the system.
* ``n_distinct = 0`` means the planner has no estimate — recorded as unknown,
  not as zero distinct values.
* ``null_frac`` is already a rate and is stored as one.
* ``reltuples`` is an estimate, recorded as ``row_count_source:
  stats-estimate`` rather than ``stats``; ``reltuples = -1`` means the table
  was never analysed, which is a missing-stats signal and not a row count of
  minus one.
"""

from __future__ import annotations

from ..results import (
    STATS_ESTIMATE,
    CodeObject,
    ColumnStats,
    ExternalReference,
    TableStats,
)
from .ansi import AnsiAdapter
from .base import (
    Parsed,
    Rows,
    as_bool,
    as_date,
    as_float,
    as_int,
    as_text,
    read_indexes,
)

#: pg_class.reltuples for a table that has never been analysed.
NEVER_ANALYSED = -1

#: pg_stats.n_distinct when the planner has no estimate at all.
NO_ESTIMATE = 0


class PostgresAdapter(AnsiAdapter):
    engine = "postgres"
    verified = True

    # -- A4: indexes -------------------------------------------------------

    def parse_indexes(self, rows: Rows) -> Parsed:
        """Indexes from pg_index.

        The block's seventh column is ``k.ordinality > ix.indnkeyatts`` —
        true for an INCLUDE column, which is payload rather than join
        intent. One documented omission remains, the catalog block's:
        expression-index members are dropped by its pg_attribute join, an
        expression not being a join column. Composite key order is preserved.
        """
        return read_indexes(rows, is_included=lambda row: as_bool(row[6], default=False))

    # -- A6: dictionary statistics ----------------------------------------

    def parse_table_stats(self, rows: Rows) -> Parsed:
        """Row estimates, one per table, from pg_class.reltuples."""
        seen: dict[tuple[str, str], TableStats] = {}
        for schema, table, _column, _n_distinct, _null_frac, est_rows, stats_date in rows:
            key = (as_text(schema), as_text(table))
            if key in seen:
                continue
            estimate = as_int(est_rows)
            if estimate is None or estimate == NEVER_ANALYSED:
                # Never analysed: no statistics, rather than a row count.
                continue
            seen[key] = TableStats(
                schema=key[0],
                table=key[1],
                row_count=estimate,
                source=STATS_ESTIMATE,
                stats_date=as_date(stats_date),
            )
        return sorted(seen.values(), key=lambda s: (s.schema, s.table)), []

    # -- A7: code-object definitions ---------------------------------------

    def parse_code_objects(self, rows: Rows, *, key: str = "A7") -> Parsed:
        """Views from information_schema.views (``A7``) and routines from
        information_schema.routines (``A7-routines``). A routine whose
        ``routine_definition`` is NULL — a C-language or otherwise opaque
        function — is recorded with no definition; the extractor counts it
        unparsed rather than pretending it read anything."""
        objects, warnings = [], []
        for row in rows:
            if key == "A7-routines":
                schema, name, routine_type, definition = row
                kind = (as_text(routine_type) or "ROUTINE").upper()
            else:
                schema, name, definition = row
                kind = "VIEW"
            objects.append(
                CodeObject(
                    schema=as_text(schema),
                    name=as_text(name),
                    kind=kind,
                    definition=definition if isinstance(definition, str) else None,
                )
            )
        return objects, warnings

    # -- A8: cross-database references -------------------------------------

    def parse_external_references(self, rows: Rows, *, key: str = "A8") -> Parsed:
        """Foreign servers from pg_foreign_server — recorded lineage only."""
        references = [
            ExternalReference(
                target=as_text(srvname) or "",
                kind="foreign-server",
                source="pg_foreign_server",
                detail=as_text(fdwname),
            )
            for srvname, fdwname in rows
        ]
        return references, []

    def parse_column_stats(self, rows: Rows) -> Parsed:
        """Per-column distinct estimates and null rates from pg_stats."""
        stats = []
        for schema, table, column, n_distinct, null_frac, est_rows, stats_date in rows:
            name = as_text(column)
            if name is None:
                # A row with no column name describes the table, not a
                # column; parse_table_stats is what reads those.
                continue
            stats.append(
                ColumnStats(
                    schema=as_text(schema),
                    table=as_text(table),
                    column=name,
                    distinct_count=distinct_from_n_distinct(
                        as_float(n_distinct), as_int(est_rows)
                    ),
                    null_count=None,  # pg_stats reports a fraction, not a count
                    null_rate=as_float(null_frac),
                    stats_date=as_date(stats_date),
                    approximate=True,
                )
            )
        stats.sort(key=lambda s: (s.schema, s.table, s.column))
        return stats, []


def distinct_from_n_distinct(n_distinct, est_rows) -> int | None:
    """Turn pg_stats.n_distinct into a distinct count, or None if unknowable."""
    if n_distinct is None or n_distinct == NO_ESTIMATE:
        return None
    if n_distinct > 0:
        return int(n_distinct)
    if est_rows is None or est_rows <= 0:
        # A ratio with nothing to multiply it by says nothing.
        return None
    return int(round(-n_distinct * est_rows))
