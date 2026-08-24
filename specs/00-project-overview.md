# SOR Review Agent — Data Discovery & Query Building Platform

## Problem

An agentic system receives work items from a source system and must answer a set
of review questions per item. Rules and required fields per question are known.
The evidence lives across multiple legacy SORs (databases today, APIs later).
The bottleneck is human effort in evidence location: finding the right tables
and columns, discovering cross-database lineage, and composing the query or
query series. Legacy reality: ~90% of databases have no declared FK constraints;
humans currently eyeball raw data to write any query.

Key insight: the question set, rules, required fields, field-to-SOR mappings,
and join paths are all stable (compile-time knowledge). Only work-item key
values vary per run. Today that compile-time knowledge is re-derived manually
at run time. This platform compiles it once into OKF artifacts.

## Architecture — three independent pipelines

**Step 1 — Schema Crawler (per database, independent runs).**
Integration team provides DB access. A non-agentic crawler executes a fixed
catalog of pre-declared queries (metadata + profiling), and an LLM annotator
writes column descriptions. Output: one OKF bundle per database. Runs as
scheduled OpenShift jobs; refresh handles drift.

**Step 2 — Relationship Builder (per database pair, offline).**
Integration team names the n databases relevant to a process. Entirely offline:
computes candidate join edges from the step 1 OKFs (value-overlap on hashed
fingerprints, index/PI signals, name similarity as tiebreaker). Output: one
relationship OKF bundle per database pair. No database access.

**Step 3 — Query Builder (per request, human in the loop).**
Integration team provides fields of interest with descriptions (and DBs if
known). Consumes step 2 + step 1 OKFs and emits per-database queries plus an
execution sequence with key bindings between them. Never writes cross-database
joins. Two output grades (see step 3 spec). Human review happens here.

## Decisions locked in this design

1. **Static tools only.** The agent never composes SQL at crawl time. All
   queries are pre-declared templates; `{schema}/{table}/{column}` parameters
   are allow-listed against the crawl's own catalog before interpolation.
2. **Profiles, not rows.** Raw data is read transiently; only derived profiles,
   format patterns, and hashed value fingerprints are persisted. Fingerprint
   hashing is KEYED (HMAC with a vault-held deployment secret, uniform across
   crawls) so guessable value spaces cannot be recovered by brute force from
   the stored hashes. Sensitive
   columns (config list) are excluded from top-N values and fingerprints.
3. **OKF granularity.** Step 1: one concept file per table,
   `/db/<dbname>/<schema>/<table>.md`, plus `index.md` per database.
   Step 2: one bundle per DB pair, one file per table pair.
4. **Provenance tags on every line.** `[observed]` = read from metadata/data;
   `[inferred]` = LLM-written with confidence marker; `[confirmed]` =
   human-validated. Step 3 must be able to distinguish fact from guess.
5. **No-FK signal hierarchy.** Declared constraints are recorded when present
   but expected absent. Primary evidence: measured value overlap — scored as
   **containment** |A∩B|/min(|A|,|B|), not Jaccard (FKs reference subsets;
   Jaccard under-scores true edges — fixture-validated) — plus index
   membership and Teradata Primary Index. Name similarity is a tiebreaker,
   EXCEPT for integer-surrogate pairs, where value spaces are generic and
   name similarity ≥ 0.6 becomes a required gate with confidence capped at
   medium (fixture-validated: dense integer sequences overlap by
   construction). Character-typed keys rely fully on overlap.
6. **Cardinality gates.** Fingerprints only for columns with distinct_ratio
   > 0.5 or index/constraint membership; top-N values only below a distinct
   threshold. Suppresses status-code false positives. Additionally, step 2
   applies an evidence floor: no edge is scored where either side has < 30
   distinct fingerprinted values (containment over a handful of values is
   vacuous — fixture-validated).
7. **Normalization rules on edges.** Trim / case / leading-zero rules applied
   before fingerprinting are recorded per edge; step 3 applies the same
   transforms in generated SQL.
8. **Edge weights.** Every candidate edge carries measured overlap AND the row
   counts it was measured against (overlap on 60 rows is anecdote, on 5M rows
   is evidence).
9. **HITL placement.** Full review at step 3 (small, consequential artifact).
   Steps 1–2 are review-by-exception (low-confidence inferences only).
10. **Grade A / Grade B outputs.** Grade A: full query sequence with bindings
    when the join path is high-confidence end to end. Grade B: ranked candidate
    columns with profiles + safe partial queries when any edge is weak. Every
    Grade B human resolution is written back to the relationship OKF as a
    `[confirmed]` edge — the system converges; nothing is resolved manually
    twice.
11. **Grant-gap detection.** Each crawl ends with reconciliation (visible vs.
    expected table counts); incomplete crawls are flagged, never silent.
12. **Row counts are load-bearing.** Denominator for rates, confidence weight
    for overlap, junk-table filter, and query-sequencing hint for step 3.

## Engines supported

Oracle, DB2 (LUW), SQL Server, ANSI information_schema (PostgreSQL/MySQL),
Teradata. Each pipeline run selects its engine toolset in config.

## Repo layout (suggested)

```
/specs           <- these documents
/catalog         <- step1-query-catalog.md (the tool SQL)
/okf/db/...      <- step 1 output bundles
/okf/rel/...     <- step 2 output bundles
/pipelines       <- step 1/2/3 implementations
```

## Resolved since first draft

- Profiling load policy: STATS-FIRST — dictionary column statistics (A6) are
  read at catalog cost; scan-based B1/B2 run only for missing/stale stats,
  batched multi-column, approximate where available, in off-peak windows.

## Open items

- Sensitive-column list per database (compliance input; drives B3/C1 exclusion)
- Whether step 3 output additionally compiles to the team's deterministic
  DAG/YAML skill format for platform execution (natural fit, not yet decided)
- API-backed SORs (future; step 1 crawler concept extends to OpenAPI specs)
