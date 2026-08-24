"""Derived numbers, flags and gates — everything the measuring pass decides.

B1, B2, B3 and C1 return raw aggregates. This module turns them into what the
OKF publishes and what step 2 gates on, and it is deliberately separate from
:mod:`crawler.measure` so that every rule here can be tested against the
fixture bundles without a database in the room.

Three families of decision live here.

**Rates.** ``null_rate`` is against the row count and ``distinct_ratio``
against the non-null count. Different denominators, on purpose: a column that
is 83% NULL with ten distinct values in the rest is a ten-value code list with
a lot of NULLs, and dividing its distinct count by the row count would report
a distinct_ratio of 0.17 and hide that. Both are rounded to four places,
which is what the fixture bundles carry.

**Flags.** ``dense_sequence`` marks an integer column whose distinct values
fill a contiguous range starting at 1 — a generic surrogate key, whose value
overlap with another such column is an artifact of both being 1..n rather than
evidence of a join (specs/01 step 3; specs/00 decision 5). ``junk-suspect``
marks a table that is empty or named like a backup.

**Gates.** Which columns get a fingerprint (distinct_ratio above the gate, or
membership of any index or constraint) and which get top-N values (few enough
distinct values to characterise). Both are what keeps a two-value status code
out of step 2's join candidates and a million-value free-text column out of
the bundle. Sensitive-listed columns pass neither, ever.

Every refusal is recorded rather than implied: a column with no fingerprint
carries the reason it has none, because "not a join candidate" and
"suppressed for compliance" are different facts about a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .results import (
    GATE_CARDINALITY,
    GATE_DISTINCT,
    LENGTH_PRECISION,
    RATE_PRECISION,
    SENSITIVE,
    TopValue,
)

#: Canonical types whose values are whole numbers, for ``dense_sequence``.
#: DECIMAL and NUMBER are here because a legacy surrogate key is routinely
#: ``DECIMAL(18,0)`` — the specs/01 table-format example is exactly that.
INTEGER_TYPES = ("INT", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT")
SCALE_ZERO_TYPES = ("DECIMAL", "NUMERIC", "NUMBER")

#: Canonical types measured in characters, so length statistics mean
#: something. B2's length block runs for these and no others.
CHARACTER_TYPES = ("CHAR", "NCHAR", "VARCHAR", "NVARCHAR", "TEXT", "CLOB")

#: Canonical types rendered through the catalog's canonical temporal
#: rendering (adopted P15, :mod:`crawler.temporal`) before any value work:
#: fingerprints, min/max, top-N and length statistics all see the rendered
#: form, so two engines' crawls of the same instant hash the same string.
TEMPORAL_TYPES = (
    "DATE",
    "TIME",
    "TIME WITH TIME ZONE",
    "TIMESTAMP",
    "TIMESTAMP WITH TIME ZONE",
)


def _base_type(canonical: str | None) -> str:
    """``VARCHAR(160)`` -> ``VARCHAR``."""
    if not canonical:
        return ""
    return canonical.split("(", 1)[0].strip().upper()


def is_integer_type(column) -> bool:
    base = _base_type(column.type)
    if base in INTEGER_TYPES:
        return True
    return base in SCALE_ZERO_TYPES and (column.scale or 0) == 0


def is_character_type(column) -> bool:
    return _base_type(column.type) in CHARACTER_TYPES


def is_temporal_type(column) -> bool:
    return _base_type(column.type) in TEMPORAL_TYPES


# -- rates and flags --------------------------------------------------------


def null_rate(null_count, row_count):
    """Nulls over rows. None when either is unknown, or the table is empty —
    a rate over zero rows is not zero, it is undefined."""
    if null_count is None or not row_count:
        return None
    return round(null_count / row_count, RATE_PRECISION)


def distinct_ratio(distinct_count, non_null_count):
    """Distinct values over non-null values (the catalog's own definition)."""
    if distinct_count is None or not non_null_count:
        return None
    return round(distinct_count / non_null_count, RATE_PRECISION)


def average_length(total_or_avg):
    if total_or_avg is None:
        return None
    return round(float(total_or_avg), LENGTH_PRECISION)


def dense_sequence(
    column,
    distinct_count,
    min_value,
    max_value,
    *,
    fill: float = 0.95,
    start_max: int = 1,
    minimum: int = 2,
) -> bool:
    """True for an integer column whose values fill a range starting near 1.

    specs/01 step 3. The flag is a warning label for step 2: two dense
    sequences overlap heavily by construction, so their measured overlap says
    nothing about whether they refer to the same thing. ``start_max`` is what
    "starting near 1" is pinned to — a foreign key holding only 3, 4 and 5 is
    a contiguous run of three values and not a surrogate sequence, and the
    fixture bundles agree (``customer.support_rep_id`` carries no flag).
    ``minimum`` rules out the degenerate range: ``invoice_line.quantity`` is
    every row holding the value 1, which fills [1 .. 1] perfectly and is a
    constant rather than a sequence — the fixtures leave it unflagged too.
    """
    if not is_integer_type(column) or distinct_count is None:
        return False
    low, high = _as_int(min_value), _as_int(max_value)
    if low is None or high is None or high < low:
        return False
    if low > start_max or distinct_count < minimum:
        return False
    span = high - low + 1
    return span > 0 and distinct_count / span >= fill


def _as_int(value):
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def is_junk_table(name: str, row_count, patterns) -> bool:
    """specs/01 step 2: empty, or named like a backup or a scratch copy."""
    if row_count == 0:
        return True
    folded = str(name).casefold()
    return any(str(p).casefold() in folded for p in patterns)


# -- top-N ------------------------------------------------------------------


def top_values(rows, non_null_count, limit) -> tuple[TopValue, ...]:
    """B3's rows as :class:`~crawler.results.TopValue`, percentages derived.

    The order is B3's own. Its ``ORDER BY freq DESC`` has no tiebreaker, so
    equal-frequency values come back in whatever order the engine grouped
    them, and re-sorting here would replace one arbitrary order with another
    while pretending to be canonical. The frequencies are what carry meaning;
    the tie order does not, and is documented as not doing so.
    """
    values = []
    for row in rows[:limit]:
        value, frequency = row[0], row[1]
        frequency = int(frequency)
        percent = (
            round(100 * frequency / non_null_count) if non_null_count else 0
        )
        values.append(
            TopValue(value=_render(value), frequency=frequency, percent=percent)
        )
    return tuple(values)


def _render(value) -> str:
    """One value as the OKF carries it. NULLs never reach here — B3 filters
    them — so a None is a driver quirk and is recorded as the empty string
    rather than the four letters of ``None``."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# -- gates ------------------------------------------------------------------


@dataclass(frozen=True)
class Gate:
    """A gate's verdict: run it, or don't and say why."""

    allowed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


def fingerprint_gate(
    column,
    *,
    ratio,
    indexed: bool,
    sensitive: bool,
    threshold: float = 0.5,
) -> Gate:
    """Whether C1 runs for this column.

    The catalog's gate: ``distinct_ratio > 0.5`` or membership of any index
    or constraint from A3/A4. It is the cardinality gate that stops a
    two-value status code from becoming a join candidate — and the index
    clause is why a low-ratio foreign key like ``track.genre_id`` is
    fingerprinted anyway: somebody built an index on it, which is the
    join-intent evidence declared constraints failed to give.
    """
    if sensitive:
        return Gate(False, SENSITIVE)
    if indexed:
        return Gate(True)
    if ratio is not None and ratio > threshold:
        return Gate(True)
    return Gate(False, GATE_CARDINALITY)


def top_n_gate(
    *,
    distinct_count,
    sensitive: bool,
    maximum: int = 30,
) -> Gate:
    """Whether B3 runs for this column.

    Top-N characterises a code list. A long value list characterises nothing,
    and storing it would put raw values in the bundle for no gain.
    """
    if sensitive:
        return Gate(False, SENSITIVE)
    if distinct_count is None:
        return Gate(False, GATE_DISTINCT)
    if distinct_count <= maximum:
        return Gate(True)
    return Gate(False, GATE_DISTINCT)


# -- stats-first ------------------------------------------------------------


def stats_are_fresh(stats_date, today: date, max_age_days: int) -> bool:
    """Dictionary statistics collected recently enough to be believed."""
    if stats_date is None:
        return False
    return stats_date >= today - timedelta(days=max_age_days)


def near_gate_boundary(
    *,
    ratio,
    distinct_count,
    ratio_gate: float = 0.5,
    band: float = 0.15,
    distinct_gate: int = 30,
    factor: float = 2.0,
) -> bool:
    """True when an estimate is close enough to a gate to be worth measuring.

    specs/01 step 1: histogram- and sample-derived distincts are "ample for
    gating, and B2 still runs where one lands near a gate boundary". Near is
    within ``band`` of the ratio gate, or within a factor of the distinct
    gate — a column the dictionary believes has 28 distinct values is one
    collection cycle away from being on the wrong side of the top-N gate.
    """
    if ratio is not None and abs(ratio - ratio_gate) <= band:
        return True
    if distinct_count is not None and factor > 0:
        return distinct_gate / factor <= distinct_count <= distinct_gate * factor
    return False


def needs_row_scan(stats, today: date, *, max_age_days: int, trust_estimates: bool):
    """Whether B1 runs for a table, and the reason either way.

    Returns ``(needed, reason)``. The estimate case is the interesting one:
    PostgreSQL's ``reltuples`` is the planner's belief, and specs/01 calls row
    counts load-bearing — rate denominators, overlap confidence weight,
    junk-table filter, step 3 sequencing. A belief is not a denominator, so
    by default the crawler counts. ``trust_estimates`` turns that off for
    estates where a full count is not affordable.
    """
    from .results import STATS, STATS_ESTIMATE

    if stats is None:
        return True, "no dictionary row count"
    if stats.source == STATS_ESTIMATE:
        if trust_estimates:
            return False, "dictionary estimate trusted by config"
        return True, "dictionary row count is a planner estimate"
    if stats.source == STATS and not stats_are_fresh(
        stats.stats_date, today, max_age_days
    ):
        return True, f"dictionary row count older than {max_age_days} days"
    return False, "fresh dictionary row count"


def needs_column_scan(stats, today: date, *, max_age_days: int, **boundary):
    """Whether B2 runs for a column, and the reason either way.

    The stats-first policy skips a scan when the dictionary already knows the
    answer. It only knows it when the answer is a *count*: PostgreSQL's
    ``n_distinct`` and SQL Server's histogram sums are approximations, and
    specs/01 is explicit that approximations are for gating rather than
    publishing. So an approximate statistic plans the scan; it does not
    replace it. Oracle, DB2 and Teradata report exact per-column counts, and
    a fresh exact count away from every gate boundary is where the policy
    actually saves the scan — which is the estate it was written for.
    """
    if stats is None:
        return True, "no dictionary column statistics"
    if stats.approximate:
        return True, "dictionary statistics are approximate"
    if not stats_are_fresh(stats.stats_date, today, max_age_days):
        return True, f"dictionary statistics older than {max_age_days} days"
    ratio = None
    if near_gate_boundary(ratio=ratio, distinct_count=stats.distinct_count, **boundary):
        return True, "dictionary estimate sits near a gate boundary"
    return False, "fresh exact dictionary statistics"


__all__ = [
    "INTEGER_TYPES",
    "CHARACTER_TYPES",
    "TEMPORAL_TYPES",
    "Gate",
    "average_length",
    "dense_sequence",
    "distinct_ratio",
    "fingerprint_gate",
    "is_character_type",
    "is_integer_type",
    "is_junk_table",
    "is_temporal_type",
    "near_gate_boundary",
    "needs_column_scan",
    "needs_row_scan",
    "null_rate",
    "stats_are_fresh",
    "top_n_gate",
    "top_values",
]
