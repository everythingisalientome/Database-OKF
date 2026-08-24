# BUILD-PLAN.md — Session Roadmap

Slicing rules: one testable deliverable per session; dependency order;
small enough to review in one sitting. Every session ends with the demo
rule: show the command sequence, run it, show the result.
Update the Status column as sessions complete; adjudicated rulings go
into the specs, not here.

## Step 1 — Schema Crawler

| # | Deliverable | Acceptance | Status |
|---|-------------|-----------|--------|
| 1 | `okf` package: read/write/validate bundles; frontmatter, provenance, fingerprints, round-trip | 465 tests green; fixtures validate clean | DONE |
| 1b | Adjudication follow-ups: [confirmed] syntax validation + fixture example; final-newline rule; algo-string cross-check | pytest green; byte-exact round-trip except deliberate fixes | DONE |
| 2 | Crawler skeleton, Tier A only (A1–A6, A5), allow-list; adapters: PostgreSQL, SQL Server, Teradata (UNVERIFIED) | Acceptance vs live Chinook on PG + MSSQL containers; allow-list rejection test | DONE — 766 green; acceptance passes live on PG + MSSQL Chinook containers. Catalog gaps P1–P9 all adopted; A6 SQL Server corrected (`p.rows` -> `p.row_count`). Teradata remains UNVERIFIED per session 6. |
| 3 | Measuring pass: stats-first A6, B1/B2 batched profiles + derived rates/flags, B3 top-N, C1 bottom-k + keyed hashing, gates & budgets | Measured numbers == fixture numbers, digit-for-digit | DONE — 1134 green. Live acceptance vs fixtures on PG + MSSQL (Chinook 1.4.5 loaded, binary collation): every count/rate/bound/length/format/top-N digit-for-digit; 39/39 fingerprint payloads byte-exact on both engines. P10–P13 adopted; P14/P15 adjudicated in-session and adopted (format classification incl. sensitive-category ruling; canonical YYYY/M/D temporal rendering). One known fixture artifact, reported not patched: invoice_date max is the lexicographic '2025/9/7', chronologically 2025/12/22. |
| 4 | LLM annotation pass + OKF bundle emission (descriptions at DB/table/column level, index.md) | Emitted bundle passes session-1 validator; annotator sees derived artifacts only | NEXT |
| 5 | Reconciliation wiring, refresh diff (stale marking), OpenShift job packaging | Diff run flags a simulated schema change; job runs end-to-end locally | |
| 6 | Teradata dry run at workplace (read-only, dev system) | Tier A crawl of real Teradata; UNVERIFIED flag removed | |

## Step 2 — Relationship Builder (pure function of step 1 bundles)

| # | Deliverable | Acceptance | Status |
|---|-------------|-----------|--------|
| 7 | Candidate generation + gates (type compat, evidence floor, int-pair name gate) | Fixture pair yields exactly the 2 true edges; suppression counts match | |
| 8 | Scoring (containment primary, Jaccard recorded), boosts (idx/PI/name), confidence weighting, edge + pair-bundle emission | Emitted rel bundle matches fixture rel bundle | |
| 9 | Staleness propagation + [confirmed] writeback handler (incl. stale-confirmed demotion) | Simulated schema change demotes a confirmed edge; nothing drops silently | |

## Step 3 — Query Builder

| # | Deliverable | Acceptance | Status |
|---|-------------|-----------|--------|
| 10 | Field resolution (descriptions as matching surface, expected-type filter) | Known fields resolve to fixture columns; wrong-type candidates excluded | |
| 11 | Path construction + sequencing with bindings; normalization applied in SQL; per-engine emission | Grade A YAML for a fixture request; no cross-DB joins ever | |
| 12 | Grade B mode: ranked candidates + safe partials; writeback of human resolutions to step 2 | Weak-edge fixture request yields Grade B with the fixture's weak edges surfaced | |
| 13 | Review artifact polish + shape validation against sample execution | Reviewed YAML validates types vs step 1 metadata | |

## Session protocol (applies to every session — this replaces long prompts)

1. Read CLAUDE.md, this file, and the spec(s) named in the session's brief
   below before writing anything.
2. Build ONLY the session marked NEXT. Out-of-scope work is a defect.
3. Every SQL statement comes verbatim from the catalog; identifier
   interpolation only from the crawl's own allow-list.
4. Conflicts with specs or fixtures: report them, change nothing.
   Missing catalog queries: propose, flag for adoption, don't silently invent.
5. Definition of done, always: pytest fully green + show the exact command
   sequence to run the deliverable, run it, show the result.

## Session briefs

- **2**: Specs: 01, catalog. Tier A only; JSON crawl-result output (no OKF
  emission). Engine-adapter seam; adapters: PostgreSQL, SQL Server,
  Teradata (unit-tested only, mark UNVERIFIED). Rig: docker-compose with PG
  + MSSQL, both loaded with Chinook; acceptance asserts known Chinook facts
  (11 tables, columns/types, PK/FK, indexes, A5 pass) — NOT the fixture
  bundles (they simulate a split estate). Include allow-list rejection test.
  Known catalog gaps to propose: A6 for Postgres (pg_stats) and SQL Server
  column distincts.
- **3**: Specs: 01, catalog. Stats-first; batched B2; B3 <=30 distinct;
  C1 bottom-k + keyed hash (env/vault key; plain sha256 only in fixture
  mode); sensitive/gate/budget enforcement. Acceptance: measured numbers ==
  fixture numbers digit-for-digit.
- **4**: Specs: 01, 04. Annotation pass (LLM sees derived artifacts only,
  never rows; all output [inferred:conf]) + full OKF emission incl. index.md.
  Acceptance: emitted bundle passes the okf validator.
- **5**: Spec: 01. A5 wiring, refresh diff -> stale marking, OpenShift job
  packaging. Acceptance: simulated schema change produces correct diff/flags.
- **6**: Read-only Tier A dry run on workplace dev Teradata; remove
  UNVERIFIED. No code goal beyond fixes the dry run forces.
- **7–9**: Specs: 02, 04. See table; fixtures' rel bundle is the oracle.
- **10–13**: Specs: 03, 04. See table; Grade A/B YAML per spec 03 examples.

## Standing rules

- Sessions never merge; a failed session is redone, not patched mid-next.
- Spec conflicts surfaced in any session are adjudicated in chat, rulings
  land in specs/, and this file's Status is the only thing edited here.
- New engines, API-based SORs, and skill-format compilation are post-13
  backlog, not scope creep for these sessions.
