---
type: index
database: MUSICSTORE_CORE
description: Music catalog system of record.
engine: ansi
build_date: 2026-08-23
completeness: COMPLETE  # reconciliation: visible == cataloged
---

- [inferred:high] Music catalog system of record. Contains the artist and album masters, the central track catalog (with genre and media-type reference lists), and playlist definitions with their track assignments. Entity families: catalog content and its classification. No customer, sales, or financial data. Identifiers are dense integer surrogates starting at 1.

## Tables

- `CORE/artist.md` — Recording artists master list (275 rows)
- `CORE/album.md` — Albums with owning artist reference (347 rows)
- `CORE/track.md` — Track catalog: album, genre, media type, duration, price (3503 rows)
- `CORE/genre.md` — Music genre reference codes (25 rows)
- `CORE/media_type.md` — Media format reference codes (5 rows)
- `CORE/playlist.md` — Named playlists (18 rows)
- `CORE/playlist_track.md` — Playlist-to-track assignment (bridge) (8715 rows)