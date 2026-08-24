"""The Tier A crawl — inventory, constraints, indexes, dictionary stats, A5.

Order is the spec's (specs/01, execution order): A1 and A2 build the picture
and the allow-list, A3/A4/A6 add evidence, A5 reconciles last because it is
what catches a grant gap that made everything before it quietly partial.

Four behaviours are worth stating outright, because they are the difference
between a crawl you can trust and one you cannot:

* **Filtering happens here, not in SQL.** Statements run verbatim; rows for
  schemas out of scope are dropped in Python. No config value is ever
  interpolated into a statement.
* **System schemas are always out of scope**, config or no config, per the
  catalog's schema-scope policy — and what was dropped is recorded, so the
  scope of the bundle is auditable rather than implied.
* **A skipped query and a failed query are different things.** Skipped means
  the catalog has no block for this engine — a known, recorded gap
  (:mod:`crawler.gaps`). Failed means the database refused; that is a grant
  gap, and it downgrades reconciliation to INCOMPLETE.
* **Nothing is dropped silently.** Anything the crawl could not represent
  faithfully lands in ``result.warnings`` and travels with the bundle.
"""

from __future__ import annotations

from datetime import date

from . import gaps as gaps_module
from .adapters import Adapter, for_engine
from .allowlist import AllowList
from .catalog import COLUMN_STATS, REQUIRED, TABLE_STATS, TIER_A
from .config import CrawlConfig
from .errors import QueryError
from .execute import Connection, run_statement
from .results import (
    COMPLETE,
    INCOMPLETE,
    UNVERIFIED,
    CrawlResult,
    QueryRun,
    Reconciliation,
    Scope,
)
from .schemas import describe as describe_system_schemas
from .schemas import is_system_schema

#: The A6 statement keys, in run order. One query id, up to two blocks.
STATS_KEYS = ("A6", "A6-columns")


def crawl(
    connection: Connection,
    config: CrawlConfig,
    *,
    adapter: Adapter | None = None,
    today: date | None = None,
) -> CrawlResult:
    """Run Tier A against ``connection`` and return the result."""
    adapter = adapter or for_engine(config.engine)
    result = CrawlResult(
        database=config.database,
        engine=config.engine,
        crawl_date=today or date.today(),
        engine_verified=adapter.verified,
        gaps=[
            {
                "engine": gap.engine,
                "query_id": gap.query_id,
                "partial": gap.partial,
                "reason": gap.reason,
                "impact": gap.impact,
                "proposal": gap.proposal,
            }
            for gap in gaps_module.gaps_for(config.engine)
        ],
    )
    if not adapter.verified:
        result.warnings.append(
            f"the {config.engine} adapter has never been run against a live "
            "system; this crawl is UNVERIFIED until it has"
        )

    in_scope = _scope_filter(config)
    runner = _Runner(connection, config, adapter, result, in_scope)

    tables = runner.read("A1", adapter.parse_tables)
    result.scope = _scope_report(tables, config)
    # Sorted, because no catalog block carries an ORDER BY and dictionaries
    # are free to return rows in whatever order they like. Two crawls of an
    # unchanged database have to produce identical output or the refresh
    # diff — the whole drift signal — fills up with reordering noise.
    result.tables = sorted(
        (t for t in tables if in_scope(t.schema)),
        key=lambda t: (t.schema, t.name),
    )
    if result.scope.system_schemas:
        result.warnings.append(
            "system schemas excluded by policy: "
            f"{', '.join(result.scope.system_schemas)} "
            f"({result.scope.system_tables} base tables); "
            f"{config.engine} system schemas are "
            f"{describe_system_schemas(config.engine)}"
        )

    columns = runner.read("A2", adapter.parse_columns)
    known = {(t.schema, t.name) for t in result.tables}
    result.columns = sorted(
        (c for c in columns if in_scope(c.schema) and (c.schema, c.table) in known),
        key=lambda c: (c.schema, c.table, c.ordinal, c.name),
    )
    orphans = {
        (c.schema, c.table)
        for c in columns
        if in_scope(c.schema) and (c.schema, c.table) not in known
    }
    for schema, table in sorted(orphans):
        result.warnings.append(
            f"{schema}.{table}: A2 returned columns for a table A1 did not "
            "list; its columns are not cataloged and it is not allow-listed"
        )

    allowlist = AllowList.from_inventory(result.tables, result.columns)
    result.allowlist = allowlist.to_obj()
    for rejection in allowlist.rejected:
        result.warnings.append(
            f"{rejection.identifier}: {rejection.reason}; it is cataloged but "
            "cannot be profiled by Tier B/C"
        )

    result.constraints = [
        c for c in runner.read("A3", adapter.parse_constraints) if in_scope(c.schema)
    ]
    result.indexes = [
        i for i in runner.read("A4", adapter.parse_indexes) if in_scope(i.schema)
    ]

    if adapter.statement("A6") is None:
        # No dictionary statistics at all for this engine. A missing
        # A6-columns is not a gap — it is the normal case, for every engine
        # whose single A6 block answers for columns too.
        runner.skip("A6")
    for key in STATS_KEYS:
        statement = adapter.statement(key)
        if statement is None:
            continue
        rows = runner.rows(key)
        if rows is None:
            continue
        if TABLE_STATS in statement.provides:
            result.table_stats.extend(
                s
                for s in runner.parse(adapter.parse_table_stats, rows)
                if in_scope(s.schema) and (s.schema, s.table) in known
            )
        if COLUMN_STATS in statement.provides:
            result.column_stats.extend(
                s
                for s in runner.parse(adapter.parse_column_stats, rows)
                if in_scope(s.schema) and (s.schema, s.table) in known
            )
    result.table_stats.sort(key=lambda s: (s.schema, s.table))
    result.column_stats.sort(key=lambda s: (s.schema, s.table, s.column))

    result.reconciliation = _reconcile(runner, config, result)
    return result


def _scope_filter(config: CrawlConfig):
    """The predicate deciding whether a schema is crawled at all."""

    def in_scope(schema: str) -> bool:
        if is_system_schema(config.engine, schema):
            return False
        return config.includes_schema(schema)

    return in_scope


def _scope_report(tables, config: CrawlConfig) -> Scope:
    """What A1 returned and this run will not catalog, and why."""
    system_schemas, config_schemas = set(), set()
    system_tables = config_tables = 0
    for table in tables:
        if is_system_schema(config.engine, table.schema):
            system_schemas.add(table.schema)
            system_tables += int(table.is_base_table)
        elif not config.includes_schema(table.schema):
            config_schemas.add(table.schema)
            config_tables += int(table.is_base_table)
    return Scope(
        system_schemas=tuple(sorted(system_schemas)),
        system_tables=system_tables,
        config_schemas=tuple(sorted(config_schemas)),
        config_tables=config_tables,
    )


class _Runner:
    """Runs one statement: lookup, execution, parsing, bookkeeping."""

    def __init__(self, connection, config, adapter, result, in_scope):
        self.connection = connection
        self.config = config
        self.adapter = adapter
        self.result = result
        self.in_scope = in_scope
        self.failures: list[str] = []

    def skip(self, key: str) -> None:
        """Record that the catalog has no block for ``key`` on this engine."""
        gap = next(
            (
                g
                for g in gaps_module.gaps_for(self.config.engine)
                if g.query_id == key.split("-")[0] and not g.partial
            ),
            None,
        )
        note = f"no catalog block for {key} on {self.config.engine}"
        if gap:
            note = f"{note} (gap {gap.proposal})"
        self.result.queries.append(
            QueryRun(
                key=key,
                query_id=key.split("-")[0],
                variant=self.config.engine,
                sql="",
                status="skipped",
                note=note,
            )
        )
        self.result.warnings.append(f"{key} not run: {note}")

    def rows(self, key: str) -> list[tuple] | None:
        """Raw rows for ``key``, or None when skipped or failed."""
        statement = self.adapter.statement(key)
        if statement is None:
            if key in REQUIRED:
                raise QueryError(
                    f"no catalog block for {key}",
                    query_id=key,
                    engine=self.config.engine,
                )
            self.skip(key)
            return None
        try:
            rows = run_statement(self.connection, statement)
        except Exception as exc:  # noqa: BLE001 — the driver's error, verbatim
            if key in REQUIRED:
                raise QueryError(
                    str(exc), query_id=key, engine=self.config.engine
                ) from exc
            self.result.queries.append(
                QueryRun(
                    key=key,
                    query_id=statement.query_id,
                    variant=statement.variant,
                    sql=statement.sql,
                    status="failed",
                    note=str(exc),
                )
            )
            self.result.warnings.append(f"{key} failed: {exc}")
            self.failures.append(key)
            return None
        self.result.queries.append(
            QueryRun(
                key=key,
                query_id=statement.query_id,
                variant=statement.variant,
                sql=statement.sql,
                status="ok",
                rows=len(rows),
            )
        )
        return rows

    def parse(self, parser, rows):
        items, warnings = parser(rows)
        # A statement runs verbatim over the whole account view, so a reader
        # sees rows for schemas this run does not catalog. Their warnings
        # belong to somebody else's bundle, not this one.
        self.result.warnings.extend(
            warning.message
            for warning in warnings
            if warning.schema is None or self.in_scope(warning.schema)
        )
        return items

    def read(self, key: str, parser) -> list:
        rows = self.rows(key)
        if rows is None:
            return []
        return self.parse(parser, rows)


def _reconcile(runner: _Runner, config: CrawlConfig, result: CrawlResult):
    """A5: does what we cataloged match what should be there?

    Three ways to answer, in order of authority: the count the DBA put in
    config; failing that, A5's own visible count, once the base tables policy
    dropped are added back — A5 counts everything the account sees, system
    schemas included, so the comparison is only sound with that arithmetic
    and only when config filtered nothing further; failing that, nothing, and
    an unverifiable crawl is flagged UNVERIFIED rather than assumed complete.
    """
    cataloged = len(result.base_tables)
    expected = config.expected_table_count
    scope = result.scope
    visible = None
    notes = []

    rows = runner.rows("A5")
    if rows is not None:
        visible, warnings = runner.adapter.parse_reconciliation(rows)
        result.warnings.extend(warning.message for warning in warnings)

    if expected is not None:
        status = COMPLETE if cataloged == expected else INCOMPLETE
        notes.append(
            f"cataloged {cataloged} of {expected} expected base tables"
            + (f"; account sees {visible}" if visible is not None else "")
        )
    elif visible is not None and not scope.config_filtered:
        accounted = cataloged + scope.system_tables
        status = COMPLETE if accounted == visible else INCOMPLETE
        notes.append(
            f"cataloged {cataloged} base tables plus {scope.system_tables} "
            f"in system schemas = {accounted} of {visible} visible"
        )
    else:
        status = UNVERIFIED
        if visible is None:
            notes.append("no A5 count available and no expected count in config")
        else:
            notes.append(
                f"account sees {visible} base tables, but the crawl was "
                f"config-filtered to {cataloged} "
                f"({', '.join(scope.config_schemas)} excluded); set "
                "expected_table_count in config to make this verifiable"
            )

    if runner.failures and status == COMPLETE:
        status = INCOMPLETE
        notes.append(
            "downgraded: " + ", ".join(sorted(runner.failures)) + " failed to "
            "run, so evidence is missing that the counts cannot show"
        )
    elif runner.failures:
        notes.append(", ".join(sorted(runner.failures)) + " failed to run")

    if not result.engine_verified:
        notes.append(f"{result.engine} adapter is unverified")

    return Reconciliation(
        status=status,
        cataloged_tables=cataloged,
        visible_tables=visible,
        expected_tables=expected,
        note="; ".join(notes),
    )


__all__ = ["crawl", "TIER_A"]
