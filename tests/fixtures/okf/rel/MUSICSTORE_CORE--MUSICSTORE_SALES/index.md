---
type: index
databases: [MUSICSTORE_CORE, MUSICSTORE_SALES]
description: Cross-SOR edges between music catalog and sales databases.
build_date: 2026-08-23
completeness: COMPLETE
suppressed_int_pairs: 34
below_evidence_floor: 117
---

- [inferred:high] Sales line items reference catalog track identifiers; edges below were scored offline from step 1 fingerprints (exact Jaccard on hash sets).

## Table pairs

- `playlist_track--invoice_line.md` — 1 candidate, 0 weak edge(s)
- `track--invoice_line.md` — 1 candidate, 0 weak edge(s)