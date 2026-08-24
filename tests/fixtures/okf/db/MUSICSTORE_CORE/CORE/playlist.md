---
type: table
name: CORE.playlist
description: Named playlists
description_confirmed: false
database: MUSICSTORE_CORE
engine: ansi
row_count: 18
row_count_source: live
crawl_date: 2026-08-23
flags: []
---

- [inferred:high] Purpose: User-facing named playlists.

## Columns

### playlist_id
- [observed] type: INT, not null
- [observed] distinct_count: 18; distinct_ratio: 1.0; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 18]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] constraint: PRIMARY KEY
- [observed] top_values: 1(6%), 2(6%), 3(6%), 4(6%), 5(6%), 6(6%)
- [observed] fingerprint: sha256-set @ fingerprints/playlist.playlist_id.json
- [observed] normalization: [none]
- [inferred:high] Unique identifier; candidate key of playlist.

### name
- [observed] type: VARCHAR(120), nullable
- [observed] distinct_count: 14; distinct_ratio: 0.7778; null_rate: 0.0
- [observed] length: min 5, max 26, avg 12.1
- [observed] format: alpha; range: ['90’s Music' .. 'TV Shows']
- [observed] top_values: Music(11%), Movies(11%), TV Shows(11%), Audiobooks(11%), 90’s Music(6%), Music Videos(6%)
- [observed] fingerprint: sha256-set @ fingerprints/playlist.name.json
- [observed] normalization: [uppercase]
- [inferred:high] Display name of the playlist record.
