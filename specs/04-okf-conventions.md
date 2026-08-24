# OKF Conventions — Shared Across All Steps

Based on OKF v0.1 (markdown + YAML frontmatter, path-as-identity, lenient
consumers) with project-specific extensions. Consumers MUST tolerate unknown
frontmatter fields and broken links (per spec); producers here are stricter
than the spec requires.

## Bundle layout

```
/okf/db/<dbname>/index.md                    # step 1, per database
/okf/db/<dbname>/<schema>/<table>.md
/okf/db/<dbname>/fingerprints/...            # binary/JSON payloads, path-referenced
/okf/rel/<dbA>--<dbB>/index.md               # step 2, per database pair
/okf/rel/<dbA>--<dbB>/<tableA>--<tableB>.md
```

Pair directories: database names sorted lexicographically so the pair has one
canonical path.

## Frontmatter — required fields by type

| type         | required                                                          |
|--------------|-------------------------------------------------------------------|
| table        | type, name, description, database, engine, crawl_date, row_count  |
| relationship | type, tables, built_from                                          |
| index        | type, database(s), description, build_date, completeness          |

`description` is one line. It is `[inferred]` by definition unless
`description_confirmed: true` is set (human/process-doc overlay). Frontmatter
cannot carry per-line provenance tags, so this flag is the frontmatter
equivalent.

`built_from` pins the source bundle versions (name@date) — staleness detection
depends on it.

## Provenance tags — every content line carries exactly one

- `[observed]` — read directly from metadata or computed from data by the
  deterministic crawler. Never LLM-authored.
- `[inferred:high|medium|low]` — LLM-authored annotation. Confidence is
  mandatory. `low` items are HITL-queued.
- `[confirmed]` — human-validated, with author, date, and context. Outranks
  everything. On invalidating schema change becomes `stale-confirmed` and is
  re-queued, never silently dropped.

`[confirmed]` lines MUST carry attribution inline: `by:` and `date:`
(ISO), with optional `source:` / `context:` — e.g.
`- [confirmed] by: preet, date: 2026-09-02, source: process-doc-registry — ...`.
A confirmed line without by/date is a validation error.

Rule: a downstream consumer (step 3, or any future agent) must be able to
reconstruct exactly which claims rest on measurement, which on model guesswork,
and which on human judgment. This is the control that makes an LLM-annotated
artifact defensible in review.

## index.md contract

Each bundle's index.md is the progressive-disclosure surface, with two layers:

1. **Bundle-level description** (required): for a database bundle, a 3–6
   sentence summary of what the database contains — subject areas, entity
   families, apparent purpose. For a relationship bundle, what connects the
   pair. This layer is what lets a consumer choose *which bundle* to open when
   it only knows what it's looking for, not where it lives.
2. **Child index** (required): one line per child file — the child's
   frontmatter `description`, extracted mechanically — enough to decide
   whether to open it. Keep one-liners under ~120 chars.

Consumers read index.md first and load child files lazily.

## File mechanics

- Every file ends with exactly one final newline.
- Fingerprint references are bundle-relative (`fingerprints/<table>.<column>.json`),
  never absolute. The markdown line's algo string and the payload's `algo`
  field are the same vocabulary: `hmac-sha256/8B` (prod) / `sha256/8B`
  (unkeyed fixtures).
- index.md child-index lines (layer 2) are mechanically extracted and carry
  no provenance tag — they are structural, not claims. Relationship files
  have no frontmatter `description`; pair-index one-liners are computed.

## Data-in-OKF rules

1. No raw values for high-cardinality columns — hashed fingerprints only.
2. No values at all (raw or hashed) for sensitive-listed columns.
3. Top-N literal values only for low-cardinality, non-sensitive columns.
4. Fingerprint payloads live outside the markdown, referenced by path.

## Versioning & drift

Bundles are git-committed per refresh run. The refresh diff is the drift
signal: consumers of a changed file are invalidated transitively
(table -> edges -> generated queries). Nothing consumes a bundle marked
`INCOMPLETE` without surfacing that flag in its own output.
