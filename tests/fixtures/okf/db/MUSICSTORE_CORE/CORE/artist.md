---
type: table
name: CORE.artist
description: Recording artists master list
description_confirmed: false
database: MUSICSTORE_CORE
engine: ansi
row_count: 275
row_count_source: live
crawl_date: 2026-08-23
flags: []
---

- [inferred:high] Purpose: Master list of recording artists. One row per artist; referenced by album.

## Columns

### artist_id
- [observed] type: INT, not null
- [observed] distinct_count: 275; distinct_ratio: 1.0; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 275]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] constraint: PRIMARY KEY
- [observed] fingerprint: sha256/8B @ fingerprints/CORE.artist.artist_id.json
- [observed] normalization: [none]
- [inferred:high] Unique identifier; candidate key of artist.

### name
- [observed] type: VARCHAR(120), nullable
- [observed] distinct_count: 275; distinct_ratio: 1.0; null_rate: 0.0
- [observed] length: min 2, max 85, avg 20.6
- [observed] format: alpha; range: ['A Cor Do Som' .. 'Zeca Pagodinho']
- [observed] fingerprint: sha256/8B @ fingerprints/CORE.artist.name.json
- [observed] normalization: [uppercase]
- [inferred:high] Display name of the artist record.
