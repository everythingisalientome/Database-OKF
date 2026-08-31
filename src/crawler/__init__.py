"""crawler — step 1, the deterministic half.

Reads one legacy database through a fixed catalog of pre-declared queries and
writes what it found as JSON. The crawl itself is an extractor, not an agent:
it composes no SQL, makes no inferences, and asks no model anything. The
annotation pass (:mod:`crawler.annotate`) is the one place a model speaks —
over derived artifacts only, sanitized in code — and emission
(:mod:`crawler.emit`) turns crawl plus annotations into the OKF bundle.
Keeping the three apart means the crawl can be re-run, diffed and audited on
its own, and the annotator consumes a file rather than a live connection.

    >>> from crawler import CrawlConfig, connect, crawl
    >>> config = CrawlConfig.load("rig/config/chinook-postgres.json")
    >>> result = crawl(connect(config), config)
    >>> result.completeness
    'COMPLETE'

Layers, lowest first:
    types       -- the canonical type vocabulary
    schemas     -- engine system schemas, never crawled
    catalog     -- the only SQL that exists, verbatim from catalog/
    gaps        -- queries the catalog lacks, proposed not invented
    results     -- what a crawl produces
    allowlist   -- identifiers Tier B/C may interpolate, and nothing else
    bind        -- the only code that fills a Tier B/C template
    normalize   -- value normalization, and the record of what it did
    temporal    -- the canonical temporal rendering (adopted P15)
    formats     -- format-pattern classification (adopted P14)
    fingerprint -- keyed hashing; unkeyed only in fixture mode
    profile     -- derived rates, flags and the cardinality gates
    adapters    -- per-engine row readers (postgres, sqlserver, teradata)
    execute     -- statement to cursor
    measure     -- the Tier B/C run, B1..B2..B3..C1
    crawl       -- the Tier A run, A1..A6, optionally measure, then A5
    annotate    -- the annotation pass: views, the LLM seam, the sanitizer
    llm         -- the seam's one concrete plug: OpenAI-compatible endpoints
    emit        -- crawl result + annotations -> OKF bundle on disk
    diff        -- the refresh diff: change report + stale manifest
    config      -- one file per database
    connect     -- driver selection
    cli         -- python -m crawler
"""

from .allowlist import AllowList, Rejection, is_bare_identifier
from .annotate import (
    INSUFFICIENT,
    Annotations,
    ColumnAnnotation,
    ColumnView,
    DatabaseAnnotation,
    DatabaseView,
    LLMAnnotator,
    NullAnnotator,
    TableAnnotation,
    TableView,
    annotate,
    database_view,
    table_views,
)
from .bind import batches, bind
from .catalog import (
    ENGINES,
    QUERY_IDS,
    SAMPLE_CAP,
    STATEMENTS,
    TEMPLATES,
    TIER_A,
    TIER_BC,
    TOP_N,
    Statement,
    Template,
    all_statements,
    all_templates,
    statement,
    statements_for,
    template,
    templates_for,
)
from .config import CrawlConfig, MeasureSettings, SchemaFilter
from .connect import connect
from .crawl import crawl
from .diff import (
    MANIFEST_CONTRACT,
    MANIFEST_FORMAT,
    BundleSnapshot,
    DiffTolerance,
    RefreshDiff,
    TableChange,
    diff_bundles,
    diff_snapshots,
    snapshot,
)
from .emit import TOP_VALUES_SHOWN, Emission, PreparedBundle, emit, prepare
from .errors import (
    AdapterError,
    AllowListError,
    AnnotationError,
    ConfigError,
    CrawlerError,
    QueryError,
)
from .execute import run_statement
from .fingerprint import KEYED_ALGO, UNKEYED_ALGO, Hasher, hasher_for
from .formats import FORMATS, classify_column, classify_value
from .gaps import GAPS, CatalogGap, gaps_for
from .llm import annotator_from_env, openai_compatible
from .measure import measure
from .normalize import RULES, Normalized, normalize_sample
from .temporal import parse_temporal, render_temporal
from .results import (
    COMPLETE,
    INCOMPLETE,
    UNVERIFIED,
    Column,
    ColumnProfile,
    ColumnStats,
    Constraint,
    CrawlResult,
    Fingerprint,
    Index,
    QueryRun,
    Reconciliation,
    Scope,
    Table,
    TableProfile,
    TableStats,
    TopValue,
)
from .schemas import SYSTEM_SCHEMAS, is_system_schema
from .types import canonical_type

__version__ = "0.1.0"

__all__ = [
    "AllowList", "Rejection", "is_bare_identifier",
    "INSUFFICIENT", "Annotations", "ColumnAnnotation", "ColumnView",
    "DatabaseAnnotation", "DatabaseView", "LLMAnnotator", "NullAnnotator",
    "TableAnnotation", "TableView", "annotate", "database_view", "table_views",
    "TOP_VALUES_SHOWN", "Emission", "PreparedBundle", "emit", "prepare",
    "bind", "batches",
    "ENGINES", "QUERY_IDS", "SAMPLE_CAP", "STATEMENTS", "TEMPLATES",
    "TIER_A", "TIER_BC", "TOP_N", "Statement", "Template",
    "all_statements", "all_templates", "statement", "statements_for",
    "template", "templates_for",
    "CrawlConfig", "MeasureSettings", "SchemaFilter",
    "MANIFEST_CONTRACT", "MANIFEST_FORMAT", "BundleSnapshot", "DiffTolerance",
    "RefreshDiff", "TableChange", "diff_bundles", "diff_snapshots", "snapshot",
    "connect", "crawl", "measure", "run_statement",
    "KEYED_ALGO", "UNKEYED_ALGO", "Hasher", "hasher_for",
    "FORMATS", "classify_column", "classify_value",
    "parse_temporal", "render_temporal",
    "RULES", "Normalized", "normalize_sample",
    "AdapterError", "AllowListError", "AnnotationError", "ConfigError",
    "CrawlerError", "QueryError",
    "GAPS", "CatalogGap", "gaps_for",
    "annotator_from_env", "openai_compatible",
    "COMPLETE", "INCOMPLETE", "UNVERIFIED",
    "Column", "ColumnProfile", "ColumnStats", "Constraint", "CrawlResult",
    "Fingerprint", "Index", "QueryRun", "Reconciliation", "Scope", "Table",
    "TableProfile", "TableStats", "TopValue",
    "SYSTEM_SCHEMAS", "is_system_schema",
    "canonical_type",
    "__version__",
]
