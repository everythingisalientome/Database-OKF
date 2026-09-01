"""The only SQL this crawler may issue.

Every statement below is a verbatim copy of a fenced block in
``catalog/step1-query-catalog.md``. The copy is deliberate duplication: the
runtime stays a plain Python package with no markdown parsing and no data
files to ship, while ``tests/crawler/test_catalog.py`` re-extracts the blocks
from the catalog on every test run and fails the build if a single character
drifts. Adding SQL here that is not in the catalog fails that test — which is
the point. Queries the catalog lacks are proposed in ``catalog/proposals/``
and recorded in :mod:`crawler.gaps`, never quietly invented here.

Statements are addressed by ``key``, not by catalog query id, because one
query id can have more than one block for an engine: SQL Server answers A6
with two statements, one for table row counts and one for per-column
histogram distincts. Each statement declares what its rows ``provides`` so
the crawl knows which reader consumes them.

Tier A statements take no parameters at all. Where a run should see only some
schemas, the statement still runs verbatim and the crawler filters rows in
Python (:mod:`crawler.schemas`, :meth:`crawler.config.CrawlConfig.includes_schema`);
building a WHERE clause out of config would make the SQL dynamic.
"""

from __future__ import annotations

from dataclasses import dataclass

#: What a statement's rows can be read as.
TABLES = "tables"
COLUMNS = "columns"
CONSTRAINTS = "constraints"
INDEXES = "indexes"
TABLE_STATS = "table_stats"
COLUMN_STATS = "column_stats"
CODE_OBJECTS = "code_objects"
EXTERNAL_REFS = "external_refs"
RECONCILIATION = "reconciliation"

#: Statement keys in the order the spec fixes: inventory first, A7/A8 after
#: A4 (the catalog's run-order line), A5 last.
TIER_A = (
    "A1", "A2", "A3", "A4",
    "A7", "A7-routines", "A8", "A8-synonyms",
    "A6", "A6-columns", "A5",
)

#: Catalog query ids. Every engine needs a block for each of these; several
#: keys can implement one id (SQL Server answers A6 with two blocks), and one
#: block can answer for another id (see :data:`ANSWERED_ELSEWHERE`).
QUERY_IDS = ("A1", "A2", "A3", "A4", "A7", "A8", "A6", "A5")

#: Query ids an engine answers through another query's block rather than a
#: block of their own. The catalog, on Teradata's A8: "foreign-server objects
#: surface in A7's TableKind kinds" — the A7-TD block selects TableKind 'E'
#: and its reader emits those rows as external references, so there is no
#: separate A8 statement to run and nothing is skipped or gapped.
ANSWERED_ELSEWHERE: dict[tuple[str, str], str] = {("teradata", "A8"): "A7"}

#: Keys without which there is no crawl at all.
REQUIRED = ("A1", "A2")

#: Runtime engines this crawler supports.
ENGINES = ("postgres", "sqlserver", "teradata")

#: Engines whose adapter has never been exercised against a live system.
UNVERIFIED_ENGINES = ("teradata",)


@dataclass(frozen=True)
class Statement:
    """One catalog SQL block, addressed by the query it implements."""

    #: Unique per engine. Usually the catalog query id; ``A6-columns`` where
    #: one query id has two blocks.
    key: str
    #: Catalog query id this block implements (``A6`` for both A6 blocks).
    query_id: str
    #: Which catalog variant this block is: ``ansi``/``postgres``/
    #: ``sqlserver``/``teradata``.
    variant: str
    #: Verbatim block text, exactly as the catalog has it.
    sql: str
    #: Heading the block sits under in the catalog, for traceability.
    heading: str
    #: What the rows can be read as — see the constants above.
    provides: tuple[str, ...]
    #: Identifier placeholders. Tier A has none; Tier B/C fill this in later.
    parameters: tuple[str, ...] = ()

    @property
    def is_parameterised(self) -> bool:
        return bool(self.parameters)


A1_ANSI = Statement(
    key="A1",
    query_id="A1",
    variant="ansi",
    heading="A1. Table inventory",
    provides=(TABLES,),
    sql="""SELECT table_catalog, table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_type IN ('BASE TABLE', 'VIEW');""",
)

A2_ANSI = Statement(
    key="A2",
    query_id="A2",
    variant="ansi",
    heading="A2. Column inventory",
    provides=(COLUMNS,),
    sql="""SELECT table_schema, table_name, column_name, ordinal_position,
       data_type, character_maximum_length, numeric_precision,
       numeric_scale, is_nullable, column_default
FROM information_schema.columns;""",
)

A3_ANSI = Statement(
    key="A3",
    query_id="A3",
    variant="ansi",
    heading=(
        "A3. Declared constraints (PK / FK / unique — often absent in "
        "legacy, capture when present)"
    ),
    provides=(CONSTRAINTS,),
    sql="""SELECT tc.constraint_type, tc.table_schema, tc.table_name, tc.constraint_name,
       kcu.column_name, kcu.ordinal_position,
       rc.unique_constraint_schema, rc.unique_constraint_name,
       ref_tc.table_schema AS referenced_schema,
       ref_tc.table_name   AS referenced_table
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON  kcu.constraint_name   = tc.constraint_name
  AND kcu.constraint_schema = tc.constraint_schema
LEFT JOIN information_schema.referential_constraints rc
  ON  rc.constraint_name   = tc.constraint_name
  AND rc.constraint_schema = tc.constraint_schema
LEFT JOIN information_schema.table_constraints ref_tc
  ON  ref_tc.constraint_name   = rc.unique_constraint_name
  AND ref_tc.constraint_schema = rc.unique_constraint_schema;""",
)

A4_POSTGRES = Statement(
    key="A4",
    query_id="A4",
    variant="postgres",
    heading="A4. Indexes (join-intent signal where FKs are undeclared)",
    provides=(INDEXES,),
    sql="""SELECT n.nspname AS table_schema, t.relname AS table_name,
       i.relname AS index_name, ix.indisunique AS is_unique,
       a.attname AS column_name, k.ordinality AS key_ordinal,
       (k.ordinality > ix.indnkeyatts) AS is_included
FROM pg_index ix
JOIN pg_class i ON i.oid = ix.indexrelid
JOIN pg_class t ON t.oid = ix.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ordinality) ON TRUE
JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
WHERE t.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema');""",
)

A4_SQLSERVER = Statement(
    key="A4",
    query_id="A4",
    variant="sqlserver",
    heading="A4. Indexes (join-intent signal where FKs are undeclared)",
    provides=(INDEXES,),
    sql="""SELECT s.name AS table_schema, t.name AS table_name, i.name AS index_name,
       i.is_unique, c.name AS column_name, ic.key_ordinal
FROM sys.indexes i
JOIN sys.tables t ON i.object_id = t.object_id
JOIN sys.schemas s ON t.schema_id = s.schema_id
JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id;""",
)

A6_POSTGRES = Statement(
    key="A6",
    query_id="A6",
    variant="postgres",
    heading=(
        "A6. Column statistics from the dictionary (STATS-FIRST — run "
        "before Tier B)"
    ),
    # One block, both readers: pg_class carries the table row estimate and
    # the outer-joined pg_stats the per-column numbers, which is why a table
    # with no column statistics still reports its estimate.
    provides=(TABLE_STATS, COLUMN_STATS),
    sql="""SELECT n.nspname AS table_schema, c.relname AS table_name,
       s.attname AS column_name, s.n_distinct, s.null_frac,
       c.reltuples::bigint AS est_rows,
       GREATEST(st.last_analyze, st.last_autoanalyze) AS stats_date
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stats s ON s.schemaname = n.nspname AND s.tablename = c.relname
LEFT JOIN pg_stat_all_tables st ON st.relid = c.oid
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema');""",
)

A6_SQLSERVER = Statement(
    key="A6",
    query_id="A6",
    variant="sqlserver",
    heading=(
        "A6. Column statistics from the dictionary (STATS-FIRST — run "
        "before Tier B)"
    ),
    provides=(TABLE_STATS,),
    sql="""SELECT s.name AS table_schema, t.name AS table_name, p.row_count
FROM sys.tables t
JOIN sys.schemas s ON t.schema_id = s.schema_id
JOIN sys.dm_db_partition_stats p ON t.object_id = p.object_id
WHERE p.index_id IN (0,1);""",
)

A6_COLUMNS_SQLSERVER = Statement(
    key="A6-columns",
    query_id="A6",
    variant="sqlserver",
    heading=(
        "A6. Column statistics from the dictionary (STATS-FIRST — run "
        "before Tier B)"
    ),
    provides=(COLUMN_STATS,),
    sql="""SELECT sch.name AS table_schema, t.name AS table_name, c.name AS column_name,
       sp.rows, sp.rows_sampled, sp.last_updated,
       SUM(CASE WHEN h.distinct_range_rows > 0 THEN h.distinct_range_rows ELSE 0 END)
         + COUNT(h.step_number) AS approx_distinct
FROM sys.stats st
JOIN sys.stats_columns sc ON st.object_id = sc.object_id AND st.stats_id = sc.stats_id
JOIN sys.columns c ON c.object_id = sc.object_id AND c.column_id = sc.column_id
JOIN sys.tables t ON t.object_id = st.object_id
JOIN sys.schemas sch ON sch.schema_id = t.schema_id
CROSS APPLY sys.dm_db_stats_properties(st.object_id, st.stats_id) sp
CROSS APPLY sys.dm_db_stats_histogram(st.object_id, st.stats_id) h
WHERE sc.stats_column_id = 1
GROUP BY sch.name, t.name, c.name, sp.rows, sp.rows_sampled, sp.last_updated;""",
)

_A7_HEADING = (
    "A7. Code-object definitions (views / procedures / triggers — "
    "join-intent text)"
)
_A8_HEADING = (
    "A8. Cross-database reference inventory (recorded lineage between SORs)"
)

A7_VIEWS_POSTGRES = Statement(
    key="A7",
    query_id="A7",
    variant="postgres",
    heading=_A7_HEADING,
    provides=(CODE_OBJECTS,),
    sql="""SELECT table_schema, table_name, view_definition FROM information_schema.views
WHERE table_schema NOT IN ('pg_catalog', 'information_schema');""",
)

A7_ROUTINES_POSTGRES = Statement(
    key="A7-routines",
    query_id="A7",
    variant="postgres",
    heading=_A7_HEADING,
    provides=(CODE_OBJECTS,),
    sql="""SELECT routine_schema, routine_name, routine_type, routine_definition
FROM information_schema.routines
WHERE routine_schema NOT IN ('pg_catalog', 'information_schema');""",
)

A7_SQLSERVER = Statement(
    key="A7",
    query_id="A7",
    variant="sqlserver",
    heading=_A7_HEADING,
    provides=(CODE_OBJECTS,),
    sql="""SELECT s.name AS table_schema, o.name AS object_name, o.type AS object_type,
       m.definition
FROM sys.sql_modules m
JOIN sys.objects o ON o.object_id = m.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE o.type IN ('V', 'P', 'FN', 'TF', 'IF', 'TR');""",
)

A7_TERADATA = Statement(
    key="A7",
    query_id="A7",
    variant="teradata",
    heading=_A7_HEADING,
    # One block, both readers: TableKind 'V'/'M'/'P' rows are code objects
    # and 'E' rows are foreign-server objects — the catalog's answer to A8
    # on this engine (see ANSWERED_ELSEWHERE).
    provides=(CODE_OBJECTS, EXTERNAL_REFS),
    sql="""SELECT DatabaseName, TableName, TableKind, RequestText
FROM DBC.TablesV
WHERE TableKind IN ('V', 'M', 'P', 'E');""",
)

A8_POSTGRES = Statement(
    key="A8",
    query_id="A8",
    variant="postgres",
    heading=_A8_HEADING,
    provides=(EXTERNAL_REFS,),
    sql="""SELECT s.srvname, w.fdwname FROM pg_foreign_server s
JOIN pg_foreign_data_wrapper w ON w.oid = s.srvfdw;""",
)

A8_SQLSERVER = Statement(
    key="A8",
    query_id="A8",
    variant="sqlserver",
    heading=_A8_HEADING,
    provides=(EXTERNAL_REFS,),
    sql="""SELECT name, data_source, provider FROM sys.servers WHERE is_linked = 1;""",
)

A8_SYNONYMS_SQLSERVER = Statement(
    key="A8-synonyms",
    query_id="A8",
    variant="sqlserver",
    heading=_A8_HEADING,
    provides=(EXTERNAL_REFS,),
    sql="""SELECT s.name AS schema_name, sy.name AS synonym_name, sy.base_object_name
FROM sys.synonyms sy JOIN sys.schemas s ON s.schema_id = sy.schema_id;""",
)

A5_ANSI = Statement(
    key="A5",
    query_id="A5",
    variant="ansi",
    heading="A5. Reconciliation (run last — detects grant gaps)",
    provides=(RECONCILIATION,),
    sql="""SELECT COUNT(*) AS visible_tables FROM information_schema.tables
WHERE table_type = 'BASE TABLE';""",
)

A1_TERADATA = Statement(
    key="A1",
    query_id="A1",
    variant="teradata",
    heading="A1-TD. Table inventory",
    provides=(TABLES,),
    sql="""SELECT DatabaseName AS table_schema, TableName AS table_name, TableKind
FROM DBC.TablesV
WHERE TableKind IN ('T', 'O', 'V');""",
)

A2_TERADATA = Statement(
    key="A2",
    query_id="A2",
    variant="teradata",
    heading="A2-TD. Column inventory",
    provides=(COLUMNS,),
    sql="""SELECT DatabaseName, TableName, ColumnName, ColumnId,
       ColumnType, ColumnLength, DecimalTotalDigits, DecimalFractionalDigits,
       Nullable, DefaultValue
FROM DBC.ColumnsV;""",
)

A3_TERADATA = Statement(
    key="A3",
    query_id="A3",
    variant="teradata",
    heading="A3-TD. Constraints",
    provides=(CONSTRAINTS,),
    sql="""SELECT DatabaseName, TableName, IndexName, IndexNumber, IndexType,
       ColumnName, ColumnPosition
FROM DBC.IndicesV
WHERE IndexType IN ('K', 'U');  -- K = primary key, U = unique — rare on legacy""",
)

A4_TERADATA = Statement(
    key="A4",
    query_id="A4",
    variant="teradata",
    heading=(
        "A4-TD. Indexes, including the Primary Index — read this one "
        "carefully"
    ),
    provides=(INDEXES,),
    sql="""SELECT DatabaseName, TableName, IndexName, IndexNumber, IndexType, UniqueFlag,
       ColumnName, ColumnPosition
FROM DBC.IndicesV;""",
)

A6_TERADATA = Statement(
    key="A6",
    query_id="A6",
    variant="teradata",
    heading=(
        "A6. Column statistics from the dictionary (STATS-FIRST — run "
        "before Tier B)"
    ),
    provides=(TABLE_STATS, COLUMN_STATS),
    sql="""SELECT DatabaseName, TableName, ColumnName, RowCount, UniqueValueCount,
       NullCount, LastCollectTimeStamp
FROM DBC.StatsV;""",
)

A5_TERADATA = Statement(
    key="A5",
    query_id="A5",
    variant="teradata",
    heading="A5. Reconciliation (run last — detects grant gaps)",
    provides=(RECONCILIATION,),
    sql="""SELECT COUNT(*) AS visible_tables
FROM DBC.TablesV
WHERE TableKind IN ('T', 'O');""",
)

#: engine -> statement key -> statement. A key absent for an engine means the
#: catalog has no block for it; the crawler records a gap instead of guessing.
STATEMENTS: dict[str, dict[str, Statement]] = {
    "postgres": {
        "A1": A1_ANSI,
        "A2": A2_ANSI,
        "A3": A3_ANSI,
        "A4": A4_POSTGRES,
        "A7": A7_VIEWS_POSTGRES,
        "A7-routines": A7_ROUTINES_POSTGRES,
        "A8": A8_POSTGRES,
        "A6": A6_POSTGRES,
        "A5": A5_ANSI,
    },
    "sqlserver": {
        "A1": A1_ANSI,
        "A2": A2_ANSI,
        "A3": A3_ANSI,
        "A4": A4_SQLSERVER,
        "A7": A7_SQLSERVER,
        "A8": A8_SQLSERVER,
        "A8-synonyms": A8_SYNONYMS_SQLSERVER,
        "A6": A6_SQLSERVER,
        "A6-columns": A6_COLUMNS_SQLSERVER,
        "A5": A5_ANSI,
    },
    "teradata": {
        "A1": A1_TERADATA,
        "A2": A2_TERADATA,
        "A3": A3_TERADATA,
        "A4": A4_TERADATA,
        "A7": A7_TERADATA,
        "A6": A6_TERADATA,
        "A5": A5_TERADATA,
    },
}


def covered_query_ids(engine: str) -> set[str]:
    """Query ids ``engine`` can answer: its own blocks, plus the ids the
    catalog documents as answered inside another query's block."""
    covered = {stmt.query_id for stmt in statements_for(engine)}
    for (gap_engine, query_id), via in ANSWERED_ELSEWHERE.items():
        if gap_engine == engine and via in covered:
            covered.add(query_id)
    return covered


def statement(engine: str, key: str) -> Statement | None:
    """The catalog block ``key`` on ``engine``, or None.

    None is a first-class answer: the catalog does not cover that query for
    that engine, and the crawler must record the gap rather than improvise.
    """
    return STATEMENTS.get(engine, {}).get(key)


def statements_for(engine: str) -> list[Statement]:
    """Every statement ``engine`` runs, in Tier A execution order."""
    by_key = STATEMENTS.get(engine, {})
    return [by_key[key] for key in TIER_A if key in by_key]


def all_statements() -> list[Statement]:
    """Every distinct registered statement, deduplicated."""
    seen: list[Statement] = []
    for by_key in STATEMENTS.values():
        for stmt in by_key.values():
            if stmt not in seen:
                seen.append(stmt)
    return seen


# ---------------------------------------------------------------------------
# Tier B / Tier C — the measuring pass
#
# These blocks carry identifier placeholders, which is the whole difference
# from Tier A: nothing may fill one unless the crawl's own A1/A2 output
# produced it. :mod:`crawler.bind` is the only place that fills them, and
# :func:`crawler.execute.run_statement` refuses a parameterised statement
# outright, so a template cannot reach a cursor unbound.
#
# Two blocks in the catalog are written as a two-column example with a
# ``/* ... chunked ... */`` comment rather than as a finished statement,
# because batching is the point: one scan profiles many columns. They are
# registered as a :class:`Batch` — a prefix, a per-column fragment, and a
# suffix — and ``tests/crawler/test_catalog.py`` renders each for the two
# columns the catalog names and asserts the result is the catalog block,
# character for character. The expander is a transcription of the block, and
# the test is what keeps it one.
# ---------------------------------------------------------------------------

#: What a template's rows can be read as.
ROW_COUNT = "row_count"
COLUMN_AGGREGATES = "column_aggregates"
COLUMN_LENGTHS = "column_lengths"
TOP_VALUES = "top_values"
FORMAT_SAMPLE = "format_sample"
VALUE_SAMPLE = "value_sample"

#: Template keys in the catalog's run order: B1, B2, B3, B4, C1.
TIER_B = ("B1", "B2", "B2-length", "B3", "B4")
TIER_C = ("C1", "C1-cast")
TIER_BC = TIER_B + TIER_C

#: Catalog query ids the templates implement.
TEMPLATE_IDS = ("B1", "B2", "B3", "B4", "C1")

#: Sample ceiling written into the C1 blocks. A configured ``k`` below this is
#: applied to the returned rows (they arrive in selection-hash order, so the
#: first k of them are the bottom-k); a configured ``k`` above it cannot be
#: honoured without editing the catalog, and is a configuration error.
SAMPLE_CAP = 5000

#: Top-N size written into the B3 blocks, for the same reason.
TOP_N = 20

#: Sample ceiling written into the B4 blocks. Classification is a plurality
#: judgement; 500 distinct values decide it as well as 5000 would, at a tenth
#: of the transfer, and the values are dropped the moment they are classified.
FORMAT_CAP = 500

#: The column names the catalog's batched examples use, in order. The
#: expander must reproduce the block exactly for these.
BATCH_EXAMPLE_COLUMNS = ("c1", "c2")


@dataclass(frozen=True)
class Batch:
    """A batched block, as prefix + repeated fragment + suffix.

    ``fragment`` holds ``{column}`` once per aggregate. Rendering for the
    catalog's own example columns must reproduce the block verbatim, which is
    what makes this a transcription rather than a paraphrase.
    """

    prefix: str
    fragment: str
    separator: str
    suffix: str
    #: Values one column contributes to the result row.
    width: int

    def render(self, columns) -> str:
        columns = tuple(columns)
        if not columns:
            raise ValueError("a batched statement needs at least one column")
        body = self.separator.join(
            self.fragment.replace("{column}", column) for column in columns
        )
        return f"{self.prefix}{body}{self.suffix}"


@dataclass(frozen=True)
class Template:
    """One Tier B/C catalog block, with its identifier placeholders."""

    key: str
    #: Catalog query id (``C1`` for both C1 blocks).
    query_id: str
    variant: str
    #: Verbatim block text. For a batched block this is the catalog's
    #: two-column example; :attr:`batch` is what renders a real one.
    sql: str
    heading: str
    provides: tuple[str, ...]
    parameters: tuple[str, ...]
    batch: Batch | None = None
    #: True for the blocks that cast a non-character column to VARCHAR.
    casts: bool = False

    @property
    def is_batched(self) -> bool:
        return self.batch is not None

    def render(self, columns=None) -> str:
        """The statement text, still holding ``{schema}``/``{table}``.

        Binding those — and checking every identifier against the crawl's own
        allow-list — is :mod:`crawler.bind`'s job, not this module's.
        """
        if self.batch is None:
            return self.sql
        return self.batch.render(columns or BATCH_EXAMPLE_COLUMNS)


_CHUNK_SUFFIX = (
    "\n       /* ... chunked to stay within engine memory ... */"
    "\nFROM {schema}.{table};"
)

B1_ANSI = Template(
    key="B1",
    query_id="B1",
    variant="ansi",
    heading="B1. Row count (per table)",
    provides=(ROW_COUNT,),
    parameters=("schema", "table"),
    sql="""SELECT COUNT(*) AS row_count FROM {schema}.{table};""",
)

B1_TERADATA = Template(
    key="B1",
    query_id="B1",
    variant="teradata",
    heading="B1-TD. Row count",
    provides=(ROW_COUNT,),
    parameters=("schema", "table"),
    sql="""SELECT COUNT(*) AS row_count FROM {schema}.{table};""",
)

B2_ANSI = Template(
    key="B2",
    query_id="B2",
    variant="ansi",
    heading="B2. Column profile — BATCHED (one scan profiles many columns)",
    provides=(COLUMN_AGGREGATES,),
    parameters=("schema", "table", "columns"),
    sql="""SELECT COUNT(*),
       COUNT(c1), COUNT(DISTINCT c1), MIN(c1), MAX(c1),
       COUNT(c2), COUNT(DISTINCT c2), MIN(c2), MAX(c2)
       /* ... chunked to stay within engine memory ... */
FROM {schema}.{table};""",
    batch=Batch(
        prefix="SELECT COUNT(*),\n       ",
        fragment=(
            "COUNT({column}), COUNT(DISTINCT {column}), "
            "MIN({column}), MAX({column})"
        ),
        separator=",\n       ",
        suffix=_CHUNK_SUFFIX,
        width=4,
    ),
)

B2_LENGTH_ANSI = Template(
    key="B2-length",
    query_id="B2",
    variant="ansi",
    heading="B2. Column profile — BATCHED (one scan profiles many columns)",
    provides=(COLUMN_LENGTHS,),
    parameters=("schema", "table", "columns"),
    sql="""SELECT MIN(LENGTH(c1)), MAX(LENGTH(c1)), AVG(LENGTH(c1)),
       MIN(LENGTH(c2)), MAX(LENGTH(c2)), AVG(LENGTH(c2))
       /* ... chunked to stay within engine memory ... */
FROM {schema}.{table};""",
    batch=Batch(
        prefix="SELECT ",
        fragment=(
            "MIN(LENGTH({column})), MAX(LENGTH({column})), AVG(LENGTH({column}))"
        ),
        separator=",\n       ",
        suffix=_CHUNK_SUFFIX,
        width=3,
    ),
)

B2_LENGTH_SQLSERVER = Template(
    key="B2-length",
    query_id="B2",
    variant="sqlserver",
    heading="B2. Column profile — BATCHED (one scan profiles many columns)",
    provides=(COLUMN_LENGTHS,),
    parameters=("schema", "table", "columns"),
    sql="""SELECT MIN(LEN(c1)), MAX(LEN(c1)), AVG(CAST(LEN(c1) AS float)),
       MIN(LEN(c2)), MAX(LEN(c2)), AVG(CAST(LEN(c2) AS float))
       /* ... chunked to stay within engine memory ... */
FROM {schema}.{table};""",
    batch=Batch(
        prefix="SELECT ",
        fragment=(
            "MIN(LEN({column})), MAX(LEN({column})), "
            "AVG(CAST(LEN({column}) AS float))"
        ),
        separator=",\n       ",
        suffix=_CHUNK_SUFFIX,
        width=3,
    ),
)

B2_LENGTH_TERADATA = Template(
    key="B2-length",
    query_id="B2",
    variant="teradata",
    heading="B2-TD. Column profile",
    provides=(COLUMN_LENGTHS,),
    parameters=("schema", "table", "columns"),
    sql="""SELECT MIN(CHARACTER_LENGTH(c1)), MAX(CHARACTER_LENGTH(c1)), AVG(CHARACTER_LENGTH(c1)),
       MIN(CHARACTER_LENGTH(c2)), MAX(CHARACTER_LENGTH(c2)), AVG(CHARACTER_LENGTH(c2))
       /* ... chunked to stay within engine memory ... */
FROM {schema}.{table};""",
    batch=Batch(
        prefix="SELECT ",
        fragment=(
            "MIN(CHARACTER_LENGTH({column})), MAX(CHARACTER_LENGTH({column})), "
            "AVG(CHARACTER_LENGTH({column}))"
        ),
        separator=",\n       ",
        suffix=_CHUNK_SUFFIX,
        width=3,
    ),
)

B3_ANSI = Template(
    key="B3",
    query_id="B3",
    variant="ansi",
    heading="B3. Top-N frequent values (per column; gated — see note)",
    provides=(TOP_VALUES,),
    parameters=("schema", "table", "column"),
    sql="""SELECT {column} AS value, COUNT(*) AS freq
FROM {schema}.{table}
WHERE {column} IS NOT NULL
GROUP BY {column}
ORDER BY freq DESC
FETCH FIRST 20 ROWS ONLY;""",
)

B3_SQLSERVER = Template(
    key="B3",
    query_id="B3",
    variant="sqlserver",
    heading="B3. Top-N frequent values (per column; gated — see note)",
    provides=(TOP_VALUES,),
    parameters=("schema", "table", "column"),
    sql="""SELECT TOP 20 {column} AS value, COUNT(*) AS freq
FROM {schema}.{table}
WHERE {column} IS NOT NULL
GROUP BY {column}
ORDER BY freq DESC;""",
)

B3_TERADATA = Template(
    key="B3",
    query_id="B3",
    variant="teradata",
    heading="B3-TD. Top-N frequent values",
    provides=(TOP_VALUES,),
    parameters=("schema", "table", "column"),
    sql="""SELECT TOP 20 {column} AS value, COUNT(*) AS freq
FROM {schema}.{table}
WHERE {column} IS NOT NULL
GROUP BY {column}
ORDER BY freq DESC;""",
)

B4_ANSI = Template(
    key="B4",
    query_id="B4",
    variant="ansi",
    heading="B4. Format classification sample (per column; adopted P14)",
    provides=(FORMAT_SAMPLE,),
    parameters=("schema", "table", "column"),
    casts=True,
    sql="""SELECT v, freq FROM (
  SELECT UPPER(TRIM(CAST({column} AS varchar))) AS v, COUNT(*) AS freq
  FROM {schema}.{table} WHERE {column} IS NOT NULL
  GROUP BY UPPER(TRIM(CAST({column} AS varchar)))
) t ORDER BY MD5(v) FETCH FIRST 500 ROWS ONLY;""",
)

B4_SQLSERVER = Template(
    key="B4",
    query_id="B4",
    variant="sqlserver",
    heading="B4. Format classification sample (per column; adopted P14)",
    provides=(FORMAT_SAMPLE,),
    parameters=("schema", "table", "column"),
    casts=True,
    sql="""SELECT TOP 500 v, freq FROM (
  SELECT UPPER(LTRIM(RTRIM(CAST({column} AS nvarchar(4000))))) AS v,
         COUNT(*) AS freq
  FROM {schema}.{table} WHERE {column} IS NOT NULL
  GROUP BY UPPER(LTRIM(RTRIM(CAST({column} AS nvarchar(4000)))))
) t ORDER BY HASHBYTES('MD5', v);""",
)

B4_TERADATA = Template(
    key="B4",
    query_id="B4",
    variant="teradata",
    heading="B4-TD. Format classification sample",
    provides=(FORMAT_SAMPLE,),
    parameters=("schema", "table", "column"),
    casts=True,
    sql="""SELECT v, freq FROM (
  SELECT UPPER(TRIM(CAST({column} AS VARCHAR(4000)))) AS v, COUNT(*) AS freq
  FROM {schema}.{table}
  WHERE {column} IS NOT NULL
  GROUP BY UPPER(TRIM(CAST({column} AS VARCHAR(4000))))
) t
QUALIFY ROW_NUMBER() OVER (ORDER BY HASHROW(v)) <= 500;""",
)

C1_ANSI = Template(
    key="C1",
    query_id="C1",
    variant="ansi",
    heading="C1. Hashed distinct sample (deterministic bottom-k)",
    provides=(VALUE_SAMPLE,),
    parameters=("schema", "table", "column"),
    sql="""SELECT v, raw, freq FROM (
  SELECT UPPER(TRIM({column})) AS v, MIN({column}) AS raw, COUNT(*) AS freq
  FROM {schema}.{table} WHERE {column} IS NOT NULL
  GROUP BY UPPER(TRIM({column}))
) t ORDER BY MD5(v) FETCH FIRST 5000 ROWS ONLY;""",
)

C1_CAST_ANSI = Template(
    key="C1-cast",
    query_id="C1",
    variant="ansi",
    heading="C1. Hashed distinct sample (deterministic bottom-k)",
    provides=(VALUE_SAMPLE,),
    parameters=("schema", "table", "column"),
    casts=True,
    sql="""SELECT v, raw, freq FROM (
  SELECT UPPER(TRIM(CAST({column} AS varchar))) AS v,
         MIN(CAST({column} AS varchar)) AS raw, COUNT(*) AS freq
  FROM {schema}.{table} WHERE {column} IS NOT NULL
  GROUP BY UPPER(TRIM(CAST({column} AS varchar)))
) t ORDER BY MD5(v) FETCH FIRST 5000 ROWS ONLY;""",
)

C1_SQLSERVER = Template(
    key="C1",
    query_id="C1",
    variant="sqlserver",
    heading="C1. Hashed distinct sample (deterministic bottom-k)",
    provides=(VALUE_SAMPLE,),
    parameters=("schema", "table", "column"),
    sql="""SELECT TOP 5000 v, raw, freq FROM (
  SELECT UPPER(LTRIM(RTRIM({column}))) AS v, MIN({column}) AS raw,
         COUNT(*) AS freq
  FROM {schema}.{table} WHERE {column} IS NOT NULL
  GROUP BY UPPER(LTRIM(RTRIM({column})))
) t ORDER BY HASHBYTES('MD5', v);""",
)

C1_CAST_SQLSERVER = Template(
    key="C1-cast",
    query_id="C1",
    variant="sqlserver",
    heading="C1. Hashed distinct sample (deterministic bottom-k)",
    provides=(VALUE_SAMPLE,),
    parameters=("schema", "table", "column"),
    casts=True,
    sql="""SELECT TOP 5000 v, raw, freq FROM (
  SELECT UPPER(LTRIM(RTRIM(CAST({column} AS varchar(4000))))) AS v,
         MIN(CAST({column} AS varchar(4000))) AS raw, COUNT(*) AS freq
  FROM {schema}.{table} WHERE {column} IS NOT NULL
  GROUP BY UPPER(LTRIM(RTRIM(CAST({column} AS varchar(4000)))))
) t ORDER BY HASHBYTES('MD5', v);""",
)

C1_TERADATA = Template(
    key="C1",
    query_id="C1",
    variant="teradata",
    heading="C1-TD. Hashed distinct sample",
    provides=(VALUE_SAMPLE,),
    parameters=("schema", "table", "column"),
    sql="""SELECT v, raw, freq FROM (
  SELECT UPPER(TRIM({column})) AS v, MIN({column}) AS raw, COUNT(*) AS freq
  FROM {schema}.{table}
  WHERE {column} IS NOT NULL
  GROUP BY UPPER(TRIM({column}))
) t
QUALIFY ROW_NUMBER() OVER (ORDER BY HASHROW(v)) <= 5000;""",
)

C1_CAST_TERADATA = Template(
    key="C1-cast",
    query_id="C1",
    variant="teradata",
    heading="C1-TD. Hashed distinct sample",
    provides=(VALUE_SAMPLE,),
    parameters=("schema", "table", "column"),
    casts=True,
    sql="""SELECT v, raw, freq FROM (
  SELECT UPPER(TRIM(CAST({column} AS VARCHAR(4000)))) AS v,
         MIN(CAST({column} AS VARCHAR(4000))) AS raw, COUNT(*) AS freq
  FROM {schema}.{table}
  WHERE {column} IS NOT NULL
  GROUP BY UPPER(TRIM(CAST({column} AS VARCHAR(4000))))
) t
QUALIFY ROW_NUMBER() OVER (ORDER BY HASHROW(v)) <= 5000;""",
)

#: engine -> template key -> template, mirroring :data:`STATEMENTS`.
TEMPLATES: dict[str, dict[str, Template]] = {
    "postgres": {
        "B1": B1_ANSI,
        "B2": B2_ANSI,
        "B2-length": B2_LENGTH_ANSI,
        "B3": B3_ANSI,
        "B4": B4_ANSI,
        "C1": C1_ANSI,
        "C1-cast": C1_CAST_ANSI,
    },
    "sqlserver": {
        "B1": B1_ANSI,
        "B2": B2_ANSI,
        "B2-length": B2_LENGTH_SQLSERVER,
        "B3": B3_SQLSERVER,
        "B4": B4_SQLSERVER,
        "C1": C1_SQLSERVER,
        "C1-cast": C1_CAST_SQLSERVER,
    },
    "teradata": {
        "B1": B1_TERADATA,
        "B2": B2_ANSI,
        "B2-length": B2_LENGTH_TERADATA,
        "B3": B3_TERADATA,
        "B4": B4_TERADATA,
        "C1": C1_TERADATA,
        "C1-cast": C1_CAST_TERADATA,
    },
}


def template(engine: str, key: str) -> Template | None:
    """The Tier B/C block ``key`` on ``engine``, or None.

    None means the catalog does not cover it for that engine — a gap to
    record, never a licence to improvise one.
    """
    return TEMPLATES.get(engine, {}).get(key)


def templates_for(engine: str) -> list[Template]:
    """Every template ``engine`` runs, in the catalog's run order."""
    by_key = TEMPLATES.get(engine, {})
    return [by_key[key] for key in TIER_BC if key in by_key]


def all_templates() -> list[Template]:
    """Every distinct registered template, deduplicated."""
    seen: list[Template] = []
    for by_key in TEMPLATES.values():
        for item in by_key.values():
            if item not in seen:
                seen.append(item)
    return seen
