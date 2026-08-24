# Copilot Instructions — SOR Data Discovery & Query Building Platform

Three independent pipelines compile legacy-database knowledge into OKF
artifacts (markdown + YAML frontmatter bundles) so review queries are
generated instead of hand-built.

## Source of truth
- `specs/00`–`04`: design specs. `specs/00-project-overview.md` lists locked
  decisions — do not relitigate them; flag conflicts instead.
- `catalog/step1-query-catalog.md`: the ONLY SQL the crawler may run.
- `tests/fixtures/okf/`: ground truth. If code and fixtures disagree, report
  it — never change fixtures or specs to make tests pass.
- `BUILD-PLAN.md`: session roadmap. Work only on the session marked NEXT;
  follow its Session protocol section for every task.

## Layout
- `src/okf` shared bundle library · `src/crawler` step 1 ·
  `src/relbuilder` step 2 · `src/querybuilder` step 3
- Pipeline packages NEVER import each other; they communicate only through
  OKF bundles on disk. All may import `okf`.

## Non-negotiable constraints (verbatim mirror of CLAUDE.md — if one changes, change both in the same commit)

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

## Environment facts
- Engines: Oracle, DB2 LUW, SQL Server, ANSI (Postgres/MySQL), Teradata;
  toolset per run in config. Teradata Primary Index is a first-class join
  signal — preserve its flagging end to end.
- Pipelines run as scheduled OpenShift jobs; bundles are git-committed per
  refresh and diffs drive staleness.

## Working style
- One deliverable per task; tests with the code, not after; every
  deliverable ends with the exact command sequence to run it.
- Missing catalog queries: propose and flag for adoption; do not invent
  unlisted SQL silently.
