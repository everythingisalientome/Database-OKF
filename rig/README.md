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

## Data and collation

Both databases hold the full Chinook 1.4.5 data — the same rows the fixture
bundles under `tests/fixtures/okf/` were generated from. The load files
(`02-chinook-data.sql`) are generated verbatim from
`tests/fixtures/source/Chinook_PostgreSql.sql` by
`rig/chinook/tools/extract-data.py`; rerun the tool if the source ever
changes, and commit both together.

Comparisons are binary, deliberately: the fixture generator measured with
Python's codepoint comparisons, and MIN/MAX/DISTINCT only reproduce its
numbers when the engine compares the same way. PostgreSQL gets it from
`initdb --locale=C`; SQL Server gets `COLLATE Latin1_General_100_BIN2` on
every character *column* — not on the server or database, because a binary
collation there makes identifier lookup case-sensitive and the catalog's
lowercase `information_schema` blocks stop resolving. That failure mode is
real and was hit; the comment in `docker-compose.yml` records it.

The fixtures simulate a *split* estate — Chinook divided across
`MUSICSTORE_CORE` and `MUSICSTORE_SALES` — while the rig is one undivided
database per engine. The measured acceptance
(`tests/crawler/test_acceptance_measured.py`) therefore compares per-column
*numbers*, which do not care about the split, never bundle structure.

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

Session 3 adds the measured acceptance on top
(`test_acceptance_measured.py`): a full `--measure` crawl of each engine,
compared with the fixture bundles' published numbers digit for digit — row
counts, distincts, rates, bounds, length statistics, formats, top-N, and
every fingerprint payload byte for byte. One datum is asserted at its
measured value instead of the fixture's: the generator took temporal min/max
over rendered *strings*, so `invoice.invoice_date`'s fixture max is the
lexicographic `'2025/9/7'` where the chronologically last invoice is
`2025/12/22`. The test names it as a known fixture artifact.

One thing the acceptance pins that is easy to misread as a bug:
**SQL Server's column statistics are marked approximate** — they are
histogram sums, and B2 still runs where one lands near a gate boundary
(PostgreSQL's `pg_stats` estimates likewise), which is why every measured
profile on this rig reports `source: live`.
