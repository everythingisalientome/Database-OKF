"""The measuring pass — B1, B2, B3, C1, in the catalog's run order.

Tier A read the dictionary. This is the half that touches data, and every
sentence of the design is about touching as little of it as possible and
keeping none of it.

**Stats-first.** A6 already asked the dictionary for row counts and per-column
distincts. B1 and B2 run only where that answer is missing, stale, or an
estimate — see :func:`crawler.profile.needs_row_scan` and
:func:`~crawler.profile.needs_column_scan` for exactly when, and why an
approximate statistic plans a scan rather than replacing it.

**Batched.** One B2 statement profiles up to twenty columns in one pass. The
catalog is explicit that a scan per column is not acceptable on a large table,
and a legacy fact table has hundreds of columns.

**Gated.** Top-N values are stored only for columns with few enough distinct
values to be a code list; fingerprints only for columns that look like join
keys. Both gates exist to keep the bundle small and step 2 honest, and both
record their refusals.

**Classified.** Every measured column gets a ``format`` category — the
plurality judgement of :mod:`crawler.formats` over a value sample. The C1
sample serves where it ran; B4 (adopted P14) samples the rest, sensitive
columns included, per the adjudicated ruling that a persisted category is not
a value. Temporal values pass through the canonical rendering of
:mod:`crawler.temporal` (adopted P15) before any of this, so fingerprints,
bounds, top-N and lengths agree across engines.

**Nothing raw survives.** B3's values are the one exception the rules allow
(low-cardinality, non-sensitive, explicitly permitted by the storage rule).
C1's and B4's values are classified, hashed and dropped inside this module —
no sample ever reaches the crawl result. Sensitive-listed columns keep their
numbers, their format and their length statistics, and contribute no value in
any form.

The pass runs between A6 and A5 so that a scan failure is still in front of
reconciliation: a grant that covers the dictionary but not the data is a real
grant gap, and the bundle has to say so.
"""

from __future__ import annotations

from datetime import date

from . import catalog, profile
from .adapters import Adapter, for_engine
from .adapters.base import Aggregates
from .allowlist import AllowList
from .bind import batches, bind
from .config import CrawlConfig
from .errors import AllowListError
from .fingerprint import hasher_for
from .formats import classify_column
from .normalize import Normalized, normalize_sample
from .results import (
    BUDGET_DENIED,
    FLAG_EMPTY,
    FLAG_JUNK,
    JUNK_SUSPECT,
    LIVE,
    SENSITIVE,
    STATS,
    STATS_ESTIMATE,
    UNPARSEABLE_TEMPORAL,
    ColumnProfile,
    Fingerprint,
    QueryRun,
    TableProfile,
)
from .temporal import render_temporal


def measure(
    connection,
    config: CrawlConfig,
    result,
    *,
    adapter: Adapter | None = None,
    today: date | None = None,
    failures: list | None = None,
) -> None:
    """Run B1/B2/B3/C1 over ``result``'s inventory, in place."""
    adapter = adapter or for_engine(config.engine)
    today = today or result.crawl_date or date.today()
    runner = _Runner(connection, config, adapter, result, failures)
    runner.run(today)


class _Budget:
    """What the pass is allowed to spend, and what it has spent.

    A budget refusal is a first-class outcome: the profile it denied says so,
    rather than reading like a column with nothing interesting about it.
    """

    def __init__(self, settings):
        self.max_statements = settings.max_statements
        self.max_tables = settings.max_scanned_tables
        self.max_rows = settings.max_scanned_rows
        self.statements = 0
        self.tables = 0

    def statement_available(self) -> bool:
        return self.max_statements is None or self.statements < self.max_statements

    def spend_statement(self) -> None:
        self.statements += 1

    def table_available(self) -> bool:
        return self.max_tables is None or self.tables < self.max_tables

    def spend_table(self) -> None:
        self.tables += 1

    def table_too_big(self, row_count) -> bool:
        return (
            self.max_rows is not None
            and row_count is not None
            and row_count > self.max_rows
        )


class _Runner:
    """One measuring pass: statements out, profiles in, everything recorded."""

    def __init__(self, connection, config, adapter, result, failures):
        self.connection = connection
        self.config = config
        self.settings = config.measure
        self.adapter = adapter
        self.result = result
        self.failures = failures if failures is not None else []
        self.allowlist = AllowList.from_obj(result.allowlist)
        self.hasher = hasher_for(config.measure)
        self.budget = _Budget(config.measure)
        self.table_stats = {(s.schema, s.table): s for s in result.table_stats}
        self.column_stats = {
            (s.schema, s.table, s.column): s for s in result.column_stats
        }
        self.indexed = _indexed_columns(result)

    # -- statement plumbing ------------------------------------------------

    def rows(self, key, *, schema, table, column=None, columns=None, note=""):
        """Bind and run one template. None when it was skipped or failed."""
        template = self.adapter.template(key)
        if template is None:
            self.result.warnings.append(
                f"{key} not run on {schema}.{table}: no catalog block for "
                f"{self.config.engine}"
            )
            return None
        if not self.budget.statement_available():
            return None
        try:
            sql = bind(
                template,
                self.allowlist,
                schema=schema,
                table=table,
                column=column,
                columns=columns,
            )
        except AllowListError as exc:
            self.result.warnings.append(f"{key} not run on {schema}.{table}: {exc}")
            return None

        self.budget.spend_statement()
        cursor = self.connection.cursor()
        try:
            try:
                cursor.execute(sql)
                rows = [tuple(row) for row in (cursor.fetchall() or [])]
            except Exception as exc:  # noqa: BLE001 — the driver's error, verbatim
                self.result.queries.append(
                    QueryRun(
                        key=key,
                        query_id=template.query_id,
                        variant=template.variant,
                        sql=sql,
                        status="failed",
                        note=f"{schema}.{table}{note}: {exc}",
                    )
                )
                self.result.warnings.append(
                    f"{key} failed on {schema}.{table}{note}: {exc}"
                )
                if key not in self.failures:
                    self.failures.append(key)
                return None
        finally:
            close = getattr(cursor, "close", None)
            if callable(close):
                close()

        self.result.queries.append(
            QueryRun(
                key=key,
                query_id=template.query_id,
                variant=template.variant,
                sql=sql,
                status="ok",
                rows=len(rows),
                note=f"{schema}.{table}{note}",
            )
        )
        return rows

    def warn(self, warnings) -> None:
        self.result.warnings.extend(str(w) for w in warnings)

    # -- the pass ----------------------------------------------------------

    def run(self, today: date) -> None:
        columns_by_table: dict[tuple[str, str], list] = {}
        for column in self.result.columns:
            columns_by_table.setdefault((column.schema, column.table), []).append(
                column
            )

        for table in self.result.base_tables:
            key = (table.schema, table.name)
            self.measure_table(table, columns_by_table.get(key, []), today)
        self.result.measured = True

    def measure_table(self, table, columns, today: date) -> None:
        schema, name = table.schema, table.name
        stats = self.table_stats.get((schema, name))
        needed, reason = profile.needs_row_scan(
            stats,
            today,
            max_age_days=self.settings.stats_max_age_days,
            trust_estimates=self.settings.trust_estimated_row_counts,
        )

        row_count = stats.row_count if stats else None
        source = stats.source if stats else STATS
        stats_date = stats.stats_date if stats else None
        notes = [reason]

        if needed and self.budget.table_too_big(row_count):
            needed = False
            notes.append(
                f"B1 skipped: dictionary says {row_count} rows, over the "
                f"scan budget of {self.settings.max_scanned_rows}"
            )
        if needed:
            rows = self.rows("B1", schema=schema, table=name)
            if rows is None:
                notes.append("B1 did not run; row count is the dictionary's")
            else:
                counted, warnings = self.adapter.parse_row_count(rows)
                self.warn(warnings)
                if counted is not None:
                    row_count, source, stats_date = counted, LIVE, None

        flags = []
        if profile.is_junk_table(name, row_count, self.settings.junk_patterns):
            flags.append(FLAG_JUNK)
            if row_count == 0:
                flags.append(FLAG_EMPTY)

        profiled = not flags
        if profiled and not self.budget.table_available():
            profiled = False
            notes.append(
                f"not profiled: the scan budget of "
                f"{self.settings.max_scanned_tables} tables is spent"
            )
        elif profiled and self.budget.table_too_big(row_count):
            profiled = False
            notes.append(
                f"not profiled: {row_count} rows is over the scan budget of "
                f"{self.settings.max_scanned_rows}"
            )
        elif flags:
            notes.append(
                "profiled minimally: "
                + ("no rows" if FLAG_EMPTY in flags else "junk-pattern name")
            )

        self.result.table_profiles.append(
            TableProfile(
                schema=schema,
                table=name,
                row_count=row_count,
                source=source,
                stats_date=stats_date,
                flags=tuple(flags),
                profiled=profiled,
                note="; ".join(notes),
            )
        )

        if profiled:
            self.budget.spend_table()
            self.measure_columns(schema, name, columns, row_count, today)
            return

        blocked = JUNK_SUSPECT if flags else BUDGET_DENIED
        for column in columns:
            agg, stats_date, null_rate, approximate = self._aggregates_from_stats(
                column, row_count
            )
            self.result.column_profiles.append(
                self._build(
                    column,
                    agg,
                    row_count,
                    source=STATS_ESTIMATE if approximate else STATS,
                    stats_date=stats_date,
                    null_rate=null_rate,
                    approximate=approximate,
                    blocked=blocked,
                )
            )

    def _aggregates_from_stats(self, column, row_count):
        """What the dictionary knows, in B2's shape, so one builder serves both.

        Engines report nulls in different currencies — Oracle and Teradata a
        count, PostgreSQL a fraction — so whichever arrived is converted here
        against the row count rather than in four places downstream.
        """
        stats = self.column_stats.get((column.schema, column.table, column.name))
        if stats is None:
            return Aggregates(), None, None, False
        null_count = stats.null_count
        if null_count is None and stats.null_rate is not None and row_count:
            null_count = round(stats.null_rate * row_count)
        non_null = (
            None
            if (row_count is None or null_count is None)
            else row_count - null_count
        )
        return (
            Aggregates(
                non_null_count=non_null,
                distinct_count=stats.distinct_count,
            ),
            stats.stats_date,
            stats.null_rate,
            bool(stats.approximate),
        )

    def measure_columns(self, schema, table, columns, row_count, today) -> None:
        """B2 where the dictionary cannot answer, gates for every column."""
        scan = []
        for column in columns:
            stats = self.column_stats.get((schema, table, column.name))
            needed, _reason = profile.needs_column_scan(
                stats,
                today,
                max_age_days=self.settings.stats_max_age_days,
                ratio_gate=self.settings.distinct_ratio_gate,
                band=self.settings.gate_boundary_band,
                distinct_gate=self.settings.top_n_distinct_max,
                factor=self.settings.gate_distinct_factor,
            )
            if needed:
                scan.append(column)

        aggregates, scanned_rows = self._scan(schema, table, scan)
        if scanned_rows is not None:
            row_count = scanned_rows

        for column in columns:
            agg = aggregates.get(column.name)
            if agg is not None:
                self.result.column_profiles.append(
                    self._build(column, agg, row_count, source=LIVE)
                )
                continue
            agg, stats_date, null_rate, approximate = self._aggregates_from_stats(
                column, row_count
            )
            notes = []
            if column in scan:
                notes.append(
                    "B2 did not run for this column; the numbers are the "
                    "dictionary's"
                )
            self.result.column_profiles.append(
                self._build(
                    column,
                    agg,
                    row_count,
                    source=STATS_ESTIMATE if approximate else STATS,
                    stats_date=stats_date,
                    null_rate=null_rate,
                    approximate=approximate,
                    notes=notes,
                )
            )

    def _scan(self, schema, table, columns):
        """B2 and B2-length over ``columns``, batched. ``{name: Aggregates}``."""
        aggregates, row_count = {}, None
        for batch in batches([c.name for c in columns], self.settings.batch_columns):
            rows = self.rows(
                "B2",
                schema=schema,
                table=table,
                columns=batch,
                note=f" [{', '.join(batch)}]",
            )
            if rows is None:
                continue
            counted, parsed, warnings = self.adapter.parse_column_aggregates(
                rows, batch
            )
            self.warn(warnings)
            if counted is not None:
                row_count = counted
            aggregates.update(parsed)

        by_name = {c.name: c for c in columns}
        measurable = [
            name
            for name in aggregates
            if profile.is_character_type(by_name[name])
        ]
        for batch in batches(measurable, self.settings.batch_columns):
            rows = self.rows(
                "B2-length",
                schema=schema,
                table=table,
                columns=batch,
                note=f" [{', '.join(batch)}]",
            )
            if rows is None:
                continue
            lengths, warnings = self.adapter.parse_column_lengths(rows, batch)
            self.warn(warnings)
            for name, (low, high, average) in lengths.items():
                aggregates[name] = aggregates[name].with_lengths(low, high, average)
        return aggregates, row_count

    def _build(
        self,
        column,
        agg,
        row_count,
        *,
        source=LIVE,
        stats_date=None,
        null_rate=None,
        approximate=False,
        blocked="",
        notes=(),
    ) -> ColumnProfile:
        """One column profile: derived numbers, then the two gates.

        ``blocked`` is set when the table was never profiled at all — junk,
        empty, or over a budget. Then no gate runs, and the reason travels on
        the profile instead of a fingerprint that was never attempted.
        """
        schema, table, name = column.schema, column.table, column.name
        non_null = agg.non_null_count
        null_count = (
            None if (row_count is None or non_null is None) else row_count - non_null
        )
        ratio = profile.distinct_ratio(agg.distinct_count, non_null)
        sensitive = self.settings.is_sensitive(schema, table, name)
        indexed = name in self.indexed.get((schema, table), ())
        notes = list(notes)
        suppressed: list[str] = []

        if blocked:
            notes.append(f"not measured: {blocked}")

        # B2 computes MIN and MAX for every column in the batch, sensitive or
        # not — a batched statement cannot leave one column's aggregate out.
        # They are read and dropped here rather than published: a range is two
        # raw values, and the rules say a sensitive column contributes none in
        # any form. The length statistics stay, because a length is not a
        # value; the fixture bundles draw the line in the same place.
        low, high = agg.min_value, agg.max_value
        if sensitive:
            notes.append("range suppressed: the column is sensitive-listed")

        # Temporal bounds pass through the canonical rendering (P15) — the
        # driver hands back a datetime, and str() of one is an engine
        # spelling, not the OKF's.
        is_temporal = profile.is_temporal_type(column)
        if is_temporal:
            if low is not None:
                low = render_temporal(low) or _render(low)
            if high is not None:
                high = render_temporal(high) or _render(high)

        top = ()
        gate = profile.top_n_gate(
            distinct_count=agg.distinct_count,
            sensitive=sensitive,
            maximum=self.settings.top_n_distinct_max,
        )
        if blocked:
            suppressed.append(blocked)
        elif gate:
            rows = self.rows("B3", schema=schema, table=table, column=name,
                             note=f".{name}")
            if rows is None:
                suppressed.append(BUDGET_DENIED)
            else:
                pairs, warnings = self.adapter.parse_top_values(rows)
                self.warn(warnings)
                if is_temporal:
                    pairs = [
                        (render_temporal(value) or _render(value), freq)
                        for value, freq in pairs
                    ]
                top = profile.top_values(pairs, non_null, self.settings.top_n)
        else:
            suppressed.append(gate.reason)

        fingerprint = None
        sample: _Sample | None = None
        gate = profile.fingerprint_gate(
            column,
            ratio=ratio,
            indexed=indexed,
            sensitive=sensitive,
            threshold=self.settings.distinct_ratio_gate,
        )
        if blocked:
            suppressed.append(blocked)
        elif gate:
            fingerprint, sample, reason, note = self._fingerprint(
                column, agg.distinct_count, is_temporal
            )
            if note:
                notes.append(note)
            if fingerprint is None:
                suppressed.append(reason or BUDGET_DENIED)
        else:
            suppressed.append(gate.reason)

        # Format (adopted P14): classify from the C1 sample where one ran,
        # else read a B4 sample transiently — sensitive columns included,
        # per the ruling, unless config withdraws them.
        column_format = None
        if sample is not None:
            column_format = classify_column(sample.normalized.values)
        elif not blocked:
            if sensitive and not self.settings.classify_sensitive_formats:
                suppressed.append(SENSITIVE)
                notes.append(
                    "format not classified: sensitive-listed, and "
                    "classify_sensitive_formats is off"
                )
            else:
                column_format, sample, note = self._format_sample(
                    column, is_temporal
                )
                if note:
                    notes.append(note)
                if column_format is None and sample is None:
                    suppressed.append(BUDGET_DENIED)

        # Temporal length statistics (P15): computed over the rendered
        # values, row-weighted by the sample's freq, and only when the
        # sample is the complete distinct set. B2-length cannot answer —
        # LENGTH() of a datetime measures an engine spelling.
        min_length = agg.min_length
        max_length = agg.max_length
        avg_length = agg.avg_length
        if is_temporal and sample is not None:
            lengths = _weighted_lengths(sample)
            if lengths is not None:
                min_length, max_length, avg_length = lengths
            else:
                notes.append(
                    "length statistics unavailable: the temporal sample is "
                    "incomplete or carries no row counts"
                )

        return ColumnProfile(
            schema=schema,
            table=table,
            column=name,
            source=source,
            stats_date=stats_date,
            row_count=row_count,
            non_null_count=non_null,
            null_count=null_count,
            distinct_count=agg.distinct_count,
            min_value=None if sensitive else _render(low),
            max_value=None if sensitive else _render(high),
            min_length=min_length,
            max_length=max_length,
            avg_length=profile.average_length(avg_length),
            null_rate=(
                profile.null_rate(null_count, row_count)
                if null_rate is None
                else round(float(null_rate), 4)
            ),
            distinct_ratio=ratio,
            approximate=approximate,
            dense_sequence=profile.dense_sequence(
                column,
                agg.distinct_count,
                low,
                high,
                fill=self.settings.dense_sequence_fill,
                start_max=self.settings.dense_sequence_start_max,
            ),
            sensitive=sensitive,
            format=column_format,
            top_values=top,
            fingerprint=fingerprint,
            suppressed=tuple(suppressed),
            notes=tuple(notes),
        )

    def _fingerprint(self, column, distinct_count, is_temporal):
        """Run C1, render if temporal, normalize, hash.

        Returns ``(fingerprint, sample, refusal_reason, note)``. The values
        never leave this method — the sample that survives holds normalized
        (rendered) values and their row counts, for format classification and
        temporal length statistics, and is dropped with the profile build.
        """
        key = "C1" if profile.is_character_type(column) else "C1-cast"
        rows = self.rows(
            key,
            schema=column.schema,
            table=column.table,
            column=column.name,
            note=f".{column.name}",
        )
        if rows is None:
            return None, None, BUDGET_DENIED, ""
        triples, warnings = self.adapter.parse_value_sample(rows)
        self.warn(warnings)

        if is_temporal:
            rendered = []
            for value, raw, freq in triples:
                canonical = render_temporal(raw if raw is not None else value)
                if canonical is None:
                    return (
                        None,
                        None,
                        UNPARSEABLE_TEMPORAL,
                        f"no fingerprint: {_render(raw or value)!r} does not "
                        "parse as a temporal value, and a partially rendered "
                        "sample is no longer a bottom-k",
                    )
                # The rendered value is its own representative: rendering is
                # not one of the recordable normalization rules, and the
                # fixture payloads agree (hire_date records []).
                rendered.append((canonical, canonical, freq))
            triples = rendered

        normalized = normalize_sample(triples)
        cap = self.settings.fingerprint_sample
        kept = normalized.head(cap)
        count = len(normalized.values)
        note = ""
        if len(rows) >= catalog.SAMPLE_CAP:
            # The statement's own limit bit, so the true distinct count is
            # not knowable from this sample. B2's distinct count is an upper
            # bound on it and is the honest number to publish.
            count = max(count, distinct_count or count)
            note = (
                f"C1 returned the catalog's cap of {catalog.SAMPLE_CAP} values; "
                "the stored count is B2's distinct count, and the hash set is "
                "a bottom-k slice of it"
            )
        if normalized.collapsed:
            note = (
                (note + "; ") if note else ""
            ) + f"normalization merged {normalized.collapsed} values"

        return (
            Fingerprint(
                schema=column.schema,
                table=column.table,
                column=column.name,
                algo=self.hasher.algo,
                normalization=normalized.rules,
                sample_cap=cap,
                count=count,
                hashes=self.hasher.hash_all(kept.values),
            ),
            _Sample(normalized, complete=len(rows) < catalog.SAMPLE_CAP),
            "",
            note,
        )

    def _format_sample(self, column, is_temporal):
        """Run B4 and return ``(format, sample, note)``.

        The one query that reads sensitive columns, and the ruling that lets
        it (adopted P14): the values are classified and dropped, and only the
        category — never a value in any form — reaches the result.
        """
        rows = self.rows(
            "B4",
            schema=column.schema,
            table=column.table,
            column=column.name,
            note=f".{column.name}",
        )
        if rows is None:
            return None, None, ""
        pairs, warnings = self.adapter.parse_format_sample(rows)
        self.warn(warnings)

        note = ""
        if is_temporal:
            rendered = []
            for value, freq in pairs:
                canonical = render_temporal(value)
                if canonical is None:
                    return (
                        None,
                        None,
                        f"format not classified: {_render(value)!r} does not "
                        "parse as a temporal value",
                    )
                rendered.append((canonical, canonical, freq))
            triples = rendered
        else:
            triples = [(value, None, freq) for value, freq in pairs]

        normalized = normalize_sample(triples)
        sample = _Sample(normalized, complete=len(rows) < catalog.FORMAT_CAP)
        return classify_column(normalized.values), sample, note


class _Sample:
    """A normalized value sample and whether it is the complete distinct set.

    ``complete`` is read off the row count: a statement that returned fewer
    rows than its own cap had nothing left to return. Only a complete sample
    may stand in for the column in a row-weighted computation.
    """

    def __init__(self, normalized: Normalized, *, complete: bool):
        self.normalized = normalized
        self.complete = complete


def _weighted_lengths(sample: _Sample):
    """``(min, max, avg)`` character lengths over a rendered temporal sample,
    row-weighted, or None when the sample cannot honestly answer."""
    normalized = sample.normalized
    if not sample.complete or not normalized.values:
        return None
    freqs = normalized.freqs
    if len(freqs) != len(normalized.values) or any(f is None for f in freqs):
        return None
    sizes = [len(value) for value in normalized.values]
    total = sum(freqs)
    if not total:
        return None
    weighted = sum(
        len(value) * freq for value, freq in zip(normalized.values, freqs)
    )
    return min(sizes), max(sizes), weighted / total


def _indexed_columns(result) -> dict[tuple[str, str], set]:
    """Columns carrying join intent: index key columns and constraint columns.

    INCLUDE columns are left out on purpose. An index carries them so they can
    be fetched without a second read, explicitly not so they can be searched
    on, and counting one as evidence would manufacture a join nobody intended
    (the same distinction A4 preserves).
    """
    members: dict[tuple[str, str], set] = {}
    for index in result.indexes:
        members.setdefault((index.schema, index.table), set()).update(index.columns)
    for constraint in result.constraints:
        members.setdefault((constraint.schema, constraint.table), set()).update(
            constraint.columns
        )
    return members


def _render(value):
    """One measured bound as the OKF carries it: text, or nothing."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


__all__ = ["measure"]
