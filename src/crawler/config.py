"""Crawl configuration — one file per database, one run per file.

Tier A's keys are the top level; the measuring pass reads
:class:`MeasureSettings` under ``measure``. Every key here is read by code —
a field nothing reads is a promise the crawler does not keep — and unknown
keys are an error rather than a typo that silently does nothing.

Secrets are never config values. ``connection.password_env`` names an
environment variable; the crawler reads the variable, and the config file
stays committable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import ENGINES, SAMPLE_CAP, TOP_N
from .errors import ConfigError


@dataclass(frozen=True)
class SchemaFilter:
    """Which schemas a run catalogs.

    Tier A statements never carry a schema predicate — they run verbatim and
    the rows are filtered here, in Python. That keeps config out of the SQL.
    Comparison is case-insensitive because config is written by people and
    engines disagree about identifier case.
    """

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    def allows(self, schema: str) -> bool:
        folded = str(schema).casefold()
        if any(folded == x.casefold() for x in self.exclude):
            return False
        if self.include:
            return any(folded == i.casefold() for i in self.include)
        return True

    @property
    def is_filtered(self) -> bool:
        """True when the run sees less than the whole account view.

        Reconciliation depends on this: A5 counts every base table the
        account can see, which is only comparable with the cataloged count
        when nothing was filtered out.
        """
        return bool(self.include or self.exclude)

    def to_obj(self) -> dict:
        return {"include": list(self.include), "exclude": list(self.exclude)}

    @classmethod
    def from_obj(cls, obj: dict | None) -> SchemaFilter:
        obj = obj or {}
        return cls(
            include=tuple(obj.get("include") or ()),
            exclude=tuple(obj.get("exclude") or ()),
        )


@dataclass(frozen=True)
class MeasureSettings:
    """What the measuring pass needs to know, all of it optional with a default.

    The defaults are the spec's: fingerprint gate at distinct_ratio > 0.5 or
    index/constraint membership, top-N stored only below 30 distinct values,
    top-N of 20, a bottom-k fingerprint sample of 5000, statistics believed
    for 90 days. A config that names none of these gets exactly the behaviour
    specs/01 describes.

    Two keys are the compliance surface and deserve reading twice.
    ``sensitive_columns`` excludes a column from top-N and from fingerprints
    entirely — its numbers are still measured, since a null rate is not a
    value, but nothing derived from its contents is persisted in any form.
    ``fixture_mode`` turns off keyed hashing, and exists only so the
    fixture bundles in this repository can be reproduced by anyone; a
    production crawl leaves it alone and names the vault key's environment
    variable in ``fingerprint_key_env``.
    """

    #: ``column``, ``table.column`` or ``schema.table.column``, case-blind.
    sensitive_columns: tuple[str, ...] = ()
    #: C1 runs above this distinct_ratio, or on any indexed column.
    distinct_ratio_gate: float = 0.5
    #: B3 runs at or below this many distinct values.
    top_n_distinct_max: int = 30
    #: Rows B3 keeps. Bounded by the catalog block's own literal.
    top_n: int = TOP_N
    #: Values C1 keeps. Bounded by the catalog block's own literal.
    fingerprint_sample: int = SAMPLE_CAP
    #: Columns per B2 statement (the catalog says 10-20).
    batch_columns: int = 20
    #: Dictionary statistics older than this are re-measured.
    stats_max_age_days: int = 90
    #: How close to a gate an estimate has to be to force a scan anyway.
    gate_boundary_band: float = 0.15
    gate_distinct_factor: float = 2.0
    #: dense_sequence: fill of the range, and how far from 1 it may start.
    dense_sequence_fill: float = 0.95
    dense_sequence_start_max: int = 1
    #: Table-name fragments that mark a backup or scratch copy.
    junk_patterns: tuple[str, ...] = ("_BKP", "_TEST", "_OLD", "TMP_")
    #: Budgets. None means unbounded. A budget refusal is recorded on the
    #: profile it denied, never left to look like an absence of data.
    max_scanned_tables: int | None = None
    max_scanned_rows: int | None = None
    max_statements: int | None = None
    #: PostgreSQL's reltuples and friends: believe them, or count.
    trust_estimated_row_counts: bool = False
    #: The adopted P14 ruling: sensitive-listed columns are format-classified
    #: from a transient read, category only. False withdraws even that.
    classify_sensitive_formats: bool = True
    #: Environment variable holding the vault's fingerprint key.
    fingerprint_key_env: str | None = None
    #: Unkeyed, reproducible hashing. Fixtures only — see the class docstring.
    fixture_mode: bool = False

    def __post_init__(self):
        if self.top_n > TOP_N:
            raise ConfigError(
                f"top_n {self.top_n} exceeds the catalog's own literal of "
                f"{TOP_N}; raising it means editing the B3 blocks, not the "
                "config"
            )
        if self.fingerprint_sample > SAMPLE_CAP:
            raise ConfigError(
                f"fingerprint_sample {self.fingerprint_sample} exceeds the "
                f"catalog's own literal of {SAMPLE_CAP}; raising it means "
                "editing the C1 blocks, not the config"
            )
        for name in ("top_n", "fingerprint_sample", "batch_columns"):
            if getattr(self, name) < 1:
                raise ConfigError(f"{name} must be at least 1")
        if self.fixture_mode and self.fingerprint_key_env:
            raise ConfigError(
                "fixture_mode and fingerprint_key_env contradict each other: "
                "one asks for unkeyed hashes, the other supplies a key"
            )

    def is_sensitive(self, schema: str, table: str, column: str) -> bool:
        """True when config lists this column as sensitive.

        Three spellings are accepted because compliance lists arrive in all
        three: a bare column name (every ``ssn`` in the database), a
        ``table.column``, or a fully qualified name. Matching is
        case-insensitive; the lists are written by people.
        """
        candidates = {
            str(column).casefold(),
            f"{table}.{column}".casefold(),
            f"{schema}.{table}.{column}".casefold(),
        }
        return any(str(e).strip().casefold() in candidates for e in self.sensitive_columns)

    def to_obj(self) -> dict:
        return {
            "sensitive_columns": list(self.sensitive_columns),
            "distinct_ratio_gate": self.distinct_ratio_gate,
            "top_n_distinct_max": self.top_n_distinct_max,
            "top_n": self.top_n,
            "fingerprint_sample": self.fingerprint_sample,
            "batch_columns": self.batch_columns,
            "stats_max_age_days": self.stats_max_age_days,
            "gate_boundary_band": self.gate_boundary_band,
            "gate_distinct_factor": self.gate_distinct_factor,
            "dense_sequence_fill": self.dense_sequence_fill,
            "dense_sequence_start_max": self.dense_sequence_start_max,
            "junk_patterns": list(self.junk_patterns),
            "max_scanned_tables": self.max_scanned_tables,
            "max_scanned_rows": self.max_scanned_rows,
            "max_statements": self.max_statements,
            "trust_estimated_row_counts": self.trust_estimated_row_counts,
            "classify_sensitive_formats": self.classify_sensitive_formats,
            "fingerprint_key_env": self.fingerprint_key_env,
            "fixture_mode": self.fixture_mode,
        }

    @classmethod
    def from_obj(cls, obj: dict | None) -> MeasureSettings:
        obj = obj or {}
        defaults = cls()
        unknown = set(obj) - set(defaults.to_obj())
        if unknown:
            raise ConfigError(
                f"unknown measure keys: {', '.join(sorted(unknown))}"
            )
        tuples = ("sensitive_columns", "junk_patterns")
        values = {}
        for key, default in defaults.to_obj().items():
            value = obj.get(key, getattr(defaults, key))
            values[key] = tuple(value) if key in tuples else value
        return cls(**values)


@dataclass(frozen=True)
class CrawlConfig:
    """Everything one Tier A crawl needs to know."""

    database: str
    engine: str
    schemas: SchemaFilter = field(default_factory=SchemaFilter)
    #: Table count the DBA says should be there. Without it, a filtered crawl
    #: cannot be reconciled and the bundle is flagged UNVERIFIED.
    expected_table_count: int | None = None
    #: Driver-specific connection settings. No secrets — see module docstring.
    connection: dict = field(default_factory=dict)
    #: Everything the measuring pass reads.
    measure: MeasureSettings = field(default_factory=lambda: MeasureSettings())

    def __post_init__(self):
        if not self.database:
            raise ConfigError("config needs a database name")
        if self.engine not in ENGINES:
            raise ConfigError(
                f"unsupported engine {self.engine!r}; "
                f"this crawler supports {', '.join(ENGINES)}"
            )
        if self.expected_table_count is not None and self.expected_table_count < 0:
            raise ConfigError("expected_table_count cannot be negative")

    def includes_schema(self, schema: str) -> bool:
        return self.schemas.allows(schema)

    def to_obj(self) -> dict:
        return {
            "database": self.database,
            "engine": self.engine,
            "schemas": self.schemas.to_obj(),
            "expected_table_count": self.expected_table_count,
            "connection": dict(self.connection),
            "measure": self.measure.to_obj(),
        }

    @classmethod
    def from_obj(cls, obj: dict) -> CrawlConfig:
        unknown = set(obj) - {
            "database", "engine", "schemas", "expected_table_count",
            "connection", "measure",
        }
        if unknown:
            raise ConfigError(
                f"unknown config keys: {', '.join(sorted(unknown))}. "
                "A crawl config holds database, engine, schemas, "
                "expected_table_count, connection and measure."
            )
        try:
            database = obj["database"]
            engine = obj["engine"]
        except KeyError as exc:
            raise ConfigError(f"config is missing {exc.args[0]!r}") from None
        return cls(
            database=database,
            engine=engine,
            schemas=SchemaFilter.from_obj(obj.get("schemas")),
            expected_table_count=obj.get("expected_table_count"),
            connection=dict(obj.get("connection") or {}),
            measure=MeasureSettings.from_obj(obj.get("measure")),
        )

    @classmethod
    def load(cls, path) -> CrawlConfig:
        text = Path(path).read_text(encoding="utf-8")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from None
        return cls.from_obj(obj)
