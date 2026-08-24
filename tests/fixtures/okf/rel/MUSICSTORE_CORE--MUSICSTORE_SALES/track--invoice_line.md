---
type: relationship
tables: [MUSICSTORE_CORE.CORE.track, MUSICSTORE_SALES.SALES.invoice_line]
built_from: [db/MUSICSTORE_CORE@2026-08-23, db/MUSICSTORE_SALES@2026-08-23]
---

## track.track_id <-> invoice_line.track_id
- [observed] containment: 1.0 (primary); jaccard: 0.566 (exact, hash-set)
- [observed] evidence: 3503 x 2240 rows; confidence: medium
- [observed] normalization: left=[none], right=[none]
- [observed] boosts: [idx-idx, name-sim:1.0, int-pair:name-gated]
- [inferred:medium] Likely join: shared identifier population across SORs.
