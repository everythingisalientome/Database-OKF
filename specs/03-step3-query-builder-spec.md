# Step 3 — Query Builder Specification

## Purpose

Given the integration team's fields of interest (with descriptions, and target
databases when known), produce per-database queries and an execution sequence
that answers the need — or, when the evidence doesn't support a complete
answer, the ranked candidates a human needs to close the gap. This is the
stage with full human review.

## Inputs

- Field list: name, description, expected data type (from the review rules),
  known database hints if any
- Step 2 relationship bundles for the relevant database set
- Step 1 bundles (for column resolution and profiles)

## Hard rules

1. **Never emit a cross-database join.** One query targets one database.
   Cross-database data flow is expressed only as sequenced queries with
   bindings.
2. **Only `[observed]` and `[confirmed]` facts justify a Grade A path.**
   `[inferred]` annotations may guide field-to-column matching but every
   inferred hop must be surfaced in the review summary.
3. **Apply edge normalization in SQL.** If the edge says
   left=[strip-leading-zeros], the generated SQL applies the equivalent
   transform (e.g., `TRIM(LEADING '0' FROM col)`) on that side.

## Process

1. **Field resolution.** Match each requested field to candidate columns using
   step 1 descriptions, names, types, and profiles. Expected type from the
   review rules acts as a filter: a field expected to be a date never resolves
   to a CHAR(2).
2. **Path construction.** Connect the databases holding resolved columns via
   step 2 edges (confirmed > high > medium; weak edges excluded).
3. **Sequencing.** Order queries so each produces the binding keys the next
   consumes. Use row counts to drive direction: filter the small side first,
   carry keys into the large side. Warn when an intermediate result is
   projected to exceed a configurable size.
4. **Shape validation.** Where a sample execution environment exists, run each
   query with a limit and validate returned types against expected types
   before human review. (If no execution is available, validation is static:
   type-check against step 1 metadata.)

## Output grades

### Grade A — complete path

```yaml
request_id: <id>
grade: A
sequence:
  - step: 1
    database: DB2
    query: |
      SELECT ACCOUNT_NBR, RISK_TIER
      FROM RISK.ACCT_MASTER
      WHERE RISK_TIER IN ('H','C')
    produces: [ACCOUNT_NBR]
  - step: 2
    database: DB1
    binds: {ACCT_ID: "step1.ACCOUNT_NBR via strip-leading-zeros(left)"}
    query: |
      SELECT ACCT_ID, OPEN_DT, ACCT_STS_CD
      FROM CORE.ACCOUNT
      WHERE TRIM(LEADING '0' FROM CAST(ACCT_ID AS VARCHAR(18))) IN (:step1.ACCOUNT_NBR)
    produces: [OPEN_DT, ACCT_STS_CD]
stitch: join step1 x step2 on the bound key, in the platform layer
evidence:
  - edge: DB1.CORE.ACCOUNT.ACCT_ID <-> DB2.RISK.ACCT_MASTER.ACCOUNT_NBR
    basis: "[confirmed] 2026-09-02"   # or jaccard + confidence
review_notes:
  - "RISK_TIER resolved via [inferred:high] description — verify semantics"
```

This YAML is one deterministic-DAG compilation away from the platform's skill
format if end-to-end execution is later desired; the schema is designed so that
mapping is mechanical.

### Grade B — gap present

For each unresolved field or missing/weak edge:

```yaml
grade: B
resolved: [<the Grade A-style partial sequence for what IS safe>]
gaps:
  - field: customer_reference
    candidates:
      - column: DB1.CORE.ACCOUNT.CUST_REF
        profile: {type: VARCHAR(12), distinct_ratio: 0.98, null_rate: 0.01}
        signals: [indexed, name-sim:0.7]
      - column: DB1.CORE.ACCOUNT.CLIENT_KEY
        profile: {type: DECIMAL(10), distinct_ratio: 0.99, null_rate: 0.0}
        signals: [pi]
    weak_edges:
      - "CUST_REF <-> DB2.RISK.CLIENT_ID (jaccard 0.51)"
    ask_human: "Which column carries the customer reference used by this process?"
```

The human closes the gap once; the resolution is written back to step 2 as a
`[confirmed]` edge (see step 2 spec). Grade B's contract: the human never
eyeballs raw tables — they choose between profiled, ranked candidates.

## HITL

Every step 3 output is human-reviewed before use. The review artifact is small
(one YAML), evidence-linked (every join cites its edge and basis), and
distinguishes observed fact from inference. This is where the platform's
review effort is spent — by design, the only place it needs to be.
