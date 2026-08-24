"""Writing: bundles out, documents built from scratch, minimal diffs."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from okf import (
    Column,
    ContentLine,
    FrontMatter,
    IndexDocument,
    IndexEntry,
    Provenance,
    TableDocument,
    parse_document,
    read_bundle,
    read_document,
    read_root,
    render_document,
    validate_bundle,
    write_document,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "okf"


def _bundle_dirs():
    return sorted(
        p for parent in ("db", "rel") for p in (FIXTURE_ROOT / parent).iterdir()
        if p.is_dir()
    )


@pytest.mark.parametrize("bundle_dir", _bundle_dirs(), ids=lambda p: p.name)
def test_bundle_writes_back_byte_for_byte(bundle_dir, tmp_path):
    bundle = read_bundle(bundle_dir)
    out = tmp_path / bundle_dir.name
    bundle.write(out)
    for original in sorted(bundle_dir.rglob("*.md")):
        relative = original.relative_to(bundle_dir)
        written = out / relative
        assert written.is_file(), relative
        assert written.read_bytes() == original.read_bytes(), relative


def test_written_bundle_still_validates(tmp_path):
    source = FIXTURE_ROOT / "db" / "MUSICSTORE_CORE"
    out = tmp_path / source.name
    shutil.copytree(source, out)  # carry the fingerprint payloads across
    bundle = read_bundle(source)
    bundle.write(out)
    assert validate_bundle(read_bundle(out)) == []


def test_editing_one_field_changes_one_line(tmp_path):
    path = FIXTURE_ROOT / "db" / "MUSICSTORE_CORE" / "CORE" / "track.md"
    before = path.read_text(encoding="utf-8").splitlines()
    doc = read_document(path)
    doc.frontmatter["row_count"] = 3504
    after = render_document(doc).splitlines()
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert changed == [("row_count: 3503", "row_count: 3504")]
    assert len(before) == len(after)


def test_build_a_table_document_from_scratch(tmp_path):
    doc = TableDocument(
        frontmatter=FrontMatter.parse([
            "type: table",
            "name: CORE.widget",
            "description: Widget master",
            "description_confirmed: false",
            "database: DEMO",
            "engine: ansi",
            "row_count: 12",
            "crawl_date: 2026-08-23",
            "flags: []",
        ]),
        lines=[ContentLine(Provenance("inferred", "high"), "Purpose: Widget master.")],
        columns=[
            Column("widget_id", [
                ContentLine(Provenance("observed"), "type: INT, not null"),
                ContentLine(Provenance("inferred", "high"), "Surrogate key."),
            ]),
        ],
    )
    out = write_document(doc, tmp_path / "CORE" / "widget.md")
    reparsed = read_document(out)
    assert reparsed.render() == doc.render()
    assert reparsed.name == "CORE.widget"
    assert reparsed.column("widget_id").value("type") == "INT, not null"


def test_build_an_index_document_from_scratch(tmp_path):
    doc = IndexDocument(
        frontmatter=FrontMatter.parse([
            "type: index",
            "database: DEMO",
            "description: Demo bundle.",
            "engine: ansi",
            "build_date: 2026-08-23",
            "completeness: COMPLETE",
        ]),
        summary=[ContentLine(Provenance("inferred", "high"), "One. Two. Three.")],
        section="Tables",
        entries=[IndexEntry("CORE/widget.md", "Widget master (12 rows)")],
    )
    text = render_document(doc)
    assert parse_document(text).render() == text
    assert text.endswith("- `CORE/widget.md` — Widget master (12 rows)\n")


def test_write_creates_missing_directories(tmp_path):
    doc = read_document(FIXTURE_ROOT / "db" / "MUSICSTORE_CORE" / "CORE" / "track.md")
    target = write_document(doc, tmp_path / "deep" / "nested" / "track.md")
    assert target.is_file()


def test_round_trip_of_the_whole_tree(tmp_path):
    shutil.copytree(FIXTURE_ROOT, tmp_path / "okf")
    root = read_root(tmp_path / "okf")
    for bundle in root.bundles:
        bundle.write()
    for original in sorted(FIXTURE_ROOT.rglob("*")):
        if not original.is_file() or original.name == ".DS_Store":
            continue
        copy = tmp_path / "okf" / original.relative_to(FIXTURE_ROOT)
        assert copy.read_bytes() == original.read_bytes(), original
