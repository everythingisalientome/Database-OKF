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
| `tests/fixtures/source/` | Chinook 1.4.5 SQL + the fixture generator script — the fixtures' provenance |
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

`--measure` adds the profiling pass (top-N, fingerprints); `--emit okf`
runs the annotation pass, writes the OKF bundle under `okf/db/<database>`,
and validates it. Emission also works offline from a saved crawl result:

```bash
.venv/bin/python -m crawler --config rig/config/chinook-postgres.json --measure --out out/chinook-pg.json
.venv/bin/python -m crawler --from-crawl out/chinook-pg.json --emit okf
```

By default no model is attached (`--annotator none`): every description is
emitted as the explicit unknown (`[inferred:low] insufficient evidence to
describe`, HITL-queued). `--annotator llm` calls any OpenAI-compatible
endpoint — install the extra (`pip install -e ".[llm]"`) and set
`CRAWLER_LLM_MODEL`, `CRAWLER_LLM_API_KEY` (always from the environment,
never a config value; use a dummy for keyless endpoints), and optionally
`CRAWLER_LLM_BASE_URL` for a non-vendor endpoint such as a cluster-local
vLLM or Ollama. Any other transport wires its own `complete(prompt) -> text`
callable through `crawler.annotate.LLMAnnotator`. Whatever the model
returns is sanitized in code; anything unusable becomes the explicit
unknown, never a missing description.

The acceptance rig and its one-command startup are documented in
`rig/README.md`.

## Running against a new database

A crawl needs exactly two things on a fresh machine: **one config file**
and **environment variables for the secrets**. Nothing else — no rig, no
checkout of previous results, no model.

1. **Install the crawler with the driver extra for your engine** (each is
   its own extra so a job image carries only its own driver):

   | engine | extra | driver installed |
   |--------|-------|------------------|
   | `postgres` | `pip install -e ".[postgres]"` | psycopg 3 |
   | `sqlserver` | `pip install -e ".[sqlserver]"` | pymssql |
   | `teradata` | `pip install -e ".[teradata]"` | teradatasql |

2. **Write the config file** — committable, because it never holds a
   secret (a `password`-like key in `connection` is refused outright):

   ```json
   {
     "database": "BILLING_PROD",
     "engine": "teradata",
     "expected_table_count": 214,
     "schemas": {"include": ["BILLING"], "exclude": []},
     "connection": {
       "host": "td.example.internal",
       "user": "svc_crawler_ro",
       "password_env": "CRAWLER_DB_PASSWORD"
     },
     "measure": {
       "sensitive_columns": ["ssn", "customer.email"],
       "fingerprint_key_env": "CRAWLER_FINGERPRINT_KEY"
     },
     "refresh": {"row_count": 0.10, "distinct_count": 0.10, "rate": 0.05}
   }
   ```

   Every `connection` key except `password_env` is passed to the engine's
   driver verbatim, so use your driver's parameter names.
   `expected_table_count` is the DBA's count of what the run should catalog
   (inside the schema filter, if any) — with it, reconciliation can say
   COMPLETE/INCOMPLETE; without it, a filtered crawl is honestly UNVERIFIED.
   `refresh` sets how far profile numbers may drift before the refresh diff
   invalidates a table (shown here at the defaults; omit the block to get
   them).

3. **Export the secrets** — always environment variables, never config:

   ```bash
   export CRAWLER_DB_PASSWORD='...'        # the read-only account
   export CRAWLER_FINGERPRINT_KEY='...'    # the vault-held HMAC key
   ```

   The fingerprint key is required whenever `--measure` runs outside
   fixture mode; the same key must be used for every crawl or fingerprints
   stop being comparable across databases.

4. **Run the crawl.** No model is attached by default (`--annotator none`):
   descriptions are emitted as the explicit unknown and HITL-queued, so the
   first run needs no LLM access at all.

   ```bash
   python -m crawler --config billing.json --measure \
       --out billing-crawl.json --emit okf --diff
   ```

5. **Optional LLM annotation** (`--annotator llm`): install
   `pip install -e ".[llm]"` and set `CRAWLER_LLM_MODEL`,
   `CRAWLER_LLM_API_KEY` (dummy value for keyless endpoints) and optionally
   `CRAWLER_LLM_BASE_URL`. The annotator sees derived artifacts only, never
   rows.

Each run overwrites `okf/db/<database>/` in place. With `--diff`, the run
is a *refresh*: the previous bundle is compared with the new one and the
run writes `okf/refresh/<database>/report.md` (tables added/dropped,
columns changed, profiles shifted beyond the configured tolerance) and
`okf/refresh/<database>/stale.json` — the machine-readable stale manifest
listing every invalidated artifact, which is step 2's signal to re-score
edges. The first `--diff` run records a baseline. Two on-disk bundles (say,
a git checkout and a fresh emit) can also be compared directly:

```bash
python -m crawler.diff okf-previous/db/BILLING_PROD okf/db/BILLING_PROD --manifest stale.json
```

Supported engines: Oracle, DB2 LUW, SQL Server, ANSI (PostgreSQL/MySQL),
Teradata. Build status: see BUILD-PLAN.md.

## Ground rules (full list in CLAUDE.md)

No dynamic SQL — catalog templates with allow-listed identifiers only.
No cross-database joins, ever. No raw values persisted — profiles and keyed
fingerprints only; sensitive columns excluded entirely. Every artifact line
carries provenance ([observed]/[inferred]/[confirmed]); facts, model
guesses, and human judgments arealways distinguishable.
