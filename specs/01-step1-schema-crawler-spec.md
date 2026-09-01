# Step 1 — Schema Crawler Specification

## Purpose

Produce a complete, provenance-tagged OKF bundle describing one database:
every visible table, every column, its profile, and an annotated description.
No process context. No dynamic SQL. Deterministic crawl + LLM annotation pass.

## Inputs

- Database connection (read-only service account) and engine type
- Engine toolset from `step1-query-catalog.md`, registered as tools
- Config: schema include/exclude list, sensitive-column list, cardinality
  thresholds (defaults: fingerprint gate: distinct_ratio > 0.5 OR index/constraint
  membership; top-N stored only when distinct_count <= 30; fingerprint sample 5000 via deterministic bottom-k; top-N 20)

## Execution order (per database)

1. **A1–A4, A6** (and A7/A8 after A4: code-object definitions into the
   crawl JSON only — never bundles; conservative join-intent extraction emits
   per-column `[observed] join-intent: <col> = <schema.table.col>
   (source: <object>)` lines, external references into index.md, and an
   unparsed-object count into index.md): table inventory, column inventory, constraints, indexes,
   and dictionary column statistics (stats-first: B1/B2 scans run only where
   stats are missing or stale; profile lines record their source and date).
   Build the identifier allow-list from A1/A2 output.
2. **B1** per table lacking fresh stats: row count.
   Tables with row_count = 0 or matching junk patterns (`_BKP`, `_TEST`,
   `_OLD`, `TMP_`) are cataloged but profiled minimally and flagged.
3. **B2** batched per table (10–20 columns per scan), only for columns
   lacking fresh dictionary stats: nulls, distinct count, min/max, length.
   Derive null_rate, distinct_ratio, and dense_sequence (integer column
   whose distinct values fill ≥95% of a contiguous range starting near 1 —
   flags generic surrogate keys whose value overlap is non-distinctive;
   step 2's int-pair name gate depends on the column type either way, but
   the flag makes the diagnosis visible in the table file).
4. **B3** per column with distinct_count <= 30 (post-B2): collect top 20
   into the crawl result; the bundle's top_values line renders the top 6 and not sensitive-listed:
   top 20 values with frequencies.
5. **C1** per column passing the fingerprint gate and not sensitive-listed:
   deterministic bottom-k distinct sample (k smallest engine-hash ranks of the
   normalized value — both sides of any future comparison keep the same slice
   of the value universe; arbitrary first-N sampling is forbidden) ->
   normalize (record applied rules) -> keyed hash (HMAC-SHA-256, vault-held deployment key,
   same key for all crawls; 8-byte truncation) -> store hash set or minhash
   signature. The key never appears in any bundle; without it the fingerprint
   files are irreversible even for guessable value spaces.
6. **Annotation pass (LLM)** — descriptions are REQUIRED at three levels; they
   are step 3's semantic matching surface, not documentation:
   - **Database** (into index.md): 3–6 sentence summary of what the database
     contains — subject areas, entity families, apparent purpose — synthesized
     after all tables are annotated. This is what routes step 3 when the
     integration team knows fields but not databases.
   - **Table**: one-line frontmatter `description` (mechanically extracted into
     index.md) plus a fuller `[inferred:conf] Purpose:` body line.
   - **Column**: at least one `[inferred:conf]` description line per column.
     When names/profiles/top-N genuinely support no guess, write
     `[inferred:low] insufficient evidence to describe` — an explicit unknown,
     never a silent omission. `low` items are HITL-queued as usual.
   Inputs: names, types, profiles, top-N values. The annotator never sees raw
   rows — only the derived artifacts.
   **Human overlay**: descriptions supplied from process documents or by the
   integration team replace inferred ones, tagged `[confirmed]` with author and
   source, and set `description_confirmed: true` in frontmatter. Confirmed
   descriptions survive refresh unless the column/table is schema-changed
   (then `stale-confirmed`, re-queued — same rule as edges).
7. **A5 reconciliation**: visible vs. expected table count. Mismatch or
   unverifiable -> bundle flagged `INCOMPLETE`/`UNVERIFIED` in index.md.

## Output — OKF bundle per database

```
/okf/db/<dbname>/
  index.md                      <- database summary, table list w/ one-liners,
                                   crawl timestamp, engine, completeness flag
  <schema>/<table>.md           <- one concept file per table
```

### Table file format

```markdown
---
type: table
name: <schema>.<table>
description: <one-line annotator summary — REQUIRED; extracted into index.md>
description_confirmed: false     # true when overlaid from process docs / human
database: <dbname>
engine: teradata
row_count: 4812339
row_count_source: stats        # stats | live
stats_date: 2026-08-19
crawl_date: 2026-08-22
flags: []                      # e.g. [junk-suspect], [pi:ACCT_ID]
---

- [inferred:high] Purpose: <annotator prose>

## Columns

### ACCT_ID
- [observed] type: DECIMAL(18,0), not null
- [observed] distinct_count: 4811002; distinct_ratio: 0.9997; null_rate: 0.0
- [observed] index: PRIMARY INDEX (Teradata PI)
- [observed] fingerprint: hmac-sha256/8B @ fingerprints/ACCOUNT.ACCT_ID.json
- [observed] normalization: [strip-leading-zeros]
- [inferred:high] Account surrogate key; joins to other account-keyed tables.

### ACCT_STS_CD
- [observed] type: CHAR(2), nullable
- [observed] distinct_count: 14; null_rate: 0.02
- [observed] top_values: AC(61%), CL(22%), SU(9%), ...
- [inferred:medium] Account status code; AC likely active, CL likely closed.
```

Fingerprint payloads live beside the bundle (binary/JSON), referenced by path —
keeps the markdown diffable.

## Non-goals

- No relationship inference (step 2's job)
- No raw values persisted for high-cardinality or sensitive columns
- No cross-database awareness of any kind

## Refresh & drift

Scheduled OpenShift job per database. Each run produces a fresh bundle; a diff
against the previous bundle emits a change report (tables added/dropped, columns
changed, profiles shifted beyond tolerance). Any diff touching a table
invalidates step 2 edges referencing it (marked `stale`, re-scored on next
step 2 run) and flags dependent step 3 outputs.

## HITL

Review-by-exception only: `[inferred:low]` annotations and `INCOMPLETE` bundles
are queued for human attention. Nobody reviews thousands of tables wholesale.
