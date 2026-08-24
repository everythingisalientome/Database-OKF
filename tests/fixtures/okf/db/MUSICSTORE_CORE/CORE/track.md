---
type: table
name: CORE.track
description: Track catalog: album, genre, media type, duration, price
description_confirmed: false
database: MUSICSTORE_CORE
engine: ansi
row_count: 3503
row_count_source: live
crawl_date: 2026-08-23
flags: []
---

- [inferred:high] Purpose: Central track catalog. Carries album/genre/media type references, playback length, file size, and unit price.

## Columns

### track_id
- [observed] type: INT, not null
- [observed] distinct_count: 3503; distinct_ratio: 1.0; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 3503]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] constraint: PRIMARY KEY
- [observed] fingerprint: sha256/8B @ fingerprints/CORE.track.track_id.json
- [observed] normalization: [none]
- [inferred:high] Unique identifier; candidate key of track.

### name
- [observed] type: VARCHAR(200), not null
- [observed] distinct_count: 3257; distinct_ratio: 0.9298; null_rate: 0.0
- [observed] length: min 2, max 123, avg 15.9
- [observed] format: alpha; range: ['"40"' .. 'Último Pau-De-Arara']
- [observed] fingerprint: sha256/8B @ fingerprints/CORE.track.name.json
- [observed] normalization: [uppercase]
- [inferred:high] Display name of the track record.

### album_id
- [observed] type: INT, nullable
- [observed] distinct_count: 347; distinct_ratio: 0.0991; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 347]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] constraint: FOREIGN KEY -> CORE.album.album_id
- [observed] index: non-unique
- [observed] fingerprint: sha256/8B @ fingerprints/CORE.track.album_id.json
- [observed] normalization: [none]
- [inferred:medium] Reference identifier (album).

### media_type_id
- [observed] type: INT, not null
- [observed] distinct_count: 5; distinct_ratio: 0.0014; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 5]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] constraint: FOREIGN KEY -> CORE.media_type.media_type_id
- [observed] index: non-unique
- [observed] top_values: 1(87%), 2(7%), 3(6%), 5(0%), 4(0%)
- [observed] fingerprint: sha256/8B @ fingerprints/CORE.track.media_type_id.json
- [observed] normalization: [none]
- [inferred:medium] Reference identifier (media_type).

### genre_id
- [observed] type: INT, nullable
- [observed] distinct_count: 25; distinct_ratio: 0.0071; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 25]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] constraint: FOREIGN KEY -> CORE.genre.genre_id
- [observed] index: non-unique
- [observed] top_values: 1(37%), 7(17%), 3(11%), 4(9%), 2(4%), 19(3%)
- [observed] fingerprint: sha256/8B @ fingerprints/CORE.track.genre_id.json
- [observed] normalization: [none]
- [inferred:medium] Reference identifier (genre).

### composer
- [observed] type: VARCHAR(220), nullable
- [observed] distinct_count: 853; distinct_ratio: 0.3377; null_rate: 0.2789
- [observed] length: min 2, max 188, avg 24.6
- [observed] format: mixed; range: ['A. F. Iommi, W. Ward, T. Butler, J. Osbourne' .. 'roger glover']
- [inferred:low] Composer attribute.

### milliseconds
- [observed] type: INT, not null
- [observed] distinct_count: 3080; distinct_ratio: 0.8792; null_rate: 0.0
- [observed] format: all-digits; range: [1071 .. 5286953]
- [observed] fingerprint: sha256/8B @ fingerprints/CORE.track.milliseconds.json
- [observed] normalization: [none]
- [inferred:high] Track duration in milliseconds.

### bytes
- [observed] type: INT, nullable
- [observed] distinct_count: 3501; distinct_ratio: 0.9994; null_rate: 0.0
- [observed] format: all-digits; range: [38747 .. 1059546140]
- [observed] fingerprint: sha256/8B @ fingerprints/CORE.track.bytes.json
- [observed] normalization: [none]
- [inferred:medium] Media file size in bytes.

### unit_price
- [observed] type: NUMERIC(10,2), not null
- [observed] distinct_count: 2; distinct_ratio: 0.0006; null_rate: 0.0
- [observed] format: mixed; range: [0.99 .. 1.99]
- [observed] top_values: 0.99(94%), 1.99(6%)
- [inferred:high] Sale price per track; two price points observed.
