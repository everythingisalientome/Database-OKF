---
type: table
name: CORE.playlist_track
description: Playlist-to-track assignment (bridge)
description_confirmed: false
database: MUSICSTORE_CORE
engine: ansi
row_count: 8715
row_count_source: live
crawl_date: 2026-08-23
flags: []
---

- [inferred:high] Purpose: Bridge table assigning tracks to playlists. Composite key (playlist_id, track_id).

## Columns

### playlist_id
- [observed] type: INT, not null
- [observed] distinct_count: 14; distinct_ratio: 0.0016; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 18]
- [observed] constraint: PRIMARY KEY
- [observed] constraint: FOREIGN KEY -> CORE.playlist.playlist_id
- [observed] top_values: 1(38%), 8(38%), 5(17%), 3(2%), 10(2%), 12(1%)
- [observed] fingerprint: sha256-set @ fingerprints/playlist_track.playlist_id.json
- [observed] normalization: [none]
- [inferred:medium] Reference identifier (playlist).

### track_id
- [observed] type: INT, not null
- [observed] distinct_count: 3503; distinct_ratio: 0.402; null_rate: 0.0
- [observed] format: all-digits; range: [1 .. 3503]
- [observed] dense_sequence: true  # contiguous surrogate range - value overlap non-distinctive
- [observed] constraint: PRIMARY KEY
- [observed] constraint: FOREIGN KEY -> CORE.track.track_id
- [observed] fingerprint: sha256-set @ fingerprints/playlist_track.track_id.json
- [observed] normalization: [none]
- [inferred:medium] Reference identifier (track).
