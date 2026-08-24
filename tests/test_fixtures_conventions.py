"""The fixture bundles, checked against specs/04-okf-conventions.md.

These fixtures are ground truth. Each test states one conventions rule in
terms the spec uses, so a failure here means either the fixtures drifted from
the conventions or the parser reads them wrong -- both worth a human look,
neither worth a silent fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from okf import (
    DatabaseBundle,
    IndexDocument,
    RelationshipBundle,
    RelationshipDocument,
    TableDocument,
    read_root,
    validate_root,
)
from okf.provenance import CONFIDENCES, KINDS
from okf.validate import (
    COMPLETENESS_VALUES,
    INDEX_REQUIRED,
    ONE_LINER_LIMIT,
    RELATIONSHIP_REQUIRED,
    TABLE_REQUIRED,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "okf"
ROOT = read_root(FIXTURE_ROOT)

DB_BUNDLES = ROOT.databases
REL_BUNDLES = ROOT.relationships
ALL_BUNDLES = ROOT.bundles
TABLES = [t for b in DB_BUNDLES for t in b.tables]
RELATIONSHIPS = [r for b in REL_BUNDLES for r in b.relationships]
INDEXES = [b.index for b in ALL_BUNDLES]

_id = lambda doc: str(Path(doc.path).relative_to(FIXTURE_ROOT).as_posix())  # noqa: E731

by_table = pytest.mark.parametrize("table", TABLES, ids=[_id(t) for t in TABLES])
by_relationship = pytest.mark.parametrize(
    "relationship", RELATIONSHIPS, ids=[_id(r) for r in RELATIONSHIPS]
)
by_index = pytest.mark.parametrize("index", INDEXES, ids=[_id(i) for i in INDEXES])
by_bundle = pytest.mark.parametrize(
    "bundle", ALL_BUNDLES, ids=[b.name for b in ALL_BUNDLES]
)
by_db_bundle = pytest.mark.parametrize(
    "db_bundle", DB_BUNDLES, ids=[b.name for b in DB_BUNDLES]
)
by_rel_bundle = pytest.mark.parametrize(
    "rel_bundle", REL_BUNDLES, ids=[b.name for b in REL_BUNDLES]
)


def test_fixtures_were_discovered():
    """Guard against a green suite that silently validated nothing."""
    assert DB_BUNDLES and REL_BUNDLES and TABLES and RELATIONSHIPS


# --------------------------------------------------------------------------
# whole-tree
# --------------------------------------------------------------------------


def test_fixture_tree_is_conventions_clean():
    issues = validate_root(ROOT)
    assert issues == [], "\n" + "\n".join(str(i) for i in issues)


# --------------------------------------------------------------------------
# frontmatter -- required fields by type
# --------------------------------------------------------------------------


@by_table
@pytest.mark.parametrize("required", TABLE_REQUIRED)
def test_table_frontmatter_required_fields(table: TableDocument, required):
    assert required in table.frontmatter
    assert table.frontmatter[required] not in (None, "", [])


@by_relationship
@pytest.mark.parametrize("required", RELATIONSHIP_REQUIRED)
def test_relationship_frontmatter_required_fields(relationship, required):
    assert required in relationship.frontmatter
    assert relationship.frontmatter[required] not in (None, "", [])


@by_index
@pytest.mark.parametrize("required", INDEX_REQUIRED)
def test_index_frontmatter_required_fields(index: IndexDocument, required):
    assert required in index.frontmatter
    assert index.frontmatter[required] not in (None, "", [])


@by_index
def test_index_declares_its_database_or_pair(index: IndexDocument):
    assert ("database" in index.frontmatter) != ("databases" in index.frontmatter)


@by_table
def test_table_description_is_one_line_and_flagged(table: TableDocument):
    """`description` is [inferred] by definition unless description_confirmed."""
    assert table.description and "\n" not in table.description
    assert isinstance(table.frontmatter.get("description_confirmed"), bool)


@by_table
def test_table_name_matches_its_path(table: TableDocument):
    """Path is identity: <schema>/<table>.md must agree with `name`."""
    bundle = next(b for b in DB_BUNDLES if b.name == table.database)
    relative = Path(table.path).relative_to(bundle.root).as_posix()
    assert relative == f"{table.schema}/{table.table}.md"


@by_relationship
def test_relationship_built_from_pins_both_sources(relationship: RelationshipDocument):
    """`built_from` pins the source bundle versions -- staleness depends on it."""
    pins = relationship.built_from
    assert len(pins) == len(relationship.tables)
    for pin in pins:
        assert pin.bundle.startswith("db/")
        assert pin.date


# --------------------------------------------------------------------------
# provenance -- every content line carries exactly one tag
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "document",
    TABLES + RELATIONSHIPS + INDEXES,
    ids=[_id(d) for d in TABLES + RELATIONSHIPS + INDEXES],
)
def test_every_content_line_carries_a_valid_provenance_tag(document):
    assert document.content_lines, f"{document.path} has no content lines"
    for line in document.content_lines:
        assert line.provenance.kind in KINDS
        if line.provenance.is_inferred:
            assert line.provenance.confidence in CONFIDENCES, line.render()
        else:
            assert line.provenance.confidence is None, line.render()


@pytest.mark.parametrize(
    "document",
    TABLES + RELATIONSHIPS + INDEXES,
    ids=[_id(d) for d in TABLES + RELATIONSHIPS + INDEXES],
)
def test_no_body_bullet_escapes_the_tag_rule(document):
    """Re-read the raw file: every '- ' body bullet outside the child index is
    a tagged content line. Catches anything the model dropped rather than
    faulted."""
    source = Path(document.path).read_text(encoding="utf-8")
    body = source.split("\n---\n", 1)[1]
    tagged = {line.render() for line in document.content_lines}
    for raw in body.splitlines():
        if not raw.startswith("- "):
            continue
        if raw.startswith("- `"):  # child index entry, layer 2 of index.md
            assert isinstance(document, IndexDocument)
            continue
        assert raw in tagged, raw


@by_table
def test_every_column_has_an_annotator_description(table: TableDocument):
    """spec 01 step 6: at least one [inferred:conf] line per column -- an
    explicit '[inferred:low] insufficient evidence' where nothing else fits,
    never a silent omission."""
    for column in table.columns:
        assert any(
            line.provenance.is_inferred or line.provenance.is_human
            for line in column.lines
        ), f"{table.name}.{column.name}"


@by_table
def test_profile_lines_are_observed(table: TableDocument):
    """Measured facts are [observed]; LLM output is never tagged [observed]."""
    measured = {"type", "distinct_count", "null_rate", "top_values", "fingerprint",
                "normalization", "length", "constraint", "index", "dense_sequence",
                "format", "sensitive-listed"}
    for column in table.columns:
        for line in column.lines:
            if line.key in measured:
                assert line.provenance.is_observed, line.render()


@by_bundle
def test_hitl_queue_is_reachable(bundle):
    """Review-by-exception needs the low-confidence lines to be findable."""
    flagged = bundle.needs_review
    for line in flagged:
        assert line.provenance.needs_review
    assert all(l.provenance.kind in ("inferred", "stale-confirmed") for l in flagged)


# --------------------------------------------------------------------------
# index.md contract -- both layers, and completeness
# --------------------------------------------------------------------------


@by_index
def test_index_has_a_bundle_level_description(index: IndexDocument):
    """Layer 1: what lets a consumer pick which bundle to open."""
    assert index.summary
    assert all(line.text.strip() for line in index.summary)


@by_index
def test_index_has_a_child_index(index: IndexDocument):
    """Layer 2: one line per child file."""
    assert index.entries
    assert all(entry.description.strip() for entry in index.entries)


@by_index
def test_child_index_one_liners_are_short(index: IndexDocument):
    for entry in index.entries:
        assert len(entry.description) <= ONE_LINER_LIMIT, entry.render()


@by_index
def test_completeness_flag_is_a_known_value(index: IndexDocument):
    assert index.completeness in COMPLETENESS_VALUES


@by_bundle
def test_child_index_is_complete_and_exact(bundle):
    """Every child file listed once; every listed path exists."""
    on_disk = bundle.child_paths()
    listed = [entry.path for entry in bundle.index.entries]
    assert sorted(listed) == sorted(on_disk)
    assert len(listed) == len(set(listed))
    for path in listed:
        assert (bundle.root / path).is_file()


@by_db_bundle
def test_child_index_carries_the_childs_frontmatter_description(db_bundle):
    """"the child's frontmatter `description`, extracted mechanically"."""
    for table in db_bundle.tables:
        relative = Path(table.path).relative_to(db_bundle.root).as_posix()
        entry = db_bundle.index.entry_for(relative)
        assert entry is not None, relative
        assert entry.description.startswith(table.description)


# --------------------------------------------------------------------------
# bundle layout -- path is identity
# --------------------------------------------------------------------------


@by_db_bundle
def test_database_bundle_directory_is_the_database_name(db_bundle: DatabaseBundle):
    assert db_bundle.index.database == db_bundle.root.name
    for table in db_bundle.tables:
        assert table.database == db_bundle.root.name


@by_rel_bundle
def test_pair_directory_is_the_sorted_database_pair(rel_bundle: RelationshipBundle):
    pair = rel_bundle.path_databases
    assert len(pair) == 2
    assert pair == sorted(pair), "pair directories are sorted lexicographically"
    assert rel_bundle.index.databases == pair


@by_rel_bundle
def test_relationship_tables_stay_inside_the_pair(rel_bundle: RelationshipBundle):
    pair = set(rel_bundle.path_databases)
    for doc in rel_bundle.relationships:
        assert len(doc.tables) == 2
        assert {t.split(".", 1)[0] for t in doc.tables} == pair


@by_rel_bundle
def test_built_from_pins_match_the_source_bundle_build_dates(rel_bundle):
    """Fresh edges: the pinned date equals what the source bundle now reports."""
    for doc in rel_bundle.relationships:
        for pin in doc.built_from:
            source = ROOT.database(pin.bundle.split("/", 1)[1])
            assert source is not None, pin.render()
            assert str(source.index.frontmatter["build_date"]) == pin.date


# --------------------------------------------------------------------------
# data-in-OKF rules
# --------------------------------------------------------------------------


@by_table
def test_sensitive_columns_carry_no_values_in_any_form(table: TableDocument):
    """Rule 2: no values at all -- raw or hashed -- for sensitive-listed
    columns."""
    for column in table.columns:
        if not column.is_sensitive:
            continue
        assert column.top_values is None, f"{table.name}.{column.name}"
        assert column.find("fingerprint") is None, f"{table.name}.{column.name}"


@by_db_bundle
def test_fingerprint_references_resolve_to_payloads(db_bundle: DatabaseBundle):
    """Rule 4: payloads live outside the markdown, referenced by path."""
    seen = 0
    for table, column, ref in db_bundle.fingerprint_refs():
        target = db_bundle.resolve(ref)
        assert not Path(ref.path).is_absolute(), ref.path
        assert target.is_file(), f"{table.name}.{column.name} -> {ref.path}"
        seen += 1
    assert seen, f"{db_bundle.name} references no fingerprints"


@by_db_bundle
def test_no_orphan_fingerprint_payloads(db_bundle: DatabaseBundle):
    assert db_bundle.orphan_fingerprints() == []


@by_db_bundle
def test_payload_normalization_matches_the_table_file(db_bundle: DatabaseBundle):
    """Both sides of a future comparison must know the same rules were applied."""
    for table, column, ref in db_bundle.fingerprint_refs():
        payload = db_bundle.fingerprint(ref)
        assert payload.normalization == column.normalization, (
            f"{table.name}.{column.name}"
        )


@by_db_bundle
def test_payloads_hold_hashes_not_values(db_bundle: DatabaseBundle):
    for _, _, ref in db_bundle.fingerprint_refs():
        payload = db_bundle.fingerprint(ref)
        assert payload.algo
        assert payload.values
        assert all(isinstance(v, str) for v in payload.values)
        assert len(payload.values) <= payload.count


def test_fingerprint_payload_parses(fixture_fingerprint):
    from okf import read_fingerprint

    payload = read_fingerprint(fixture_fingerprint)
    assert payload.count >= 0
    assert isinstance(payload.normalization, list)


# --------------------------------------------------------------------------
# drift propagation
# --------------------------------------------------------------------------


@by_rel_bundle
def test_incomplete_source_bundles_would_propagate(rel_bundle: RelationshipBundle):
    """Nothing consumes an INCOMPLETE bundle without surfacing the flag. All
    fixture sources are COMPLETE, so this asserts the premise holds."""
    for name in rel_bundle.path_databases:
        source = ROOT.database(name)
        assert source is not None
        if not source.is_complete:
            assert not rel_bundle.is_complete
