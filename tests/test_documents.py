"""Unit tests for the document grammar: what parses, and what must not."""

from __future__ import annotations

import pytest

from okf import (
    ContentLine,
    FingerprintRef,
    IndexDocument,
    ParseError,
    RelationshipDocument,
    TableDocument,
    parse_document,
)
from okf.documents import BuiltFrom

TABLE = """\
---
type: table
name: CORE.track
description: Track catalog
description_confirmed: false
database: MUSICSTORE_CORE
engine: ansi
row_count: 3503
crawl_date: 2026-08-23
flags: []
---

- [inferred:high] Purpose: Central track catalog.

## Columns

### track_id
- [observed] type: INT, not null
- [observed] fingerprint: sha256-set @ fingerprints/track.track_id.json
- [observed] normalization: [none]
- [inferred:high] Unique identifier.
"""

INDEX = """\
---
type: index
database: MUSICSTORE_CORE
description: Music catalog system of record.
engine: ansi
build_date: 2026-08-23
completeness: COMPLETE
---

- [inferred:high] One. Two. Three.

## Tables

- `CORE/track.md` — Track catalog (3503 rows)
"""

RELATIONSHIP = """\
---
type: relationship
tables: [MUSICSTORE_CORE.CORE.track, MUSICSTORE_SALES.SALES.invoice_line]
built_from: [db/MUSICSTORE_CORE@2026-08-23, db/MUSICSTORE_SALES@2026-08-23]
---

## track.track_id <-> invoice_line.track_id
- [observed] containment: 1.0 (primary); jaccard: 0.566 (exact, hash-set)
- [inferred:medium] Likely join.
"""


def test_table_document_shape():
    doc = parse_document(TABLE)
    assert isinstance(doc, TableDocument)
    assert (doc.name, doc.schema, doc.table) == ("CORE.track", "CORE", "track")
    assert doc.description == "Track catalog"
    assert doc.description_confirmed is False
    assert [c.name for c in doc.columns] == ["track_id"]
    assert doc.lines[0].key == "Purpose"


def test_index_document_shape():
    doc = parse_document(INDEX)
    assert isinstance(doc, IndexDocument)
    assert doc.databases == ["MUSICSTORE_CORE"]
    assert doc.section == "Tables"
    assert doc.is_complete
    assert doc.entries[0].path == "CORE/track.md"
    assert doc.entries[0].description == "Track catalog (3503 rows)"


def test_relationship_document_shape():
    doc = parse_document(RELATIONSHIP)
    assert isinstance(doc, RelationshipDocument)
    assert doc.tables[0] == "MUSICSTORE_CORE.CORE.track"
    assert [(e.left, e.right) for e in doc.edges] == [
        ("track.track_id", "invoice_line.track_id")
    ]
    assert [p.bundle for p in doc.built_from] == [
        "db/MUSICSTORE_CORE", "db/MUSICSTORE_SALES"
    ]
    assert doc.built_from[0].date == "2026-08-23"


def test_content_line_accessors():
    line = ContentLine.parse(
        "- [observed] dense_sequence: true  # contiguous surrogate range"
    )
    assert line.key == "dense_sequence"
    assert line.value == "true"
    assert line.comment == "contiguous surrogate range"
    assert line.render() == (
        "- [observed] dense_sequence: true  # contiguous surrogate range"
    )


def test_prose_lines_have_no_key():
    line = ContentLine.parse("- [inferred:medium] Likely join: shared identifiers.")
    assert line.key is None
    assert line.value is None


def test_fingerprint_reference_parsing():
    doc = parse_document(TABLE)
    ref = doc.column("track_id").fingerprint
    assert ref == FingerprintRef("sha256-set", "fingerprints/track.track_id.json")
    assert ref.render() == "sha256-set @ fingerprints/track.track_id.json"


def test_normalization_none_is_the_empty_rule_list():
    doc = parse_document(TABLE)
    assert doc.column("track_id").normalization == []


def test_built_from_pin_requires_a_date():
    with pytest.raises(ParseError):
        BuiltFrom.parse("db/MUSICSTORE_CORE")


# -- strictness: the grammar is closed ------------------------------------


def test_untagged_body_bullet_is_rejected():
    broken = TABLE.replace("- [observed] type: INT, not null", "- type: INT, not null")
    with pytest.raises(ParseError, match="provenance tag"):
        parse_document(broken)


def test_bad_confidence_is_rejected():
    broken = TABLE.replace("[inferred:high]", "[inferred:certain]")
    with pytest.raises(ParseError):
        parse_document(broken)


def test_bare_prose_line_is_rejected():
    broken = TABLE.replace("## Columns", "Some untagged prose.\n\n## Columns")
    with pytest.raises(ParseError, match="unrecognised body line"):
        parse_document(broken)


def test_unknown_document_type_is_rejected():
    with pytest.raises(ParseError, match="unknown document type"):
        parse_document(TABLE.replace("type: table", "type: schema"))


def test_missing_type_is_rejected():
    with pytest.raises(ParseError, match="no 'type' field"):
        parse_document(TABLE.replace("type: table\n", ""))


def test_expected_type_mismatch_is_rejected():
    with pytest.raises(ParseError, match="expected a 'index' document"):
        parse_document(TABLE, expected_type="index")


def test_column_heading_before_the_columns_section_is_rejected():
    with pytest.raises(ParseError, match="column heading before '## Columns'"):
        parse_document(TABLE.replace("## Columns\n", ""))


def test_table_without_columns_section_is_rejected():
    header, _ = TABLE.split("## Columns", 1)
    with pytest.raises(ParseError, match="no '## Columns' section"):
        parse_document(header.rstrip("\n") + "\n")


def test_malformed_edge_heading_is_rejected():
    broken = RELATIONSHIP.replace(
        "## track.track_id <-> invoice_line.track_id", "## track_id joins invoice_line"
    )
    with pytest.raises(ParseError, match="edge heading"):
        parse_document(broken)


def test_index_entry_before_its_section_is_rejected():
    broken = INDEX.replace("## Tables\n\n", "")
    with pytest.raises(ParseError, match="before its section heading"):
        parse_document(broken)
