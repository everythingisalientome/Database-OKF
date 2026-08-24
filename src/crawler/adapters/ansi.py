"""information_schema readers — shared by every ANSI-speaking engine.

A1, A2, A3 and A5 have one catalog block each covering ANSI, SQL Server,
PostgreSQL and MySQL, so their row readers live here once. Engine subclasses
add what information_schema cannot answer: indexes and dictionary statistics.
"""

from __future__ import annotations

from ..results import Column, Constraint, Table
from ..types import canonical_type
from .base import (
    Adapter,
    Parsed,
    ParseWarning,
    Rows,
    as_bool,
    as_int,
    as_text,
    group_ordered,
)

#: Constraint kinds whose columns an FK can reference.
KEY_KINDS = ("PRIMARY KEY", "UNIQUE")


class AnsiAdapter(Adapter):
    """Row readers for the ANSI information_schema catalog blocks."""

    engine = "ansi"

    # -- A1: table inventory ----------------------------------------------

    def parse_tables(self, rows: Rows) -> Parsed:
        tables = []
        for catalog_name, schema, name, kind in rows:
            tables.append(
                Table(
                    schema=as_text(schema),
                    name=as_text(name),
                    kind=(as_text(kind) or "").upper(),
                    catalog=as_text(catalog_name),
                )
            )
        return tables, []

    # -- A2: column inventory ---------------------------------------------

    def parse_columns(self, rows: Rows) -> Parsed:
        columns = []
        for row in rows:
            (
                schema, table, name, ordinal, data_type, char_length,
                precision, scale, is_nullable, default,
            ) = row
            length = as_int(char_length)
            precision = as_int(precision)
            scale = as_int(scale)
            columns.append(
                Column(
                    schema=as_text(schema),
                    table=as_text(table),
                    name=as_text(name),
                    ordinal=as_int(ordinal),
                    type=canonical_type(
                        data_type, length=length, precision=precision, scale=scale
                    ),
                    raw_type=as_text(data_type),
                    nullable=as_bool(is_nullable, default=True),
                    length=length,
                    precision=precision,
                    scale=scale,
                    default=as_text(default),
                )
            )
        return columns, []

    # -- A3: declared constraints -----------------------------------------

    def parse_constraints(self, rows: Rows) -> Parsed:
        """Fold the per-column rows back into constraints, targets resolved.

        The block names each constraint and joins through
        ``referential_constraints`` to the referenced constraint's table, so
        a foreign key comes back with its target table. The target *columns*
        are resolved here rather than in SQL, per the catalog's note:
        ``position_in_unique_constraint`` is absent from SQL Server's
        information_schema, and this is the block SQL Server runs. So the
        referenced constraint is looked up among the PRIMARY KEY and UNIQUE
        rows of this same result and paired by ordinal position.

        A constraint whose target is not in the result — the referenced table
        sits in a schema this account cannot see — keeps the target table it
        was given and reports empty target columns with a warning. A guessed
        column list would be indistinguishable from a measured one.
        """
        constraints, warnings = [], []
        prepared = [
            {
                "kind": (as_text(kind) or "").upper(),
                "schema": as_text(schema),
                "table": as_text(table),
                "name": as_text(name),
                "column": as_text(column),
                "ordinal": as_int(ordinal),
                "unique_schema": as_text(unique_schema),
                "unique_name": as_text(unique_name),
                "referenced_schema": as_text(referenced_schema),
                "referenced_table": as_text(referenced_table),
            }
            for (
                kind, schema, table, name, column, ordinal,
                unique_schema, unique_name, referenced_schema, referenced_table,
            ) in rows
        ]

        grouped = group_ordered(
            prepared,
            key=lambda r: (r["schema"], r["table"], r["kind"], r["name"]),
            position=lambda r: r["ordinal"],
        )
        built = []
        for (schema, table, kind, name), group, duplicates in grouped:
            if duplicates:
                warnings.append(
                    ParseWarning(
                        f"{schema}.{table}: constraint {name or 'unnamed'} "
                        f"reports {len(group)} columns sharing an ordinal "
                        "position; reported as single-column constraints "
                        "rather than merged into a composite",
                        schema=schema,
                    )
                )
                built.extend(
                    (
                        Constraint(
                            kind=kind,
                            schema=schema,
                            table=table,
                            columns=(row["column"],),
                            name=name,
                            referenced_constraint=row["unique_name"],
                            referenced_table=_qualify(
                                row["referenced_schema"], row["referenced_table"]
                            ),
                        ),
                        row,
                    )
                    for row in group
                )
                continue
            built.append(
                (
                    Constraint(
                        kind=kind,
                        schema=schema,
                        table=table,
                        columns=tuple(r["column"] for r in group),
                        name=name,
                        referenced_constraint=group[0]["unique_name"],
                        referenced_table=_qualify(
                            group[0]["referenced_schema"],
                            group[0]["referenced_table"],
                        ),
                    ),
                    group[0],
                )
            )

        # Index the key constraints by the name a foreign key refers to them
        # by. A constraint lives in its table's schema on every engine that
        # runs this block, so the table schema is the constraint schema.
        keys = {
            (constraint.schema, constraint.name): constraint
            for constraint, _row in built
            if constraint.kind in KEY_KINDS and constraint.name
        }

        for constraint, row in built:
            if constraint.kind != "FOREIGN KEY" or not constraint.referenced_constraint:
                constraints.append(constraint)
                continue
            target = keys.get(
                (row["unique_schema"], constraint.referenced_constraint)
            ) or keys.get(
                (row["referenced_schema"], constraint.referenced_constraint)
            )
            if target is None:
                warnings.append(
                    ParseWarning(
                        f"{constraint.schema}.{constraint.table}: foreign key "
                        f"{constraint.name} references constraint "
                        f"{constraint.referenced_constraint}, which A3 did "
                        "not return — target columns unresolved (the "
                        "referenced table is probably outside this account's "
                        "grants)",
                        schema=constraint.schema,
                    )
                )
                constraints.append(constraint)
                continue
            constraints.append(
                Constraint(
                    kind=constraint.kind,
                    schema=constraint.schema,
                    table=constraint.table,
                    columns=constraint.columns,
                    name=constraint.name,
                    referenced_constraint=constraint.referenced_constraint,
                    referenced_table=(
                        constraint.referenced_table
                        or _qualify(target.schema, target.table)
                    ),
                    referenced_columns=target.columns[: len(constraint.columns)],
                )
            )

        constraints.sort(key=lambda c: (c.schema, c.table, c.kind, c.columns))
        return constraints, warnings

    # -- A5: reconciliation -----------------------------------------------

    def parse_reconciliation(self, rows: Rows) -> tuple[int | None, list]:
        rows = list(rows)
        if not rows or not rows[0]:
            return None, [
                ParseWarning("A5 returned no row; visible table count unknown")
            ]
        return as_int(rows[0][0]), []


def _qualify(schema, table) -> str | None:
    """``schema.table``, or None when the dictionary gave neither."""
    if not table:
        return None
    return f"{schema}.{table}" if schema else table
