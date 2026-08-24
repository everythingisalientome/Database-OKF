"""crawler — step 1, the deterministic half.

Reads one legacy database through a fixed catalog of pre-declared queries and
writes what it found as JSON. It is an extractor, not an agent: it composes
no SQL, makes no inferences, and asks no model anything. Session 4 adds the
annotation pass that turns this result into an OKF bundle; keeping the two
apart means the crawl can be re-run, diffed and audited on its own.

    >>> from crawler import CrawlConfig, connect, crawl
    >>> config = CrawlConfig.load("rig/config/chinook-postgres.json")
    >>> result = crawl(connect(config), config)
    >>> result.completeness
    'COMPLETE'

Layers, lowest first:
    types      -- the canonical type vocabulary
    schemas    -- engine system schemas, never crawled
    catalog    -- the only SQL that exists, verbatim from catalog/
    gaps       -- queries the catalog lacks, proposed not invented
    results    -- what a crawl produces
    allowlist  -- identifiers Tier B/C may interpolate, and nothing else
    adapters   -- per-engine row readers (postgres, sqlserver, teradata)
    execute    -- statement to cursor
    crawl      -- the Tier A run, A1..A6 then A5
    config     -- one file per database
    connect    -- driver selection
    cli        -- python -m crawler
"""

from .allowlist import AllowList, Rejection, is_bare_identifier
from .catalog import (
    ENGINES,
    QUERY_IDS,
    STATEMENTS,
    TIER_A,
    Statement,
    all_statements,
    statement,
    statements_for,
)
from .config import CrawlConfig, SchemaFilter
from .connect import connect
from .crawl import crawl
from .errors import (
    AdapterError,
    AllowListError,
    ConfigError,
    CrawlerError,
    QueryError,
)
from .execute import run_statement
from .gaps import GAPS, CatalogGap, gaps_for
from .results import (
    COMPLETE,
    INCOMPLETE,
    UNVERIFIED,
    Column,
    ColumnStats,
    Constraint,
    CrawlResult,
    Index,
    QueryRun,
    Reconciliation,
    Scope,
    Table,
    TableStats,
)
from .schemas import SYSTEM_SCHEMAS, is_system_schema
from .types import canonical_type

__version__ = "0.1.0"

__all__ = [
    "AllowList", "Rejection", "is_bare_identifier",
    "ENGINES", "QUERY_IDS", "STATEMENTS", "TIER_A", "Statement",
    "all_statements", "statement", "statements_for",
    "CrawlConfig", "SchemaFilter",
    "connect", "crawl", "run_statement",
    "AdapterError", "AllowListError", "ConfigError", "CrawlerError", "QueryError",
    "GAPS", "CatalogGap", "gaps_for",
    "COMPLETE", "INCOMPLETE", "UNVERIFIED",
    "Column", "ColumnStats", "Constraint", "CrawlResult", "Index", "QueryRun",
    "Reconciliation", "Scope", "Table", "TableStats",
    "SYSTEM_SCHEMAS", "is_system_schema",
    "canonical_type",
    "__version__",
]
