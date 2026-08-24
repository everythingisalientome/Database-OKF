# Acceptance rig

Two live databases the crawler can be checked against: PostgreSQL and SQL
Server, both loaded with the Chinook schema.

```bash
docker compose -f rig/docker-compose.yml up -d --wait
```

```bash
python -m pytest -m acceptance -v
```

```bash
docker compose -f rig/docker-compose.yml down -v
```

The acceptance tests skip themselves when the rig is not up, so the default
`python -m pytest` run stays offline.

| service | host port | database | user | password |
|---------|-----------|----------|------|----------|
| postgres | 55432 | `chinook` | `crawler` | `crawler` |
| sqlserver | 14330 | `chinook` | `sa` | `Crawler!Rig2026` |

Non-standard ports so the rig cannot collide with a database already running
on the host — and, on Windows, chosen to sit outside the port ranges WinNAT
reserves. A bind inside one of those fails with *"an attempt was made to
access a socket in a way forbidden by its access permissions"*, which reads
like a permissions problem and is not one;
`netsh interface ipv4 show excludedportrange protocol=tcp` lists them. The passwords are in `docker-compose.yml` on purpose — this is
throwaway test infrastructure, and the one-command run matters more than
secrecy. Nothing else in this repo works that way: crawl configs name an
environment variable (`connection.password_env`) and never hold a password.

Starting the SQL Server service sets `ACCEPT_EULA=Y`, which accepts
Microsoft's licence terms for that container image. Whoever runs
`docker compose up` accepts them.

## Schema only — no rows

Both scripts create the 11 Chinook tables with their columns, primary keys,
foreign keys and secondary indexes, and insert nothing. Session 2 crawls
Tier A, which reads the dictionary and never touches a row, so the schema is
the whole load.

**Session 3 will need the real Chinook data**, digit for digit, because its
acceptance is "measured numbers == fixture numbers". Two things to sort out
before then:

* `README.md` documents `tests/fixtures/source/` as holding "Chinook SQL +
  the fixture generator script". That directory does not exist in the repo,
  so the provenance of `tests/fixtures/okf/` is currently unreproducible.
* The fixtures simulate a *split* estate — Chinook divided across
  `MUSICSTORE_CORE` and `MUSICSTORE_SALES`. This rig is one undivided
  database per engine. Session 3's data load has to decide which of the two
  it is reproducing.

## Naming

Tables and columns are snake_case (`album.album_id`, not `Album.AlbumId`),
matching the Chinook variant the OKF fixtures were generated from, so the rig
and the fixtures describe the same estate in the same words.

Types are each engine's own: PostgreSQL gets `varchar`/`timestamp`, SQL
Server gets `nvarchar`/`datetime`, exactly as the canonical Chinook
distributions for those engines declare them. The acceptance asserts that
`VARCHAR(160)` and `NVARCHAR(160)` both come back with their engine's
spelling preserved in `raw_type` — the crawler is meant to canonicalise, not
to pretend every engine is PostgreSQL.

## What the acceptance asserts

Known Chinook facts, not the OKF fixtures: 11 base tables, each table's
column count and order, nullability, canonical types, all 11 primary keys
(including the composite one on `playlist_track`), all 11 foreign keys
*resolved to the table and column they point at*, 21 indexes per engine
(11 primary keys plus Chinook's 10 foreign key indexes), and a passing A5
reconciliation against the expected count of 11 in the crawl config.

Neither crawl config names a schema. That is the catalog's schema-scope
policy working: the crawl takes every schema the account can see and drops
the engine's own — `pg_catalog` and `information_schema` on PostgreSQL, `sys`
and `INFORMATION_SCHEMA` on SQL Server — leaving exactly the 11 Chinook
tables. What was dropped is recorded in the result's `scope`, so the
reconciliation arithmetic can add the system tables back before comparing
with A5's account-wide count.

Two things the acceptance pins that are easy to misread as bugs:

* **Row counts are zero and they are estimates.** The rig runs `ANALYZE` at
  load, so all 11 tables report `row_count: 0` with
  `row_count_source: stats-estimate` — PostgreSQL's `reltuples` is what the
  planner believes, not a measurement. Column statistics are empty, because
  `pg_stats` holds a row per column that has *data* and every rig table is
  empty. Session 3's data load fills the column half in.
* **SQL Server's column statistics are marked approximate** — they are
  histogram sums, and B2 still has to run near a gate boundary.
