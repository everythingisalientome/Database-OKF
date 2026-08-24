---
type: table
name: CORE.album
description: Albums with owning artist reference
description_confirmed: false
database: MUSICSTORE_CORE
engine: ansi
row_count: 347
row_count_source: live
crawl_date: 2026-08-23
flags: []
---

- [inferred:high] Purpose: Album catalog. Each album belongs to one artist via artist_id.

## Columns

### album_id
- [observed] type: INT, not null
- [observed] distinct_count: 347; distinct_ratio: 1.0; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 347]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] constraint: PRIMARY KEY
- [observed] fingerprint: sha256/8B @ fingerprints/CORE.album.album_id.json
- [observed] normalization: [none]
- [inferred:high] Unique identifier; candidate key of album.

### title
- [observed] type: VARCHAR(160), not null
- [observed] distinct_count: 347; distinct_ratio: 1.0; null_rate: 0.0
- [observed] length: min 2, max 95, avg 22.7
- [observed] format: alpha; range: ['...And Justice For All' .. '[1997] Black Light Syndrome']
- [observed] fingerprint: sha256/8B @ fingerprints/CORE.album.title.json
- [observed] normalization: [uppercase]
- [inferred:high] Display name of the album record.

### artist_id
- [observed] type: INT, not null
- [observed] distinct_count: 204; distinct_ratio: 0.5879; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 275]
- [observed] constraint: FOREIGN KEY -> CORE.artist.artist_id
- [observed] index: non-unique
- [observed] fingerprint: sha256/8B @ fingerprints/CORE.album.artist_id.json
- [observed] normalization: [none]
- [inferred:medium] Reference identifier (artist).
