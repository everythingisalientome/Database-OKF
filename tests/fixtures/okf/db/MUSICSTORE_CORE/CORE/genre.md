---
type: table
name: CORE.genre
description: Music genre reference codes
description_confirmed: false
database: MUSICSTORE_CORE
engine: ansi
row_count: 25
row_count_source: live
crawl_date: 2026-08-23
flags: []
---

- [inferred:high] Purpose: Small reference list of music genres.

## Columns

### genre_id
- [observed] type: INT, not null
- [observed] distinct_count: 25; distinct_ratio: 1.0; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 25]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] constraint: PRIMARY KEY
- [observed] top_values: 1(4%), 2(4%), 3(4%), 4(4%), 5(4%), 6(4%)
- [observed] fingerprint: sha256/8B @ fingerprints/CORE.genre.genre_id.json
- [observed] normalization: [none]
- [inferred:high] Unique identifier; candidate key of genre.

### name
- [observed] type: VARCHAR(120), nullable
- [observed] distinct_count: 25; distinct_ratio: 1.0; null_rate: 0.0
- [observed] length: min 3, max 18, avg 9.0
- [observed] format: alpha; range: ['Alternative' .. 'World']
- [observed] top_values: Rock(4%), Jazz(4%), Metal(4%), Alternative & Punk(4%), Rock And Roll(4%), Blues(4%)
- [observed] fingerprint: sha256/8B @ fingerprints/CORE.genre.name.json
- [observed] normalization: [uppercase]
- [inferred:high] Display name of the genre record.
