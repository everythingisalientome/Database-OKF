"""The engine seam.

An adapter answers two questions and nothing else: *which catalog block
implements query X on this engine* and *what do the rows that come back
mean*. It never composes SQL, never talks to a driver, and holds no state —
which is why every adapter is unit-testable against canned rows, and why the
Teradata one can be complete and honest about being unverified.

Row shapes are positional, matching the SELECT list of the catalog block, the
way DB-API returns them. Each parse method returns ``(items, warnings)``:
warnings are the things a dictionary can express and the catalog block cannot
carry, and they end up in the crawl result rather than in nobody's inbox.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .. import catalog
from ..catalog import Statement
from ..errors import AdapterError

Row = Sequence[Any]
Rows = Sequence[Row]


@dataclass(frozen=True)
class ParseWarning:
    """Something a row reader could not represent faithfully.

    The schema travels with the message so the crawl can drop warnings
    about schemas it is not cataloging. Statements run verbatim across
    the whole account view, so a reader routinely sees rows for tables
    this run has no interest in, and their warnings would be noise in
    somebody else's bundle.
    """

    message: str
    #: Schema the warning is about; None when it concerns the query.
    schema: str | None = None

    def __str__(self) -> str:
        return self.message


#: A parse method's return: what it read, and what it could not represent.
Parsed = tuple[list, list[ParseWarning]]

TRUE_WORDS = ("yes", "y", "true", "t", "1")
FALSE_WORDS = ("no", "n", "false", "f", "0")


def as_bool(value, *, default: bool | None = None) -> bool | None:
    """Read the several ways engines spell a boolean (``YES``, ``Y``, 1, bit)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().casefold()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    return default


def as_int(value) -> int | None:
    """Int or None — dictionaries return NULL and empty strings alike."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_text(value) -> str | None:
    """Trimmed text or None. Teradata pads CHAR dictionary columns."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def as_float(value) -> float | None:
    """Float or None — for the rates dictionaries report (``null_frac``)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_date(value) -> date | None:
    """A stats collection date, however the driver hands it over.

    Dictionaries return timestamps; the OKF records the day. Keeping the
    clock time would make two crawls of unchanged statistics differ.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).strip().replace(" ", "T")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


class Adapter(ABC):
    """Per-engine row reading for the Tier A catalog blocks."""

    #: Runtime engine key, matching :data:`crawler.catalog.STATEMENTS`.
    engine: str = ""
    #: False when no live system has ever exercised this adapter. A false
    #: here flags the whole bundle UNVERIFIED — see BUILD-PLAN session 6.
    verified: bool = True

    def statement(self, query_id: str) -> Statement | None:
        """The catalog block for ``query_id``, or None when there is no
        block for this engine (a gap, recorded, never improvised around)."""
        return catalog.statement(self.engine, query_id)

    # -- row readers -------------------------------------------------------

    def parse_tables(self, rows: Rows) -> Parsed:
        raise AdapterError(f"{self.engine}: A1 rows have no reader")

    def parse_columns(self, rows: Rows) -> Parsed:
        raise AdapterError(f"{self.engine}: A2 rows have no reader")

    def parse_constraints(self, rows: Rows) -> Parsed:
        raise AdapterError(f"{self.engine}: A3 rows have no reader")

    def parse_indexes(self, rows: Rows) -> Parsed:
        raise AdapterError(f"{self.engine}: A4 rows have no reader")

    def parse_table_stats(self, rows: Rows) -> Parsed:
        raise AdapterError(f"{self.engine}: A6 rows have no table-stats reader")

    def parse_column_stats(self, rows: Rows) -> Parsed:
        raise AdapterError(f"{self.engine}: A6 rows have no column-stats reader")

    def parse_reconciliation(self, rows: Rows) -> tuple[int | None, list]:
        raise AdapterError(f"{self.engine}: A5 rows have no reader")

    # -- Tier B/C: the measuring pass. Shared, see the note below. ---------

    def template(self, key: str):
        """The Tier B/C block for ``key`` on this engine, or None."""
        return catalog.template(self.engine, key)

    def parse_row_count(self, rows: Rows):
        return read_row_count(rows)

    def parse_column_aggregates(self, rows: Rows, columns):
        return read_column_aggregates(rows, columns)

    def parse_column_lengths(self, rows: Rows, columns):
        return read_column_lengths(rows, columns)

    def parse_top_values(self, rows: Rows):
        return read_top_values(rows)

    def parse_format_sample(self, rows: Rows):
        return read_format_sample(rows)

    def parse_value_sample(self, rows: Rows):
        return read_value_sample(rows)


def read_indexes(rows, *, is_included):
    """Read A4 rows beginning ``(schema, table, name, unique, column, ordinal)``.

    PostgreSQL's pg_index block and SQL Server's sys.indexes block open with
    the same six columns in the same order, so they share a reader. They
    differ in how they mark an INCLUDE column — PostgreSQL with a seventh
    boolean, SQL Server with ``key_ordinal = 0`` — so the caller passes a
    predicate over the raw row.

    The distinction is not cosmetic: A4 exists to supply join-intent
    evidence, and an INCLUDE column is payload the index carries rather than
    a column anyone searches on. Counting one as a key column invents
    evidence for a join nobody intended.

    Returns ``(indexes, warnings)`` as :class:`Parsed`.
    """
    from ..results import Index  # local: results imports nothing from here

    prepared, warnings = [], []
    for row in rows:
        schema, table, name, unique, column, ordinal = row[:6]
        index_name = as_text(name)
        if index_name is None:
            warnings.append(
                ParseWarning(
                    f"{as_text(schema)}.{as_text(table)}: A4 returned an index "
                    "row with no index name; skipped",
                    schema=as_text(schema),
                )
            )
            continue
        prepared.append(
            {
                "schema": as_text(schema),
                "table": as_text(table),
                "name": index_name,
                "unique": as_bool(unique, default=False),
                "column": as_text(column),
                "ordinal": as_int(ordinal),
                "included": bool(is_included(row)),
            }
        )

    indexes = []
    for (schema, table, name), group, _duplicates in group_ordered(
        prepared,
        key=lambda r: (r["schema"], r["table"], r["name"]),
        position=lambda r: r["ordinal"],
    ):
        key_columns = tuple(r["column"] for r in group if not r["included"])
        included = tuple(r["column"] for r in group if r["included"])
        indexes.append(
            Index(
                schema=schema,
                table=table,
                name=name,
                unique=bool(group[0]["unique"]),
                columns=key_columns,
                primary_index=False,  # Teradata-only concept
                included_columns=included,
            )
        )
    indexes.sort(key=lambda i: (i.schema, i.table, i.name))
    return indexes, warnings


def group_ordered(rows, key, position):
    """Group rows by ``key(row)``, ordering each group by ``position(row)``.

    Returns ``(key, ordered_rows, duplicate_positions)``. A duplicate position
    inside a group means two different objects were folded together because
    the catalog block does not select the name that would tell them apart —
    the caller turns that into a warning rather than a silent merge.
    """
    groups: dict[Any, list] = {}
    for row in rows:
        groups.setdefault(key(row), []).append(row)
    for group_key, group_rows in groups.items():
        ordered = sorted(group_rows, key=lambda r: (position(r) is None, position(r)))
        positions = [position(r) for r in ordered]
        duplicates = len(positions) != len(set(positions))
        yield group_key, ordered, duplicates

# -- Tier B / Tier C readers ------------------------------------------------
#
# These live on the base class rather than per engine because the catalog
# blocks were written to line up: B1 returns one count, the batched B2 blocks
# return COUNT(*) followed by a fixed number of values per column in the
# column order the statement was built with, B3 returns (value, frequency),
# and C1 returns (normalized, raw). An engine that could not answer in that
# shape would need its own block, and would get its own reader with it.


@dataclass(frozen=True)
class Aggregates:
    """B2's four numbers for one column, plus B2-length's three."""

    non_null_count: int | None = None
    distinct_count: int | None = None
    min_value: Any = None
    max_value: Any = None
    min_length: int | None = None
    max_length: int | None = None
    avg_length: float | None = None

    def with_lengths(self, min_length, max_length, avg_length) -> "Aggregates":
        return Aggregates(
            non_null_count=self.non_null_count,
            distinct_count=self.distinct_count,
            min_value=self.min_value,
            max_value=self.max_value,
            min_length=min_length,
            max_length=max_length,
            avg_length=avg_length,
        )


def read_row_count(rows) -> tuple[int | None, list[ParseWarning]]:
    """B1: one row, one number."""
    rows = list(rows)
    if not rows or not rows[0]:
        return None, [ParseWarning("B1 returned no row; row count unknown")]
    return as_int(rows[0][0]), []


def read_column_aggregates(rows, columns):
    """The batched B2 block: ``(row_count, {column: Aggregates}, warnings)``.

    The result is positional — ``COUNT(*)`` and then four values per column,
    in the order the statement named them — so a row of the wrong width means
    the statement and the column list have drifted apart. That is reported
    and dropped rather than read off by guesswork, because reading it off by
    guesswork would attribute one column's numbers to another.
    """
    columns = list(columns)
    rows = list(rows)
    expected = 1 + 4 * len(columns)
    if not rows:
        return None, {}, [ParseWarning("B2 returned no row; nothing profiled")]
    row = list(rows[0])
    if len(row) != expected:
        return (
            None,
            {},
            [
                ParseWarning(
                    f"B2 returned {len(row)} values for {len(columns)} "
                    f"columns; expected {expected}. No profile is recorded "
                    "for this batch rather than a misaligned one"
                )
            ],
        )
    row_count = as_int(row[0])
    aggregates = {}
    for index, column in enumerate(columns):
        base = 1 + 4 * index
        aggregates[column] = Aggregates(
            non_null_count=as_int(row[base]),
            distinct_count=as_int(row[base + 1]),
            min_value=row[base + 2],
            max_value=row[base + 3],
        )
    return row_count, aggregates, []


def read_column_lengths(rows, columns):
    """The batched B2-length block: ``({column: (min, max, avg)}, warnings)``."""
    columns = list(columns)
    rows = list(rows)
    expected = 3 * len(columns)
    if not rows:
        return {}, [ParseWarning("B2-length returned no row; no length stats")]
    row = list(rows[0])
    if len(row) != expected:
        return {}, [
            ParseWarning(
                f"B2-length returned {len(row)} values for {len(columns)} "
                f"columns; expected {expected}. No length stats are recorded "
                "for this batch rather than misaligned ones"
            )
        ]
    lengths = {}
    for index, column in enumerate(columns):
        base = 3 * index
        lengths[column] = (
            as_int(row[base]),
            as_int(row[base + 1]),
            as_float(row[base + 2]),
        )
    return lengths, []


def read_top_values(rows):
    """B3: ``(value, frequency)`` pairs, in the order the engine returned them.

    Not re-sorted. ``ORDER BY freq DESC`` has no tiebreaker in any of the B3
    blocks, so equal-frequency values arrive in engine order; imposing an
    order here would look canonical without being it.
    """
    pairs, warnings = [], []
    for row in rows:
        if len(row) < 2:
            warnings.append(
                ParseWarning(f"B3 returned a row of width {len(row)}; skipped")
            )
            continue
        frequency = as_int(row[1])
        if frequency is None:
            warnings.append(
                ParseWarning("B3 returned a row with no frequency; skipped")
            )
            continue
        pairs.append((row[0], frequency))
    return pairs, warnings


def read_value_sample(rows):
    """C1: ``(normalized, raw, freq)`` triples, in selection-hash order.

    The order is load-bearing and must not be touched: the rows arrive ranked
    by the engine's own hash of the normalized value, which is what makes the
    first k of them a bottom-k rather than an arbitrary slice.
    """
    triples, warnings = [], []
    for row in rows:
        if not row:
            continue
        value = row[0]
        raw = row[1] if len(row) > 1 else None
        freq = as_int(row[2]) if len(row) > 2 else None
        if value is None:
            warnings.append(
                ParseWarning("C1 returned a NULL normalized value; skipped")
            )
            continue
        triples.append((value, raw, freq))
    return triples, warnings


def read_format_sample(rows):
    """B4: ``(normalized, freq)`` pairs, in selection-hash order."""
    pairs, warnings = [], []
    for row in rows:
        if not row:
            continue
        value = row[0]
        freq = as_int(row[1]) if len(row) > 1 else None
        if value is None:
            warnings.append(
                ParseWarning("B4 returned a NULL normalized value; skipped")
            )
            continue
        pairs.append((value, freq))
    return pairs, warnings
