# Step 2 — Relationship Builder Specification

## Purpose

Given n databases named by the integration team, compute candidate join edges
between them (and within them) from step 1 OKF bundles alone. Entirely offline:
no database access. No process context — relationships are structural.

## Inputs

- List of database names (their step 1 bundles must exist and be non-stale)
- Step 1 OKF bundles + fingerprint payloads
- Config: overlap threshold (default Jaccard ≥ 0.6), minimum evidence rows
  (default: both columns' tables ≥ 1000 rows for full confidence), PI/index
  boost weights

## Algorithm

1. **Candidate generation.** All cross-database column pairs where BOTH columns
   have fingerprints (i.e., both passed step 1's cardinality gate). This gate is
   what keeps the pair space tractable and suppresses status-code noise.
2. **Type compatibility filter.** Canonical-type compatible pairs only
   (numeric-numeric, char-char with overlapping length ranges; char-numeric
   allowed only when the char format pattern is all-digits).
3. **Overlap scoring.** Jaccard similarity on hashed fingerprints (or minhash
   estimate). Record the normalization rules that were applied on each side —
   if they differ, record both; step 3 must replicate them in SQL.
3b. **Code-declared evidence.** join-intent lines and external references
   from step 1 (A7/A8) seed candidates directly (even where fingerprints are
   absent), waive the integer-pair name gate, and add the strongest
   non-measured boost. Code-declared + containment >= 0.7 justifies `high`;
   code-declared alone caps at `medium` with an explicit
   `no-value-evidence` marker (code goes stale — a human or measurement
   must corroborate before Grade A).
4. **Structural boosts.** Additive score boosts: both sides indexed; either
   side unique-indexed; both sides Teradata PI (strongest boost — PI choice
   encodes original join design); name similarity (tiebreaker weight only).
5. **Confidence weighting.** Scale confidence by evidence volume (row counts
   from step 1). High overlap on small tables -> capped at medium confidence.
6. **Edge emission.** Edges above threshold -> relationship bundle. Edges in a
   gray band (0.4–0.6) are emitted as `weak` — visible to step 3's Grade B
   mode, never used in Grade A paths.

## Output — OKF bundle per database pair

```
/okf/rel/<dbA>--<dbB>/
  index.md                       <- pair summary, edge counts by confidence,
                                    source bundle versions, build date
  <tableA>--<tableB>.md          <- one file per table pair with ≥1 edge
```

### Edge format

```markdown
---
type: relationship
tables: [DB1.CORE.ACCOUNT, DB2.RISK.ACCT_MASTER]
built_from: [db/DB1@2026-08-22, db/DB2@2026-08-21]
---

## ACCT_ID <-> ACCOUNT_NBR
- [observed] jaccard: 0.94 (minhash est.)
- [observed] evidence: 4.8M x 5.1M rows; confidence: high
- [observed] normalization: left=[strip-leading-zeros], right=[none]
- [observed] boosts: [pi-pi, name-sim:0.31]
- [inferred:high] Same account population; DB2 stores unpadded.

## BRANCH_CD <-> BR_CODE
- [observed] jaccard: 0.51; confidence: weak
- status: weak    # Grade B visibility only
```

## Human-confirmed edges (writeback from step 3)

When a human resolves a Grade B gap in step 3, the resolution is written back
here as:

```markdown
## CUST_REF <-> CLIENT_KEY
- [confirmed] by: <user>, date: 2026-09-02, context: <review/process id>
- [confirmed] join validated in executed query; normalization: left=[trim]
```

`[confirmed]` edges outrank any score. They survive re-scoring on refresh
unless a schema diff from step 1 invalidates the underlying columns, in which
case they are marked `stale-confirmed` and queued for human re-check — never
silently dropped, never silently trusted.

## Staleness

An edge references the step 1 bundle versions it was built from. When step 1
refresh diffs touch either table, the edge is marked `stale` and re-scored on
the next step 2 run.

## HITL

Review-by-exception: weak edges touching high-value tables, and
`stale-confirmed` edges. No wholesale review of the edge set.
