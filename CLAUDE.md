# CLAUDE.md — SOR Data Discovery & Query Building Platform

Build order and session status: **BUILD-PLAN.md** — work only on the
session marked NEXT and follow its Session protocol for every session.

## What this project is

Three independent pipelines that compile legacy-database knowledge into OKF
artifacts so review queries can be generated instead of hand-built:

1. **Step 1 — Schema Crawler** (`src/crawler`): deterministic crawl + LLM
   annotation -> OKF bundle per database. Spec: `specs/01-step1-schema-crawler-spec.md`.
   SQL tools: `catalog/step1-query-catalog.md`.
2. **Step 2 — Relationship Builder** (`src/relbuilder`): offline edge
   scoring from step 1 bundles -> bundle per database pair.
   Spec: `specs/02-step2-relationship-builder-spec.md`.
3. **Step 3 — Query Builder** (`src/querybuilder`): fields in -> sequenced
   per-DB queries out (Grade A) or ranked candidates (Grade B).
   Spec: `specs/03-step3-query-builder-spec.md`.

Shared format rules: `specs/04-okf-conventions.md`.
Design rationale and locked decisions: `specs/00-project-overview.md`.
Ground truth: `tests/fixtures/okf/` — if code and fixtures disagree,
report it; never change fixtures or specs to make tests pass.

## Layout rule

`src/okf` is the only shared package. Pipeline packages NEVER import each
other — they communicate only through OKF bundles on disk, mirroring the
deployment model. Tests mirror the packages.

## Non-negotiable constraints (verbatim mirror in .github/copilot-instructions.md — if one changes, change both in the same commit)

- No dynamic SQL anywhere. Only the catalog's templates; identifiers
  allow-listed against the crawl's own A1/A2 output before interpolation.
- No cross-database joins in generated SQL, ever. Sequenced queries with
  bindings only.
- No raw data values persisted except top-N on <=30-distinct, non-sensitive
  columns. Fingerprints: keyed hash (HMAC-SHA-256/8B, vault-held key);
  plain sha256 only in fixture mode. Sensitive-listed columns: no values
  in any form.
- Every OKF content line carries a provenance tag
  ([observed] / [inferred:conf] / [confirmed]). LLM output is never
  [observed]. [confirmed] lines carry by: and date:, and demote to
  stale-confirmed on schema change — never silently dropped.
- Grade A paths use only [observed]/[confirmed] edges; weak edges are
  Grade B-visible only.
- Every crawl ends with reconciliation; incomplete bundles are flagged and
  the flag propagates to all downstream outputs.
- Descriptions are required at database, table, and column level — they
  are step 3's semantic matching surface.

## Environment

- Self-hosted; pipelines run as scheduled OpenShift jobs; per-database
  step 1 runs are independent.
- Engines: Oracle, DB2 LUW, SQL Server, ANSI (Postgres/MySQL), Teradata.
  Engine toolset selected per run in config. Teradata Primary Index is a
  first-class join signal — preserve its flagging through the whole chain.
- OKF bundles are git-committed per refresh; diffs drive staleness.

## Style

- Deterministic core, LLM at the edges (annotation, field-to-column
  matching); the LLM never sees raw rows. When in doubt, move logic from
  prompt to code.
- Prefer boring: the crawler is an extractor, not an agent.
