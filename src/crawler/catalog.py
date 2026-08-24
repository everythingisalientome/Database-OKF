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
RECONCILIATION = "reconciliation"

#: Statement keys in the order the spec fixes: inventory first, A5 last.
TIER_A = ("A1", "A2", "A3", "A4", "A6", "A6-columns", "A5")

#: Catalog query ids. Every engine needs a block for each of these; several
#: keys can implement one id (SQL Server answers A6 with two blocks).
QUERY_IDS = ("A1", "A2", "A3", "A4", "A6", "A5")

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
        "A6": A6_POSTGRES,
        "A5": A5_ANSI,
    },
    "sqlserver": {
        "A1": A1_ANSI,
        "A2": A2_ANSI,
        "A3": A3_ANSI,
        "A4": A4_SQLSERVER,
        "A6": A6_SQLSERVER,
        "A6-columns": A6_COLUMNS_SQLSERVER,
        "A5": A5_ANSI,
    },
    "teradata": {
        "A1": A1_TERADATA,
        "A2": A2_TERADATA,
        "A3": A3_TERADATA,
        "A4": A4_TERADATA,
        "A6": A6_TERADATA,
        "A5": A5_TERADATA,
    },
}


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
