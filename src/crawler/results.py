"""The crawl result — what a Tier A run produces.

Step 1 finishes in two stages: this deterministic crawl, and (session 4) an
LLM annotation pass that turns the crawl into an OKF bundle. The seam between
them is this object, written as JSON. Keeping it separate means the crawl is
re-runnable and diffable on its own, and the annotator consumes a file rather
than a live connection.

Everything here is measured or read from the dictionary. Nothing in this
module is inferred, so nothing carries a confidence: the provenance of every
field is ``[observed]`` by construction, and session 4 tags it as such when
it emits the bundle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

#: Reconciliation outcomes (specs/01 step 7).
COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"
UNVERIFIED = "UNVERIFIED"

#: Where a row count came from — the specs/01 ``row_count_source`` vocabulary.
#: ``stats`` is a dictionary count, ``stats-estimate`` a planner estimate
#: (PostgreSQL's reltuples), ``live`` a B1 scan in the measuring pass.
STATS = "stats"
STATS_ESTIMATE = "stats-estimate"
LIVE = "live"
ROW_COUNT_SOURCES = (STATS, STATS_ESTIMATE, LIVE)


def _as_date(value):
    return date.fromisoformat(value) if isinstance(value, str) else value


def _iso(value):
    return value.isoformat() if isinstance(value, date) else value


@dataclass(frozen=True)
class Table:
    """One table or view from A1."""

    schema: str
    name: str
    kind: str  # BASE TABLE | VIEW
    catalog: str | None = None

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def is_base_table(self) -> bool:
        return self.kind == "BASE TABLE"

    def to_obj(self) -> dict:
        return {
            "schema": self.schema,
            "name": self.name,
            "kind": self.kind,
            "catalog": self.catalog,
        }

    @classmethod
    def from_obj(cls, obj: dict) -> Table:
        return cls(obj["schema"], obj["name"], obj["kind"], obj.get("catalog"))


@dataclass(frozen=True)
class Column:
    """One column from A2, type canonicalised, engine spelling kept."""

    schema: str
    table: str
    name: str
    ordinal: int
    type: str
    raw_type: str
    nullable: bool
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    default: str | None = None

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}.{self.name}"

    def to_obj(self) -> dict:
        return {
            "schema": self.schema,
            "table": self.table,
            "name": self.name,
            "ordinal": self.ordinal,
            "type": self.type,
            "raw_type": self.raw_type,
            "nullable": self.nullable,
            "length": self.length,
            "precision": self.precision,
            "scale": self.scale,
            "default": self.default,
        }

    @classmethod
    def from_obj(cls, obj: dict) -> Column:
        return cls(
            schema=obj["schema"],
            table=obj["table"],
            name=obj["name"],
            ordinal=obj["ordinal"],
            type=obj["type"],
            raw_type=obj["raw_type"],
            nullable=obj["nullable"],
            length=obj.get("length"),
            precision=obj.get("precision"),
            scale=obj.get("scale"),
            default=obj.get("default"),
        )


@dataclass(frozen=True)
class Constraint:
    """A declared PK / FK / UNIQUE from A3.

    A foreign key carries where it points: ``referenced_table`` as
    ``schema.table``, and ``referenced_columns`` paired with ``columns`` by
    position. Both stay empty when the target is outside the account's
    grants, so A3 never returned the constraint being referenced — an
    unresolved target is reported as unresolved, never guessed at.
    """

    kind: str  # PRIMARY KEY | FOREIGN KEY | UNIQUE | ...
    schema: str
    table: str
    columns: tuple[str, ...]
    name: str | None = None
    referenced_constraint: str | None = None
    referenced_table: str | None = None
    referenced_columns: tuple[str, ...] = ()

    def to_obj(self) -> dict:
        return {
            "kind": self.kind,
            "schema": self.schema,
            "table": self.table,
            "columns": list(self.columns),
            "name": self.name,
            "referenced_constraint": self.referenced_constraint,
            "referenced_table": self.referenced_table,
            "referenced_columns": list(self.referenced_columns),
        }

    @classmethod
    def from_obj(cls, obj: dict) -> Constraint:
        return cls(
            kind=obj["kind"],
            schema=obj["schema"],
            table=obj["table"],
            columns=tuple(obj["columns"]),
            name=obj.get("name"),
            referenced_constraint=obj.get("referenced_constraint"),
            referenced_table=obj.get("referenced_table"),
            referenced_columns=tuple(obj.get("referenced_columns") or ()),
        )


@dataclass(frozen=True)
class Index:
    """An index from A4.

    ``primary_index`` is the Teradata Primary Index flag and nothing else: it
    is the distribution key the original designers chose, the strongest
    join-intent signal on a Teradata system with no declared FKs, and it has
    to survive every hop to step 2 (CLAUDE.md, environment).
    """

    schema: str
    table: str
    #: None where the dictionary reports unnamed indexes (Teradata).
    name: str | None
    unique: bool
    columns: tuple[str, ...]
    primary_index: bool = False
    included_columns: tuple[str, ...] = ()

    def to_obj(self) -> dict:
        return {
            "schema": self.schema,
            "table": self.table,
            "name": self.name,
            "unique": self.unique,
            "columns": list(self.columns),
            "primary_index": self.primary_index,
            "included_columns": list(self.included_columns),
        }

    @classmethod
    def from_obj(cls, obj: dict) -> Index:
        return cls(
            schema=obj["schema"],
            table=obj["table"],
            name=obj["name"],
            unique=obj["unique"],
            columns=tuple(obj["columns"]),
            primary_index=obj.get("primary_index", False),
            included_columns=tuple(obj.get("included_columns") or ()),
        )


@dataclass(frozen=True)
class TableStats:
    """Dictionary row count for a table (A6).

    ``source`` is the specs/01 vocabulary: ``stats`` for a dictionary count,
    ``stats-estimate`` for a planner estimate. PostgreSQL's ``reltuples`` is
    the second kind, and row counts are load-bearing enough — rate
    denominators, overlap confidence weights, junk-table filter, step 3
    sequencing — that the difference has to travel with the number rather
    than being flattened into it. ``live`` is B1's, in the measuring pass.
    """

    schema: str
    table: str
    row_count: int
    source: str = STATS
    stats_date: date | None = None

    def __post_init__(self):
        if self.source not in ROW_COUNT_SOURCES:
            raise ValueError(
                f"row_count_source must be one of {ROW_COUNT_SOURCES}, "
                f"got {self.source!r}"
            )

    @property
    def estimated(self) -> bool:
        return self.source == STATS_ESTIMATE

    def to_obj(self) -> dict:
        return {
            "schema": self.schema,
            "table": self.table,
            "row_count": self.row_count,
            "source": self.source,
            "stats_date": _iso(self.stats_date),
        }

    @classmethod
    def from_obj(cls, obj: dict) -> TableStats:
        return cls(
            schema=obj["schema"],
            table=obj["table"],
            row_count=obj["row_count"],
            source=obj.get("source", STATS),
            stats_date=_as_date(obj.get("stats_date")),
        )


@dataclass(frozen=True)
class ColumnStats:
    """Dictionary column statistics (A6).

    Engines answer in different currencies: Oracle and Teradata give null
    *counts*, PostgreSQL gives a null *fraction*. Both are recorded as they
    came, because deriving one from the other needs a row count that is
    itself an estimate.

    ``approximate`` marks distinct counts that are histogram or sample
    derived — PostgreSQL's ``n_distinct``, SQL Server's histogram sum. They
    are ample for gating and not good enough to publish as measurements, so
    B2 still runs where they land near a gate boundary.
    """

    schema: str
    table: str
    column: str
    distinct_count: int | None = None
    null_count: int | None = None
    null_rate: float | None = None
    stats_date: date | None = None
    approximate: bool = False

    def to_obj(self) -> dict:
        return {
            "schema": self.schema,
            "table": self.table,
            "column": self.column,
            "distinct_count": self.distinct_count,
            "null_count": self.null_count,
            "null_rate": self.null_rate,
            "stats_date": _iso(self.stats_date),
            "approximate": self.approximate,
        }

    @classmethod
    def from_obj(cls, obj: dict) -> ColumnStats:
        return cls(
            schema=obj["schema"],
            table=obj["table"],
            column=obj["column"],
            distinct_count=obj.get("distinct_count"),
            null_count=obj.get("null_count"),
            null_rate=obj.get("null_rate"),
            stats_date=_as_date(obj.get("stats_date")),
            approximate=obj.get("approximate", False),
        )


@dataclass(frozen=True)
class Scope:
    """Which schemas this crawl skipped, and why.

    The catalog's schema-scope policy requires the exclusions to be recorded
    so a bundle's scope is auditable: a schema that was deliberately skipped
    and a schema that was not there read identically in the output otherwise.
    """

    #: Engine system schemas dropped by policy, and the base tables in them.
    system_schemas: tuple[str, ...] = ()
    system_tables: int = 0
    #: Schemas dropped by the run's own config, and the base tables in them.
    config_schemas: tuple[str, ...] = ()
    config_tables: int = 0

    @property
    def config_filtered(self) -> bool:
        return bool(self.config_schemas)

    def to_obj(self) -> dict:
        return {
            "system_schemas": list(self.system_schemas),
            "system_tables": self.system_tables,
            "config_schemas": list(self.config_schemas),
            "config_tables": self.config_tables,
        }

    @classmethod
    def from_obj(cls, obj: dict) -> Scope:
        return cls(
            system_schemas=tuple(obj.get("system_schemas") or ()),
            system_tables=obj.get("system_tables", 0),
            config_schemas=tuple(obj.get("config_schemas") or ()),
            config_tables=obj.get("config_tables", 0),
        )


@dataclass(frozen=True)
class QueryRun:
    """The audit trail: which catalog statement ran, verbatim, and what came
    back. A crawl that cannot show its SQL cannot be reviewed."""

    #: Statement key — the catalog query id, or ``A6-columns`` where one
    #: query id has two blocks for this engine.
    key: str
    query_id: str
    variant: str
    sql: str
    status: str  # ok | failed | skipped
    rows: int | None = None
    note: str | None = None

    def to_obj(self) -> dict:
        return {
            "key": self.key,
            "query_id": self.query_id,
            "variant": self.variant,
            "sql": self.sql,
            "status": self.status,
            "rows": self.rows,
            "note": self.note,
        }

    @classmethod
    def from_obj(cls, obj: dict) -> QueryRun:
        return cls(
            key=obj["key"],
            query_id=obj["query_id"],
            variant=obj["variant"],
            sql=obj["sql"],
            status=obj["status"],
            rows=obj.get("rows"),
            note=obj.get("note"),
        )


@dataclass(frozen=True)
class Reconciliation:
    """A5's answer: does what we cataloged match what should be there?"""

    status: str  # COMPLETE | INCOMPLETE | UNVERIFIED
    cataloged_tables: int
    visible_tables: int | None = None
    expected_tables: int | None = None
    note: str = ""

    @property
    def is_complete(self) -> bool:
        return self.status == COMPLETE

    def to_obj(self) -> dict:
        return {
            "status": self.status,
            "cataloged_tables": self.cataloged_tables,
            "visible_tables": self.visible_tables,
            "expected_tables": self.expected_tables,
            "note": self.note,
        }

    @classmethod
    def from_obj(cls, obj: dict) -> Reconciliation:
        return cls(
            status=obj["status"],
            cataloged_tables=obj["cataloged_tables"],
            visible_tables=obj.get("visible_tables"),
            expected_tables=obj.get("expected_tables"),
            note=obj.get("note", ""),
        )


# ---------------------------------------------------------------------------
# The measuring pass — what B1/B2/B3/C1 produced
# ---------------------------------------------------------------------------

#: Decimal places for the rates the OKF publishes. Four, because that is what
#: the fixture bundles carry and because the fifth digit of a ratio measured
#: against an estimated denominator is noise.
RATE_PRECISION = 4

#: Decimal places for average character length.
LENGTH_PRECISION = 1

#: Why a column carries no top-N values or no fingerprint. Every absence has
#: one of these against it: "no fingerprint" and "fingerprint suppressed
#: because the column is sensitive" are different facts and step 2 must not
#: have to guess which it is looking at.
GATE_CARDINALITY = "cardinality-gate"
GATE_DISTINCT = "distinct-gate"
SENSITIVE = "sensitive-listed"
BUDGET_DENIED = "budget"
JUNK_SUSPECT = "junk-suspect"
#: A temporal value the canonical rendering could not read. The whole
#: fingerprint is withheld — a sample that lost some values is no longer the
#: bottom-k it claims to be.
UNPARSEABLE_TEMPORAL = "unparseable-temporal"

#: Table flags, the specs/01 frontmatter vocabulary.
FLAG_JUNK = "junk-suspect"
FLAG_EMPTY = "empty"


def _rate(value, precision=RATE_PRECISION):
    return None if value is None else round(float(value), precision)


@dataclass(frozen=True)
class TopValue:
    """One row of B3. ``percent`` is against the non-null count, not the row
    count: on a column that is 80% NULL, a value present in every remaining
    row characterises the column completely, and dividing by the row count
    would report it as a fifth of one."""

    value: str
    frequency: int
    percent: int

    def to_obj(self) -> dict:
        return {
            "value": self.value,
            "frequency": self.frequency,
            "percent": self.percent,
        }

    @classmethod
    def from_obj(cls, obj: dict) -> TopValue:
        return cls(obj["value"], obj["frequency"], obj["percent"])


@dataclass(frozen=True)
class Fingerprint:
    """A hashed C1 sample, ready to be written beside a bundle.

    ``count`` is how many distinct normalized values the column has, and
    ``len(hashes)`` how many were kept: they differ only when the sample cap
    bit, which is what makes the truncation visible to step 2 rather than
    silently changing what an overlap is measured over.
    """

    schema: str
    table: str
    column: str
    algo: str
    normalization: tuple[str, ...]
    sample_cap: int
    count: int
    hashes: tuple[str, ...]

    @property
    def path(self) -> str:
        """Bundle-relative payload path (specs/04, file mechanics)."""
        return f"fingerprints/{self.table}.{self.column}.json"

    @property
    def truncated(self) -> bool:
        return self.count > len(self.hashes)

    def to_payload(self) -> dict:
        """The payload as ``okf.Fingerprint`` reads it, in fixture key order."""
        return {
            "algo": self.algo,
            "normalization": list(self.normalization),
            "sample_cap": self.sample_cap,
            "count": self.count,
            "hashes": list(self.hashes),
        }

    def to_obj(self) -> dict:
        return {
            "schema": self.schema,
            "table": self.table,
            "column": self.column,
            "path": self.path,
            **self.to_payload(),
        }

    @classmethod
    def from_obj(cls, obj: dict) -> Fingerprint:
        return cls(
            schema=obj["schema"],
            table=obj["table"],
            column=obj["column"],
            algo=obj["algo"],
            normalization=tuple(obj.get("normalization") or ()),
            sample_cap=obj["sample_cap"],
            count=obj["count"],
            hashes=tuple(obj.get("hashes") or ()),
        )


@dataclass(frozen=True)
class TableProfile:
    """What the measuring pass learned about one table (B1, plus policy).

    ``source`` is the specs/01 ``row_count_source`` vocabulary. ``flags`` is
    the frontmatter list: a table matching a junk pattern or holding no rows
    is cataloged and flagged, then profiled minimally — it is cheap to record
    that ``ACCOUNT_BKP`` exists and expensive to profile it, and step 3 needs
    to know not to route a query through it.
    """

    schema: str
    table: str
    row_count: int | None = None
    source: str = LIVE
    stats_date: date | None = None
    flags: tuple[str, ...] = ()
    #: False when the pass deliberately did not scan: junk, empty, or a
    #: budget refusal. Always with a note saying which.
    profiled: bool = True
    note: str = ""

    def __post_init__(self):
        if self.source not in ROW_COUNT_SOURCES:
            raise ValueError(
                f"row_count_source must be one of {ROW_COUNT_SOURCES}, "
                f"got {self.source!r}"
            )

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"

    def to_obj(self) -> dict:
        return {
            "schema": self.schema,
            "table": self.table,
            "row_count": self.row_count,
            "source": self.source,
            "stats_date": _iso(self.stats_date),
            "flags": list(self.flags),
            "profiled": self.profiled,
            "note": self.note,
        }

    @classmethod
    def from_obj(cls, obj: dict) -> TableProfile:
        return cls(
            schema=obj["schema"],
            table=obj["table"],
            row_count=obj.get("row_count"),
            source=obj.get("source", LIVE),
            stats_date=_as_date(obj.get("stats_date")),
            flags=tuple(obj.get("flags") or ()),
            profiled=obj.get("profiled", True),
            note=obj.get("note", ""),
        )


@dataclass(frozen=True)
class ColumnProfile:
    """One column's measured profile — B2's numbers and everything derived.

    The derived fields are stored rather than computed on read because they
    are what the OKF publishes and what step 2 gates on, and because the
    denominators differ: ``null_rate`` is against the row count and
    ``distinct_ratio`` against the non-null count. A consumer recomputing
    either from the wrong one would be wrong quietly.
    """

    schema: str
    table: str
    column: str
    #: Where the numbers came from: a B2 scan, or the dictionary.
    source: str = LIVE
    stats_date: date | None = None
    row_count: int | None = None
    non_null_count: int | None = None
    null_count: int | None = None
    distinct_count: int | None = None
    #: Rendered as text, because that is what the OKF carries and what a
    #: cross-engine comparison can be made of.
    min_value: str | None = None
    max_value: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    avg_length: float | None = None
    null_rate: float | None = None
    distinct_ratio: float | None = None
    dense_sequence: bool = False
    approximate: bool = False
    sensitive: bool = False
    #: The plurality format category (catalog B4 / formats vocabulary), or
    #: None where nothing could be classified.
    format: str | None = None
    top_values: tuple[TopValue, ...] = ()
    fingerprint: Fingerprint | None = None
    #: Why top-N or a fingerprint is absent. Never empty when either is.
    suppressed: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}.{self.column}"

    def to_obj(self) -> dict:
        return {
            "schema": self.schema,
            "table": self.table,
            "column": self.column,
            "source": self.source,
            "stats_date": _iso(self.stats_date),
            "row_count": self.row_count,
            "non_null_count": self.non_null_count,
            "null_count": self.null_count,
            "distinct_count": self.distinct_count,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "min_length": self.min_length,
            "max_length": self.max_length,
            "avg_length": self.avg_length,
            "null_rate": self.null_rate,
            "distinct_ratio": self.distinct_ratio,
            "dense_sequence": self.dense_sequence,
            "approximate": self.approximate,
            "sensitive": self.sensitive,
            "format": self.format,
            "top_values": [v.to_obj() for v in self.top_values],
            "fingerprint": (
                self.fingerprint.to_obj() if self.fingerprint else None
            ),
            "suppressed": list(self.suppressed),
            "notes": list(self.notes),
        }

    @classmethod
    def from_obj(cls, obj: dict) -> ColumnProfile:
        fingerprint = obj.get("fingerprint")
        return cls(
            schema=obj["schema"],
            table=obj["table"],
            column=obj["column"],
            source=obj.get("source", LIVE),
            stats_date=_as_date(obj.get("stats_date")),
            row_count=obj.get("row_count"),
            non_null_count=obj.get("non_null_count"),
            null_count=obj.get("null_count"),
            distinct_count=obj.get("distinct_count"),
            min_value=obj.get("min_value"),
            max_value=obj.get("max_value"),
            min_length=obj.get("min_length"),
            max_length=obj.get("max_length"),
            avg_length=obj.get("avg_length"),
            null_rate=obj.get("null_rate"),
            distinct_ratio=obj.get("distinct_ratio"),
            dense_sequence=obj.get("dense_sequence", False),
            approximate=obj.get("approximate", False),
            sensitive=obj.get("sensitive", False),
            format=obj.get("format"),
            top_values=tuple(
                TopValue.from_obj(o) for o in obj.get("top_values") or ()
            ),
            fingerprint=(
                Fingerprint.from_obj(fingerprint) if fingerprint else None
            ),
            suppressed=tuple(obj.get("suppressed") or ()),
            notes=tuple(obj.get("notes") or ()),
        )


@dataclass
class CrawlResult:
    """Everything one Tier A crawl of one database produced."""

    database: str
    engine: str
    crawl_date: date
    #: False for engines whose adapter has never met a live system.
    engine_verified: bool = True
    tables: list[Table] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    indexes: list[Index] = field(default_factory=list)
    table_stats: list[TableStats] = field(default_factory=list)
    column_stats: list[ColumnStats] = field(default_factory=list)
    #: The measuring pass. Empty on a Tier A-only crawl, which is a
    #: complete crawl in its own right: the inventory does not depend on it.
    table_profiles: list[TableProfile] = field(default_factory=list)
    column_profiles: list[ColumnProfile] = field(default_factory=list)
    #: True once the measuring pass has run over this result.
    measured: bool = False
    reconciliation: Reconciliation | None = None
    scope: Scope = field(default_factory=lambda: Scope())
    queries: list[QueryRun] = field(default_factory=list)
    #: Catalog gaps that applied to this run (see :mod:`crawler.gaps`).
    gaps: list[dict] = field(default_factory=list)
    #: Anything the run could not represent faithfully, in plain words.
    warnings: list[str] = field(default_factory=list)
    #: Identifiers this crawl observed — the only ones Tier B/C may use.
    allowlist: dict = field(default_factory=dict)

    @property
    def base_tables(self) -> list[Table]:
        return [t for t in self.tables if t.is_base_table]

    @property
    def completeness(self) -> str:
        """The flag that goes on the bundle and propagates downstream."""
        if not self.engine_verified:
            return UNVERIFIED
        if self.reconciliation is None:
            return UNVERIFIED
        return self.reconciliation.status

    def to_obj(self) -> dict:
        return {
            "database": self.database,
            "engine": self.engine,
            "crawl_date": _iso(self.crawl_date),
            "engine_verified": self.engine_verified,
            "completeness": self.completeness,
            "tables": [t.to_obj() for t in self.tables],
            "columns": [c.to_obj() for c in self.columns],
            "constraints": [c.to_obj() for c in self.constraints],
            "indexes": [i.to_obj() for i in self.indexes],
            "table_stats": [s.to_obj() for s in self.table_stats],
            "column_stats": [s.to_obj() for s in self.column_stats],
            "measured": self.measured,
            "table_profiles": [p.to_obj() for p in self.table_profiles],
            "column_profiles": [p.to_obj() for p in self.column_profiles],
            "reconciliation": (
                self.reconciliation.to_obj() if self.reconciliation else None
            ),
            "scope": self.scope.to_obj(),
            "queries": [q.to_obj() for q in self.queries],
            "gaps": list(self.gaps),
            "warnings": list(self.warnings),
            "allowlist": dict(self.allowlist),
        }

    @classmethod
    def from_obj(cls, obj: dict) -> CrawlResult:
        reconciliation = obj.get("reconciliation")
        return cls(
            database=obj["database"],
            engine=obj["engine"],
            crawl_date=_as_date(obj["crawl_date"]),
            engine_verified=obj.get("engine_verified", True),
            tables=[Table.from_obj(o) for o in obj.get("tables", [])],
            columns=[Column.from_obj(o) for o in obj.get("columns", [])],
            constraints=[Constraint.from_obj(o) for o in obj.get("constraints", [])],
            indexes=[Index.from_obj(o) for o in obj.get("indexes", [])],
            table_stats=[TableStats.from_obj(o) for o in obj.get("table_stats", [])],
            column_stats=[ColumnStats.from_obj(o) for o in obj.get("column_stats", [])],
            measured=obj.get("measured", False),
            table_profiles=[
                TableProfile.from_obj(o) for o in obj.get("table_profiles", [])
            ],
            column_profiles=[
                ColumnProfile.from_obj(o) for o in obj.get("column_profiles", [])
            ],
            reconciliation=(
                Reconciliation.from_obj(reconciliation) if reconciliation else None
            ),
            scope=Scope.from_obj(obj.get("scope") or {}),
            queries=[QueryRun.from_obj(o) for o in obj.get("queries", [])],
            gaps=list(obj.get("gaps", [])),
            warnings=list(obj.get("warnings", [])),
            allowlist=dict(obj.get("allowlist", {})),
        )
