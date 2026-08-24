"""Crawl configuration — one file per database, one run per file.

Only what Tier A actually uses lives here. The measuring pass (session 3)
adds its own keys — sensitive-column list, cardinality thresholds, sample
size — when it has code that reads them; a field nothing reads is a promise
the crawler does not keep.

Secrets are never config values. ``connection.password_env`` names an
environment variable; the crawler reads the variable, and the config file
stays committable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import ENGINES
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
        }

    @classmethod
    def from_obj(cls, obj: dict) -> CrawlConfig:
        unknown = set(obj) - {
            "database", "engine", "schemas", "expected_table_count", "connection",
        }
        if unknown:
            raise ConfigError(
                f"unknown config keys: {', '.join(sorted(unknown))}. "
                "Tier A reads only database, engine, schemas, "
                "expected_table_count and connection."
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
        )

    @classmethod
    def load(cls, path) -> CrawlConfig:
        text = Path(path).read_text(encoding="utf-8")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from None
        return cls.from_obj(obj)
