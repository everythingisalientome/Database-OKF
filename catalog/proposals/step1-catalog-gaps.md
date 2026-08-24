# Step 1 Catalog — Proposals

Nothing in this file may be executed. `catalog/step1-query-catalog.md` is the
only SQL the crawler runs. This file is where a gap the crawler hits gets
written down with a proposed statement, and where the record of what happened
to it lives.

`src/crawler/gaps.py` carries the open gaps machine-readably, and every crawl
result records the ones that applied to it, so a bundle says *"no index
evidence was collected, gap Pn"* rather than reporting a database with no
indexes. **There are no open gaps today**: every proposal session 2 filed was
adopted, and the register is empty.

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

## Open

None. A new gap goes here with its proposed statement and an entry in
`src/crawler/gaps.py`, so the crawler reports it on every affected bundle
until it is adopted.
