"""A fake database with rows in it, for the measuring pass.

:mod:`fakes` replays canned rows for exact statement text, which is the right
shape for Tier A: the rows are dictionary output and writing them out is the
test. Tier B and C compute things *from data*, so canning their answers would
be canning the very numbers under test.

This module holds the data instead. :class:`FakeDatabase` is given tables of
Python values, works out what each Tier B/C block would return over them, and
registers those rows against the statement text the crawler will actually
issue — by binding the catalog templates through the same
:func:`crawler.bind.bind` the crawler uses. The strictness of
:class:`fakes.FakeConnection` still applies underneath: SQL the fake was not
given is an assertion failure, so a crawler that composed a statement of its
own fails every test here.

Its aggregate semantics are a database with a binary collation: codepoint
MIN/MAX, case-sensitive DISTINCT. That is what the rig is configured for and
what the fixture bundles were built under, and it is the only setting in which
two engines agree about what the minimum of a text column is.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from crawler import catalog
from crawler.bind import batches, bind
from fakes import FakeConnection


@dataclass
class FakeTable:
    """One table: column names in order, and rows as Python tuples."""

    columns: tuple[str, ...]
    rows: list[tuple]
    #: Column name -> True when the crawler will treat it as character-typed,
    #: which decides B2-length and which C1 block runs.
    character: set = field(default_factory=set)

    def values(self, column):
        index = self.columns.index(column)
        return [row[index] for row in self.rows]

    def non_null(self, column):
        return [v for v in self.values(column) if v is not None]


def _text(value) -> str:
    return "" if value is None else str(value)


def _normalize(value) -> str:
    return _text(value).strip().upper()


class FakeDatabase:
    """Tables of values, and the Tier B/C answers they imply."""

    def __init__(self, tables: dict[tuple[str, str], FakeTable]):
        self.tables = tables

    # -- what each block returns -------------------------------------------

    def row_count(self, table: FakeTable):
        return [(len(table.rows),)]

    def aggregates(self, table: FakeTable, columns):
        row = [len(table.rows)]
        for column in columns:
            present = table.non_null(column)
            row.extend(
                [
                    len(present),
                    len(set(present)),
                    min(present) if present else None,
                    max(present) if present else None,
                ]
            )
        return [tuple(row)]

    def lengths(self, table: FakeTable, columns):
        row = []
        for column in columns:
            sizes = [len(_text(v)) for v in table.non_null(column)]
            row.extend(
                [
                    min(sizes) if sizes else None,
                    max(sizes) if sizes else None,
                    (sum(sizes) / len(sizes)) if sizes else None,
                ]
            )
        return [tuple(row)]

    def top_values(self, table: FakeTable, column):
        """``GROUP BY`` then ``ORDER BY freq DESC``, ties in first-seen order.

        Python's sort is stable, so this is one honest reading of a statement
        with no tiebreaker: the engine's grouping order survives among equal
        frequencies. A real engine may pick another, which is exactly why
        nothing downstream asserts the tie order.
        """
        counts: dict = {}
        for value in table.non_null(column):
            counts[value] = counts.get(value, 0) + 1
        ordered = sorted(counts.items(), key=lambda kv: -kv[1])
        return [(value, frequency) for value, frequency in ordered[: catalog.TOP_N]]

    def value_sample(self, table: FakeTable, column):
        """Bottom-k by the selection hash: one raw representative and the
        group's row count, exactly as the freq-carrying C1 blocks return."""
        groups: dict[str, list] = {}
        for value in table.non_null(column):
            groups.setdefault(_normalize(value), []).append(_text(value))
        ranked = sorted(
            groups.items(),
            key=lambda kv: hashlib.md5(kv[0].encode("utf-8")).hexdigest(),
        )
        return [
            (v, min(raws), len(raws)) for v, raws in ranked[: catalog.SAMPLE_CAP]
        ]

    def format_sample(self, table: FakeTable, column):
        """B4: the same grouping, capped at the format ceiling, values only."""
        return [
            (v, freq)
            for v, _raw, freq in self.value_sample(table, column)[
                : catalog.FORMAT_CAP
            ]
        ]

    # -- wiring ------------------------------------------------------------

    def connection(self, engine: str, allowlist, *, batch_columns: int = 20):
        """A :class:`fakes.FakeConnection` answering every statement the pass
        can issue over these tables, and nothing else."""
        responses: dict[str, list] = {}
        for (schema, name), table in self.tables.items():
            self._register(responses, engine, allowlist, schema, name, table,
                           batch_columns)
        return FakeConnection(responses)

    def _register(
        self, responses, engine, allowlist, schema, name, table, batch_columns
    ):
        def add(key, rows, **kwargs):
            template = catalog.template(engine, key)
            if template is None:
                return
            responses[
                bind(template, allowlist, schema=schema, table=name, **kwargs)
            ] = rows

        add("B1", self.row_count(table))
        for batch in batches(list(table.columns), batch_columns):
            add("B2", self.aggregates(table, batch), columns=batch)
        character = [c for c in table.columns if c in table.character]
        for batch in batches(character, batch_columns):
            add("B2-length", self.lengths(table, batch), columns=batch)
        for column in table.columns:
            add("B3", self.top_values(table, column), column=column)
            add("B4", self.format_sample(table, column), column=column)
            key = "C1" if column in table.character else "C1-cast"
            add(key, self.value_sample(table, column), column=column)


__all__ = ["FakeDatabase", "FakeTable"]
