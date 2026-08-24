---
type: table
name: CORE.media_type
description: Media format reference codes
description_confirmed: false
database: MUSICSTORE_CORE
engine: ansi
row_count: 5
row_count_source: live
crawl_date: 2026-08-23
flags: []
---

- [inferred:high] Purpose: Reference list of media/file formats.

## Columns

### media_type_id
- [observed] type: INT, not null
- [observed] distinct_count: 5; distinct_ratio: 1.0; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 5]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] constraint: PRIMARY KEY
- [observed] top_values: 1(20%), 2(20%), 3(20%), 4(20%), 5(20%)
- [observed] fingerprint: sha256-set @ fingerprints/media_type.media_type_id.json
- [observed] normalization: [none]
- [inferred:high] Unique identifier; candidate key of media_type.

### name
- [observed] type: VARCHAR(120), nullable
- [observed] distinct_count: 5; distinct_ratio: 1.0; null_rate: 0.0
- [observed] length: min 14, max 27, avg 20.8
- [observed] format: alpha; range: ['AAC audio file' .. 'Purchased AAC audio file']
- [observed] top_values: MPEG audio file(20%), Protected AAC audio file(20%), Protected MPEG-4 video file(20%), Purchased AAC audio file(20%), AAC audio file(20%)
- [observed] fingerprint: sha256-set @ fingerprints/media_type.name.json
- [observed] normalization: [uppercase]
- [inferred:high] Display name of the media_type record.
