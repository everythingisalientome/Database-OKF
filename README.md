# SOR Data Discovery & Query Building Platform

Answering review questions requires evidence scattered across legacy
systems of record — databases that are decades old, mostly without declared
foreign keys, where integration teams today hand-discover tables, trace
cross-database lineage by eyeballing data, and hand-write every query.
This platform compiles that discovery work once, into versioned artifacts,
so queries are generated instead of hand-built.

## How it works — three independent pipelines

1. **Schema Crawler** (`src/crawler`) — connects to one database at a time,
   runs a fixed catalog of read-only queries, measures every column
   (counts, rates, patterns, keyed-hash fingerprints — never raw values),
   and writes an **OKF bundle**: one markdown file per table with an LLM
   annotation pass adding descriptions. Runs as scheduled jobs; re-crawls
   diff against previous bundles to detect drift.
2. **Relationship Builder** (`src/relbuilder`) — entirely offline, no
   database access: compares fingerprints across bundles to find join
   edges (containment-scored, with evidence floors and false-positive
   gates), boosted by index/Teradata-PI signals. Human-confirmed edges are
   written back and outrank everything.
3. **Query Builder** (`src/querybuilder`) — given the fields a team needs,
   emits sequenced per-database queries with bindings (never cross-database
   joins). Grade A: full query path. Grade B: ranked candidate columns with
   profiles when evidence is weak — the human closes the gap once, and the
   confirmation persists.

Pipelines share only `src/okf` (bundle read/write/validate) and communicate
exclusively through bundles on disk.

## Repo map

| path | contents |
|------|----------|
| `specs/` | design specs 00–04 (source of truth; 00 lists locked decisions) |
| `catalog/` | the complete SQL query catalog — the only SQL the crawler runs |
| `catalog/proposals/` | queries the catalog lacks, proposed for adoption, never run |
| `src/` | `okf` (shared), `crawler`, `relbuilder`, `querybuilder` |
| `tests/fixtures/okf/` | ground-truth bundles generated from Chinook (two simulated SORs + relationship bundle) |
| `tests/fixtures/source/` | Chinook SQL + the fixture generator script (not yet in the repo) |
| `rig/` | docker-compose acceptance rig: PostgreSQL + SQL Server loaded with Chinook |
| `BUILD-PLAN.md` | session roadmap, protocol, and status |
| `CLAUDE.md` / `.github/copilot-instructions.md` | assistant instructions (same rules, two tools) |

## Quickstart

```bash
python -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest            # full suite; live-rig tests skip themselves
```

Crawl one database (needs the driver extra for its engine — `postgres`,
`sqlserver` or `teradata` — and a running database):

```bash
.venv/bin/python -m crawler --config rig/config/chinook-postgres.json --out out/chinook-pg.json
```

The acceptance rig and its one-command startup are documented in
`rig/README.md`.

Supported engines: Oracle, DB2 LUW, SQL Server, ANSI (PostgreSQL/MySQL),
Teradata. Build status: see BUILD-PLAN.md.

## Ground rules (full list in CLAUDE.md)

No dynamic SQL — catalog templates with allow-listed identifiers only.
No cross-database joins, ever. No raw values persisted — profiles and keyed
fingerprints only; sensitive columns excluded entirely. Every artifact line
carries provenance ([observed]/[inferred]/[confirmed]); facts, model
guesses, and human judgments arealways distinguishable.
