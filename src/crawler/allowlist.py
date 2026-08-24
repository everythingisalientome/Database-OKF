"""The identifier allow-list — the crawler's no-dynamic-SQL enforcement point.

Tier A takes no parameters. Tier B and C are templates with ``{schema}``,
``{table}`` and ``{column}`` holes, and the rule (specs/00 decision 1) is that
nothing may fill a hole unless the crawl's own A1/A2 output produced it. This
class is that rule, built from the inventory the crawl just read and carried
in the crawl result so the measuring pass cannot widen it.

Two independent checks have to pass before an identifier is admitted:

1. **Observed** — A1/A2 returned it for this database. A name from config, a
   name from a previous crawl, or a name a caller typed is not observed.
2. **Bare** — it matches :data:`BARE_IDENTIFIER`, so interpolating it cannot
   change the shape of the statement. Real dictionaries can hold names that
   need quoting; those are *not* admitted, they are recorded in
   :attr:`AllowList.rejected` and reported. Refusing to profile a handful of
   oddly-named tables and saying so is the cheap failure. Emitting SQL built
   from an unvetted dictionary string is the expensive one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import AllowListError

#: An identifier safe to interpolate without quoting on every target engine.
#: ``$`` and ``#`` are legal in Oracle and Teradata names and appear in real
#: legacy schemas, so they are allowed after the first character.
BARE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")

#: Longest identifier any supported engine accepts (Oracle 12.2+, DB2, MSSQL).
MAX_IDENTIFIER_LENGTH = 128


def is_bare_identifier(name) -> bool:
    """True when ``name`` can be interpolated into SQL without quoting."""
    if not isinstance(name, str) or not name:
        return False
    if len(name) > MAX_IDENTIFIER_LENGTH:
        return False
    return BARE_IDENTIFIER.match(name) is not None


@dataclass(frozen=True)
class Rejection:
    """An observed identifier that may not be interpolated, and why."""

    identifier: str
    reason: str

    def to_obj(self) -> dict:
        return {"identifier": self.identifier, "reason": self.reason}

    @classmethod
    def from_obj(cls, obj: dict) -> Rejection:
        return cls(obj["identifier"], obj["reason"])


@dataclass
class AllowList:
    """Identifiers this crawl observed and may interpolate.

    ``tables`` maps ``schema -> table -> (column, ...)``, keeping the exact
    spelling the dictionary reported. Lookups match exactly first; if that
    fails, a case-insensitive match is accepted only when it is unique, and
    returns the observed spelling. The fallback never invents a name — it
    just spares callers from guessing whether this engine folds case.
    """

    tables: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)
    rejected: list[Rejection] = field(default_factory=list)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_inventory(cls, tables, columns) -> AllowList:
        """Build from A1 :class:`~crawler.results.Table` and A2
        :class:`~crawler.results.Column` rows."""
        allow = cls()
        for table in tables:
            allow._admit_table(table.schema, table.name)
        for column in columns:
            allow._admit_column(column.schema, column.table, column.name)
        return allow

    def _reject(self, identifier: str, reason: str) -> None:
        rejection = Rejection(identifier, reason)
        if rejection not in self.rejected:
            self.rejected.append(rejection)

    def _admit_table(self, schema: str, table: str) -> bool:
        for part, kind in ((schema, "schema"), (table, "table")):
            if not is_bare_identifier(part):
                self._reject(
                    f"{schema}.{table}",
                    f"{kind} name is not a bare identifier; not interpolatable",
                )
                return False
        self.tables.setdefault(schema, {}).setdefault(table, ())
        return True

    def _admit_column(self, schema: str, table: str, column: str) -> None:
        if schema not in self.tables or table not in self.tables[schema]:
            # A2 knows a table A1 did not return. Admitting it here would
            # widen the allow-list past the table inventory, so it is
            # recorded instead.
            self._reject(
                f"{schema}.{table}.{column}",
                "column belongs to a table the A1 inventory did not return",
            )
            return
        if not is_bare_identifier(column):
            self._reject(
                f"{schema}.{table}.{column}",
                "column name is not a bare identifier; not interpolatable",
            )
            return
        existing = self.tables[schema][table]
        if column not in existing:
            self.tables[schema][table] = existing + (column,)

    # -- lookup ------------------------------------------------------------

    @staticmethod
    def _match(name: str, candidates) -> str:
        if name in candidates:
            return name
        folded = [c for c in candidates if c.casefold() == str(name).casefold()]
        if len(folded) == 1:
            return folded[0]
        if len(folded) > 1:
            raise AllowListError(
                f"{name!r} matches {len(folded)} observed identifiers "
                f"case-insensitively ({', '.join(sorted(folded))}); "
                "spell it exactly"
            )
        raise LookupError(name)

    def schema(self, schema: str) -> str:
        """The observed spelling of ``schema``, or raise."""
        try:
            return self._match(schema, self.tables)
        except LookupError:
            raise AllowListError(
                f"schema {schema!r} was not observed by this crawl"
            ) from None

    def table(self, schema: str, table: str) -> tuple[str, str]:
        """The observed ``(schema, table)`` spelling, or raise."""
        observed_schema = self.schema(schema)
        try:
            return observed_schema, self._match(table, self.tables[observed_schema])
        except LookupError:
            raise AllowListError(
                f"table {schema}.{table} was not observed by this crawl"
            ) from None

    def column(self, schema: str, table: str, column: str) -> tuple[str, str, str]:
        """The observed ``(schema, table, column)`` spelling, or raise."""
        observed_schema, observed_table = self.table(schema, table)
        try:
            observed_column = self._match(
                column, self.tables[observed_schema][observed_table]
            )
        except LookupError:
            raise AllowListError(
                f"column {schema}.{table}.{column} was not observed by this crawl"
            ) from None
        return observed_schema, observed_table, observed_column

    def qualify(self, schema: str, table: str) -> str:
        """``schema.table`` in its observed spelling — the interpolation form."""
        return "{}.{}".format(*self.table(schema, table))

    # -- inspection --------------------------------------------------------

    @property
    def schemas(self) -> tuple[str, ...]:
        return tuple(sorted(self.tables))

    def table_names(self, schema: str | None = None) -> tuple[str, ...]:
        """Qualified names of every admitted table, sorted."""
        names = [
            f"{s}.{t}"
            for s, tabs in self.tables.items()
            for t in tabs
            if schema is None or s == schema
        ]
        return tuple(sorted(names))

    def column_names(self, schema: str, table: str) -> tuple[str, ...]:
        observed_schema, observed_table = self.table(schema, table)
        return self.tables[observed_schema][observed_table]

    def __contains__(self, item) -> bool:
        """``"schema.table" in allowlist`` / ``("s", "t", "c") in allowlist``."""
        try:
            if isinstance(item, str):
                parts = item.split(".")
            else:
                parts = list(item)
            if len(parts) == 2:
                self.table(*parts)
            elif len(parts) == 3:
                self.column(*parts)
            else:
                return False
        except AllowListError:
            return False
        return True

    def __len__(self) -> int:
        return sum(len(tabs) for tabs in self.tables.values())

    # -- serialisation -----------------------------------------------------

    def to_obj(self) -> dict:
        return {
            "tables": {
                schema: {table: list(cols) for table, cols in sorted(tabs.items())}
                for schema, tabs in sorted(self.tables.items())
            },
            "rejected": [r.to_obj() for r in self.rejected],
        }

    @classmethod
    def from_obj(cls, obj: dict) -> AllowList:
        return cls(
            tables={
                schema: {table: tuple(cols) for table, cols in tabs.items()}
                for schema, tabs in obj.get("tables", {}).items()
            },
            rejected=[Rejection.from_obj(o) for o in obj.get("rejected", [])],
        )
