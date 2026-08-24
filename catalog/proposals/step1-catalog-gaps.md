# Step 1 Catalog — Proposals

Nothing in this file may be executed. `catalog/step1-query-catalog.md` is the
only SQL the crawler runs. This file is where a gap the crawler hits gets
written down with a proposed statement, and where the record of what happened
to it lives.

`src/crawler/gaps.py` carries the open gaps machine-readably, and every crawl
result records the ones that applied to it, so a bundle says *"no index
evidence was collected, gap Pn"* rather than reporting a database with no
indexes. Session 2 filed P1-P9 and all nine were adopted. Session 3 filed
P10-P15: P10-P13 were adopted directly; P14 and P15 were policy questions
rather than missing SQL, were adjudicated in-session (2026-08-24), and are
adopted below with their rulings. **The register is empty.**

## Adopted

| id | engine | query | what it added |
|----|--------|-------|---------------|
| P1 | PostgreSQL | A4 | index inventory from `pg_index` — PostgreSQL had no index block, and information_schema has none to give |
| P2 | PostgreSQL | A6 | dictionary statistics from `pg_stats` + `pg_class.reltuples`, with the interpretation rules |
| P3 | SQL Server | A6 | per-column approximate distincts from the statistics histograms, as a second A6 block |
| P4 | Teradata | A6 | `DBC.StatsV` as a Tier A statement rather than prose |
| P5 | Teradata | A5 | reconciliation from `DBC.TablesV`, base tables only |
| P6 | ANSI | A3 | `constraint_name`, schema-qualified joins, and the referenced table — foreign key targets now resolve |
| P7 | Teradata | A3/A4 | `IndexNumber`, so two unnamed indexes on one table are distinguishable |
| P8 | PostgreSQL | A4 | `k.ordinality > ix.indnkeyatts` — INCLUDE columns are payload, not join intent, and are no longer counted as key columns |
| P9 | PostgreSQL | A6 | drive from `pg_class`, outer-join `pg_stats` — a table with no column statistics keeps its row estimate |
| P10 | SQL Server | B3 | a top-N block of its own — the catalog had `SELECT TOP 20` as prose, and `FETCH FIRST` needs an `OFFSET` on this engine |
| P11 | ANSI / SQL Server / Teradata | B2 | batched length block — the single-column length form costs one scan per column, which B2's own batching rule forbids |
| P12 | ANSI / SQL Server | C1 | the non-character cast form, and SQL Server's own blocks — both were prose with no runnable block behind them |
| P13 | all | C1 | return `raw`, one un-normalized representative per kept value, so the applied normalization rules can be recorded |
| P14 | all | B4 | format classification sample — bottom-500 distinct values, read transiently, classified in the crawler; only the category persists |
| P15 | all | C1 | canonical temporal rendering (`YYYY/M/D`) applied in the crawler, plus `freq` on the C1/B4 blocks for row-weighted length statistics |

Notes worth keeping, because each one is a place the adopted block and the
obvious block differ:

* **P6** resolves the referenced *table* in SQL (a second join to
  `table_constraints`) and leaves the referenced *columns* to the crawler:
  `position_in_unique_constraint` is absent from SQL Server's
  information_schema, and this is the block SQL Server runs.
  `AnsiAdapter.parse_constraints` pairs them by ordinal position.
* **P1**, **P2** and **P9** carry
  `WHERE ... NOT IN ('pg_catalog', 'information_schema')`, matching the
  catalog's schema-scope policy. The crawler enforces the same policy for
  every engine and every query in `crawler/schemas.py` and records what it
  dropped — A1 could not filter in SQL even if it wanted to, since a crawl
  has to see the system schemas to report that it skipped them.
* **P8** keeps the PostgreSQL A4 shape compatible with the SQL Server one, so
  both adapters share `read_indexes`; each passes a predicate for how its
  engine marks an INCLUDE column (a seventh boolean on PostgreSQL,
  `key_ordinal = 0` on SQL Server).
* **P9** was found by running P2 against the acceptance rig: 11 analysed
  tables, `reltuples` populated for all of them, and A6 returning nothing,
  because `pg_stats` holds no rows for an empty table. Driving from
  `pg_class` keeps the same seven columns in the same order, so the adapter
  read it unchanged — a table with no column statistics arrives with a NULL
  `column_name`, which `parse_column_stats` skips.
* **P8 on old PostgreSQL**: `indnkeyatts` exists from PostgreSQL 11. Earlier
  versions have neither it nor INCLUDE indexes, so a deployment on 9.x/10
  needs the expression dropped.
* **P11 drops the `IS NOT NULL` predicate** the single-column length form
  carries. It is redundant rather than lost: LENGTH of NULL is NULL, and
  MIN/MAX/AVG ignore NULLs. A batched statement could not carry a per-column
  predicate anyway. SQL Server additionally casts before averaging, because
  AVG over an integer argument does integer division there — it would report
  an average length of 22 where the value is 22.7.
* **P12 spells SQL Server's trim `LTRIM(RTRIM(...))`.** `TRIM()` arrived in
  SQL Server 2017 and the systems this crawler is aimed at predate it. The
  normalized value is identical either way.
* **P13 is what makes `normalization:` reportable at all.** The OKF records
  the rules applied per column, and step 3 re-applies them in generated SQL
  (specs/00 decision 7) — but the original C1 returned only
  `UPPER(TRIM(...))`, so by the time the crawler saw a value, the evidence of
  whether trimming or upper-casing had changed anything was already gone.
  Grouping on the normalized value selects exactly the set the `DISTINCT`
  selected, so the sampled slice does not move; `MIN({column})` adds one raw
  representative per kept value. A rule is then recorded when a
  representative differs from its normalized form, which is the semantics the
  fixture bundles use: `genre.name` records `[uppercase]` and not
  `[trim, uppercase]`, because no genre name has surrounding whitespace.
* **P14 was a ruling, then a block.** The question filed was whether a
  transient read of a sensitive-listed column, persisting only a category,
  violates "no values in any form". Adjudicated 2026-08-24: it does not — a
  category is not a value, and the fixture bundles (the ground truth) carry
  `format:` lines on their sensitive columns. B4 runs for every column
  without a C1 sample, sensitive included; config
  `classify_sensitive_formats: false` withdraws the sensitive half for
  stricter regimes. B4 deliberately returns no `raw` representative — it
  reads columns whose values may never persist, so it carries nothing worth
  keeping. SQL Server's cast is `nvarchar`, because this block runs on
  character columns too and a `varchar` cast would mangle the unicode it is
  classifying.
* **P15 was a ruling, then code — no new SQL.** The rendering the fixtures
  were generated under is `YYYY/M/D` (slash-separated, unpadded, no time part
  at midnight; nonzero time appends ` H:MM:SS`), verified digit-for-digit by
  reproducing `employee.hire_date`'s committed payload from the raw dates.
  ISO-8601 was the more defensible candidate and lost: the fixtures are
  ground truth, and acceptance is digit-for-digit. Rendering happens in the
  crawler by parsing the engine's CAST output (SQL Server's legacy
  `Apr  1 2002 12:00AM` included) — a formatting expression per engine per
  block was the alternative and is exactly the SQL sprawl the catalog avoids.
  One consequence touched SQL after all: length statistics over rendered
  values are row-weighted (the fixture's `avg 8.6` is over eight rows, not
  seven distincts), so the C1 and B4 blocks return `freq`, the group row
  count the scan already had in hand. Temporal fingerprints record no
  normalization rules — rendering is not normalization, and the fixture
  payloads agree (`hire_date` records `[]`). A temporal value the renderer
  cannot read withholds the whole fingerprint with reason
  `unparseable-temporal`: a partially rendered sample is no longer a
  bottom-k.

## Corrections

Not every catalog change is a gap. This one was a statement that could not
run at all, found the first time the SQL Server rig came up.

**A6, SQL Server (table row counts) — `p.rows` -> `p.row_count`.** The block
selected `p.rows` from `sys.dm_db_partition_stats`, which has no such column;
`rows` belongs to `sys.partitions`. Every SQL Server crawl failed A6 with
*Invalid column name 'rows'*, lost its dictionary row counts, and — correctly
— downgraded itself to INCOMPLETE for a grant gap it did not have. Fixed to
`p.row_count`, with aliases added to match the other blocks. Verified live:
11 rows, one per table.

Worth noting how it surfaced. The failure was never silent: the crawl
recorded the driver's error verbatim against the statement, warned, and
refused to call the bundle COMPLETE while a Tier A query had failed. The
acceptance rig then turned that into four red tests.

**C1, Teradata — ranked by `v`, not by a hash.** `C1-TD` selected
`DISTINCT {column}` and kept `ROW_NUMBER() OVER (ORDER BY v) <= 5000`: no
normalization, and the alphabetically-lowest 5000 values rather than a
hash-ranked bottom-k. That is the arbitrary-slice sampling Tier C's own
preamble forbids — two databases holding different parts of one value space
keep disjoint slices, and the measured overlap step 2 is built on collapses
toward zero. Corrected to rank by `HASHROW(v)` over the normalized value,
matching every other engine's block. Not exercised against a live system:
Teradata stays UNVERIFIED until the session-6 dry run.

## Open

None. A new gap goes here with its proposed statement and an entry in
`src/crawler/gaps.py`, so the crawler reports it on every affected bundle
until it is adopted.
