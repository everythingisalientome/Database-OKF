# Step 1 Crawler — Query Catalog

All queries are static tools. Tier A queries take no parameters (or only a schema
filter set in pipeline config). Tier B/C queries are fixed templates whose `{table}`
and `{column}` parameters MUST be allow-listed against the Tier A crawl results
before interpolation. No other SQL is ever issued.

## Schema scope

Default: crawl ALL schemas visible to the service account. Config
`schema include/exclude` narrows when needed (archives, DEV/QA clone
schemas). Adapters ALWAYS exclude engine system schemas, without config:
Oracle SYS/SYSTEM/*AUX; SQL Server sys/INFORMATION_SCHEMA; DB2
SYSCAT/SYSIBM/SYSSTAT; Teradata DBC/Sys*; PostgreSQL
pg_catalog/information_schema. Exclusions are recorded in index.md so
scope is auditable.

Run order per database: A1 → A2 → A3 → A4 → (per table) B1 → (per column) B2 → B3 → C1 → A5.

---

## Tier A — Metadata crawl (static, no parameters)

### A1. Table inventory

ANSI / SQL Server / PostgreSQL / MySQL:
```sql
SELECT table_catalog, table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_type IN ('BASE TABLE', 'VIEW');
```

Oracle:
```sql
SELECT owner AS table_schema, table_name, 'BASE TABLE' AS table_type
FROM all_tables
UNION ALL
SELECT owner, view_name, 'VIEW' FROM all_views;
```

DB2 (LUW):
```sql
SELECT tabschema AS table_schema, tabname AS table_name, type AS table_type
FROM syscat.tables
WHERE type IN ('T', 'V');
```

### A2. Column inventory

ANSI:
```sql
SELECT table_schema, table_name, column_name, ordinal_position,
       data_type, character_maximum_length, numeric_precision,
       numeric_scale, is_nullable, column_default
FROM information_schema.columns;
```

Oracle:
```sql
SELECT owner AS table_schema, table_name, column_name, column_id AS ordinal_position,
       data_type, data_length, data_precision, data_scale, nullable
FROM all_tab_columns;
```

DB2:
```sql
SELECT tabschema, tabname, colname, colno, typename, length, scale, nulls
FROM syscat.columns;
```

### A3. Declared constraints (PK / FK / unique — often absent in legacy, capture when present)

ANSI (schema-qualified joins; resolves FK target table AND column):
```sql
SELECT tc.constraint_type, tc.table_schema, tc.table_name, tc.constraint_name,
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
  AND ref_tc.constraint_schema = rc.unique_constraint_schema;
```
Referenced COLUMNS are resolved in code, not SQL: match
(unique_constraint_schema, unique_constraint_name) against the PRIMARY
KEY/UNIQUE rows of this same result set, pairing by ordinal_position.
Rationale: position_in_unique_constraint is absent from SQL Server's
information_schema, and this block is the one SQL Server runs.

Oracle:
```sql
SELECT ac.constraint_type, ac.owner AS table_schema, ac.table_name,
       acc.column_name, acc.position, ac.r_constraint_name
FROM all_constraints ac
JOIN all_cons_columns acc
  ON ac.constraint_name = acc.constraint_name AND ac.owner = acc.owner
WHERE ac.constraint_type IN ('P', 'R', 'U');
```

DB2:
```sql
SELECT k.constname, t.type, k.tabschema, k.tabname, k.colname, k.colseq
FROM syscat.keycoluse k
JOIN syscat.tabconst t
  ON k.constname = t.constname AND k.tabschema = t.tabschema;
```

### A4. Indexes (join-intent signal where FKs are undeclared)

PostgreSQL:
```sql
SELECT n.nspname AS table_schema, t.relname AS table_name,
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
  AND n.nspname NOT IN ('pg_catalog', 'information_schema');
```
`indkey` holds an index's key columns and its INCLUDE columns in one
vector; `indnkeyatts` is where the key columns stop. INCLUDE columns are
payload — there to be fetched, explicitly not to be searched — so counting
one as a key column manufactures join-intent evidence. The crawler keeps
them apart (SQL Server marks the same distinction with key_ordinal = 0).
Needs PostgreSQL 11+; earlier versions have neither indnkeyatts nor
INCLUDE indexes, so drop the expression there.

Documented omission: expression-index members (attnum = 0) are dropped by
the pg_attribute join — an expression is not a join column. Composite
index column order is preserved via ordinality (the join-intent signal
depends on leading-column order).

Oracle:
```sql
SELECT i.owner AS table_schema, i.table_name, i.index_name,
       i.uniqueness, ic.column_name, ic.column_position
FROM all_indexes i
JOIN all_ind_columns ic
  ON i.index_name = ic.index_name AND i.owner = ic.index_owner;
```

SQL Server:
```sql
SELECT s.name AS table_schema, t.name AS table_name, i.name AS index_name,
       i.is_unique, c.name AS column_name, ic.key_ordinal
FROM sys.indexes i
JOIN sys.tables t ON i.object_id = t.object_id
JOIN sys.schemas s ON t.schema_id = s.schema_id
JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id;
```

DB2:
```sql
SELECT i.tabschema, i.tabname, i.indname, i.uniquerule, ic.colname, ic.colseq
FROM syscat.indexes i
JOIN syscat.indexcoluse ic ON i.indname = ic.indname AND i.indschema = ic.indschema;
```

### A6. Column statistics from the dictionary (STATS-FIRST — run before Tier B)

Optimizer-collected stats give row counts, distinct counts, and null counts
per column at catalog cost (no table scans). Policy: read these first, record
the collection date, and run B1/B2 scans ONLY for tables/columns with missing
or stale stats (staleness threshold in config, default 90 days).

Oracle:
```sql
SELECT owner, table_name, column_name, num_distinct, num_nulls, last_analyzed
FROM all_tab_col_statistics;
```
DB2 (already in A2's view — colcard = distinct, numnulls; -1 means never collected):
```sql
SELECT tabschema, tabname, colname, colcard, numnulls, stats_time
FROM syscat.columns JOIN syscat.tables USING (tabschema, tabname);
```
SQL Server (per-table row counts; column distincts via stats objects).
The DMV column is `row_count`; `rows` belongs to `sys.partitions`:
```sql
SELECT s.name AS table_schema, t.name AS table_name, p.row_count
FROM sys.tables t
JOIN sys.schemas s ON t.schema_id = s.schema_id
JOIN sys.dm_db_partition_stats p ON t.object_id = p.object_id
WHERE p.index_id IN (0,1);
```
PostgreSQL (n_distinct < 0 means -(distinct/rows) — multiply by row estimate):
```sql
SELECT n.nspname AS table_schema, c.relname AS table_name,
       s.attname AS column_name, s.n_distinct, s.null_frac,
       c.reltuples::bigint AS est_rows,
       GREATEST(st.last_analyze, st.last_autoanalyze) AS stats_date
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stats s ON s.schemaname = n.nspname AND s.tablename = c.relname
LEFT JOIN pg_stat_all_tables st ON st.relid = c.oid
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema');
```
Driven from pg_class, not pg_stats: pg_stats holds nothing at all for an
empty table, so driving from it loses the row estimate for exactly the
tables the junk filter looks for (row_count = 0). A table with no column
statistics comes back with a NULL column_name and its estimate intact.

Interpretation rules (crawler MUST apply): n_distinct >= 0 is a count;
n_distinct < 0 is -(distinct/rows) and must be multiplied by est_rows;
n_distinct = 0 means the planner has no estimate — unknown, not zero.
null_frac is already a rate — store directly. reltuples is an ESTIMATE and
recorded as such; reltuples = -1 means never analyzed, which is a
missing-stats signal, not a row count.

SQL Server — per-column approximate distincts from stats histograms
(2016 SP1+; leading column of each stats object only). Recorded as
APPROXIMATE; B2 still runs where the estimate lands near a gate boundary:
```sql
SELECT sch.name AS table_schema, t.name AS table_name, c.name AS column_name,
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
GROUP BY sch.name, t.name, c.name, sp.rows, sp.rows_sampled, sp.last_updated;
```

Teradata (UNTESTED until the session-6 dry run). One row per collected
statistic: a column with no stats is ABSENT, never zero-distinct. Rows with
comma-separated ColumnName are multi-column stats — ignore for per-column:
```sql
SELECT DatabaseName, TableName, ColumnName, RowCount, UniqueValueCount,
       NullCount, LastCollectTimeStamp
FROM DBC.StatsV;
```
Teradata: `DBC.StatsV` (RowCount, UniqueValueCount, LastCollectTimeStamp).

### A5. Reconciliation (run last — detects grant gaps)

Compare the count of tables the account can see vs. the count cataloged:

Oracle:
```sql
SELECT (SELECT COUNT(*) FROM all_tables)  AS visible_tables,
       (SELECT COUNT(*) FROM dba_tables) AS total_tables FROM dual;
-- if dba_tables is not granted, record visible_tables only and flag UNVERIFIED
```

Teradata (UNTESTED until session-6 dry run; base tables only, matching A1-TD):
```sql
SELECT COUNT(*) AS visible_tables
FROM DBC.TablesV
WHERE TableKind IN ('T', 'O');
```

ANSI (visible count only; compare against DBA-provided expected count in config):
```sql
SELECT COUNT(*) AS visible_tables FROM information_schema.tables
WHERE table_type = 'BASE TABLE';
```

### A-note on legacy databases

Expect A3 to return little or nothing on legacy systems (FKs never declared).
That is not a failure — record the emptiness. When A3 is empty, the join-intent
signals reweight to: A4 indexes, Teradata Primary Index (below), and Tier C
value overlap. Those three are the primary evidence, A3 is a bonus when present.

---

## Teradata variants

### A1-TD. Table inventory
```sql
SELECT DatabaseName AS table_schema, TableName AS table_name, TableKind
FROM DBC.TablesV
WHERE TableKind IN ('T', 'O', 'V');
```

### A2-TD. Column inventory
```sql
SELECT DatabaseName, TableName, ColumnName, ColumnId,
       ColumnType, ColumnLength, DecimalTotalDigits, DecimalFractionalDigits,
       Nullable, DefaultValue
FROM DBC.ColumnsV;
```
(ColumnType is coded: CV=VARCHAR, CF=CHAR, I=INTEGER, D=DECIMAL, DA=DATE, TS=TIMESTAMP, etc.
The crawler maps codes to canonical types before writing the OKF.)

### A3-TD. Constraints
```sql
SELECT DatabaseName, TableName, IndexName, IndexNumber, IndexType,
       ColumnName, ColumnPosition
FROM DBC.IndicesV
WHERE IndexType IN ('K', 'U');  -- K = primary key, U = unique — rare on legacy
```

### A4-TD. Indexes, including the Primary Index — read this one carefully
```sql
SELECT DatabaseName, TableName, IndexName, IndexNumber, IndexType, UniqueFlag,
       ColumnName, ColumnPosition
FROM DBC.IndicesV;
```
(DatabaseName, TableName, IndexNumber) is the index identity — Teradata
indexes are routinely unnamed, so IndexName is a label, not an identity.
```sql
```
IndexType 'P' (primary index) is Teradata's distribution key. It is chosen by the
original designers for join and distribution performance, which makes it the
single strongest join-intent signal in a Teradata system with no declared FKs —
tables that join were almost always given compatible PIs. The OKF must flag PI
columns distinctly; step 2 should boost candidate edges where both sides are PI
columns.

### B1-TD. Row count
```sql
SELECT COUNT(*) AS row_count FROM {schema}.{table};
```
Cheaper alternative when statistics are collected:
```sql
SELECT DatabaseName, TableName, RowCount, LastCollectTimeStamp
FROM DBC.StatsV
WHERE DatabaseName = '{schema}' AND TableName = '{table}';
```

### B2-TD. Column profile — same as ANSI B2; use CHARACTER_LENGTH() for length stats.

### B3-TD. Top-N frequent values
```sql
SELECT TOP 20 {column} AS value, COUNT(*) AS freq
FROM {schema}.{table}
WHERE {column} IS NOT NULL
GROUP BY {column}
ORDER BY freq DESC;
```

### C1-TD. Hashed distinct sample
```sql
SELECT v FROM (
  SELECT DISTINCT {column} AS v
  FROM {schema}.{table}
  WHERE {column} IS NOT NULL
) t
QUALIFY ROW_NUMBER() OVER (ORDER BY v) <= 5000;
```

---

## Tier B — Per-table / per-column profiling (templates; identifiers allow-listed)

### B1. Row count (per table)
```sql
SELECT COUNT(*) AS row_count FROM {schema}.{table};
```
Engine option: use approximate stats where scans are costly —
Oracle `all_tables.num_rows`, SQL Server `sys.dm_db_partition_stats`,
DB2 `syscat.tables.card` (record stats-collection date alongside).

### B2. Column profile — BATCHED (one scan profiles many columns)

Never issue one scan per column on large tables. Batch 10–20 columns per
statement so a single pass computes all their aggregates:
```sql
SELECT COUNT(*),
       COUNT(c1), COUNT(DISTINCT c1), MIN(c1), MAX(c1),
       COUNT(c2), COUNT(DISTINCT c2), MIN(c2), MAX(c2)
       /* ... chunked to stay within engine memory ... */
FROM {schema}.{table};
```
Where offered, prefer approximate distincts (APPROX_COUNT_DISTINCT on
Oracle 12c+/SQL Server 2019+) — much lighter, accuracy is ample for gating.
B2 runs ONLY where A6 stats are missing or stale.

Single-column reference form:
```sql
SELECT
  COUNT(*)                                    AS total_rows,
  COUNT({column})                             AS non_null_rows,
  COUNT(DISTINCT {column})                    AS distinct_count,
  MIN({column})                               AS min_value,
  MAX({column})                               AS max_value
FROM {schema}.{table};
```
Derived and stored in the OKF: null_rate, distinct_ratio (distinct/non-null).
For character columns, add length stats:
```sql
SELECT MIN(LENGTH({column})) AS min_len,
       MAX(LENGTH({column})) AS max_len,
       AVG(LENGTH({column})) AS avg_len
FROM {schema}.{table} WHERE {column} IS NOT NULL;
```
(`LEN()` on SQL Server.)

### B3. Top-N frequent values (per column; gated — see note)
```sql
SELECT {column} AS value, COUNT(*) AS freq
FROM {schema}.{table}
WHERE {column} IS NOT NULL
GROUP BY {column}
ORDER BY freq DESC
FETCH FIRST 20 ROWS ONLY;
```
(`LIMIT 20` MySQL/Postgres; `SELECT TOP 20` SQL Server.)
Gate: run and store only when distinct_count from B2 is <= 30 (default; config).
Top-N characterizes code-lists; long value lists characterize nothing.
Purpose: characterizes code/status columns so the LLM annotator can describe them.
Values from columns flagged sensitive in config are hashed before storage (see C1)
or the column is excluded from B3 entirely.

---

## Tier C — Value fingerprint (per candidate-key column; enables offline overlap in step 2)

### C1. Hashed distinct sample (deterministic bottom-k)

Arbitrary "first N" sampling is WRONG for high-cardinality columns: two
databases return different arbitrary slices and true overlap measures near
zero. Selection must be deterministic bottom-k: rank normalized values by an
in-database hash and keep the k smallest, so every crawl independently keeps
the SAME slice of the value universe. k default: 5000 (config).

ANSI/PostgreSQL:
```sql
SELECT v FROM (
  SELECT DISTINCT UPPER(TRIM({column})) AS v
  FROM {schema}.{table} WHERE {column} IS NOT NULL
) t ORDER BY MD5(v) FETCH FIRST 5000 ROWS ONLY;
```
Oracle: `ORDER BY ORA_HASH(v)` · SQL Server: `ORDER BY HASHBYTES('MD5', v)` ·
DB2: `ORDER BY HASH_MD5(v)` · Teradata: `ORDER BY HASHROW(v)`.

The selection hash is UNKEYED and engine-native — acceptable because it only
decides WHICH values are kept and is never stored. Numeric columns: cast to
VARCHAR inside the derived table before normalization.
Pipeline post-processing (outside SQL, in the crawler):
1. Normalize: strip leading zeros where all-digits (trim/uppercase already
   applied in-query for selection consistency). Record which rules applied.
2. Hash each normalized value with a KEYED hash (HMAC-SHA-256, truncated to
   8 bytes). The key is a deployment secret held in the vault, identical across
   all crawls (comparability requires it), and never written to any bundle.
   Keyed hashing defeats dictionary/brute-force recovery of guessable values
   (sequential IDs, known formats) — an unkeyed hash of an enumerable value
   space is reversible by exhaustion and is NOT acceptable for prod crawls.
3. Store the hash set (or a minhash signature over it) in the OKF — never raw values.

Gate: run C1 only for columns where distinct_ratio > 0.5 or the column appears
in any index/constraint from A3/A4. This is the cardinality gate that keeps
status-code columns from becoming join candidates.

Step 2 computes Jaccard overlap between hash sets of column pairs across
databases entirely from the OKFs. An edge records: both columns, the
normalization rules applied on each side, and the measured overlap. No
database access needed in step 2.

---

## Storage rule

Persisted per column: profile numbers, format pattern, hashed fingerprint,
top-N values only for low-cardinality non-sensitive columns. Raw high-cardinality
values are never written to the OKF. Sensitive-column list comes from pipeline
config and excludes those columns from B3 and C1 output entirely.
