# CLAUDE.md — SOR Data Discovery & Query Building Platform

## What this project is

Three independent pipelines that compile legacy-database knowledge into OKF
artifacts so review queries can be generated instead of hand-built:

1. **Step 1 — Schema Crawler**: deterministic crawl + LLM annotation -> OKF
   bundle per database. Spec: `specs/01-step1-schema-crawler-spec.md`.
   SQL tools: `catalog/step1-query-catalog.md`.
2. **Step 2 — Relationship Builder**: offline edge scoring from step 1 bundles
   -> OKF bundle per database pair. Spec: `specs/02-step2-relationship-builder-spec.md`.
3. **Step 3 — Query Builder**: fields in -> sequenced per-DB queries out
   (Grade A) or ranked candidates (Grade B). Spec: `specs/03-step3-query-builder-spec.md`.

Shared format rules: `specs/04-okf-conventions.md`.
Design rationale and locked decisions: `specs/00-project-overview.md`.

## Non-negotiable constraints (do not relax these while coding)

- No dynamic SQL anywhere. Only the catalog's templates; identifiers
  allow-listed against the crawl's own A1/A2 output before interpolation.
- No cross-database joins in generated SQL, ever. Sequenced queries with
  bindings only.
- No raw data values persisted except gated top-N on low-cardinality,
  non-sensitive columns. Sensitive-listed columns: no values in any form.
- Every OKF content line carries a provenance tag
  (`[observed]` / `[inferred:conf]` / `[confirmed]`). LLM output is never
  tagged `[observed]`.
- `[confirmed]` edges are never silently dropped — schema changes demote them
  to `stale-confirmed` for human re-check.
- Grade A paths use only `[observed]`/`[confirmed]` edges; weak edges are
  Grade B-visible only.
- Every crawl ends with reconciliation; incomplete bundles are flagged and the
  flag propagates to all downstream outputs.

## Environment

- Self-hosted; no Vertex AI / GCP-managed services.
- Pipelines run as scheduled OpenShift jobs; per-database step 1 runs are
  independent.
- Engines: Oracle, DB2 LUW, SQL Server, ANSI (Postgres/MySQL), Teradata.
  Engine toolset selected per run in config. Teradata Primary Index is a
  first-class join signal — preserve its flagging through the whole chain.
- OKF bundles are git-committed per refresh; diffs drive staleness.

## Build order

Step 1 first (everything depends on its bundles), then step 2 (pure function
of step 1 output — highly unit-testable with fixture bundles), then step 3.
Build fixture OKF bundles early; steps 2 and 3 should be developed against
fixtures, never against live databases.

## Style

- Deterministic core, LLM at the edges (annotation, field-to-column matching).
  When in doubt, move logic from prompt to code.
- Prefer boring: the crawler is an extractor, not an agent.
