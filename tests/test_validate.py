"""Negative tests for the validator.

A validator that never fires would make test_fixtures_conventions.py green
and meaningless. Each test breaks one rule in an in-memory copy of a fixture
and asserts the matching code fires. Nothing on disk is touched.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from okf import (
    ContentLine,
    IndexEntry,
    Provenance,
    read_root,
    validate_bundle,
    validate_root,
)
from okf.validate import WARNING, errors

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "okf"


@pytest.fixture
def root():
    """A fresh, mutable copy of the fixture tree."""
    return read_root(FIXTURE_ROOT)


@pytest.fixture
def db(root):
    return root.database("MUSICSTORE_CORE")


@pytest.fixture
def rel(root):
    return root.relationships[0]


def codes(issues, severity=None):
    return {i.code for i in issues if severity is None or i.severity == severity}


def test_baseline_is_clean(root):
    assert validate_root(root) == []


# -- frontmatter -----------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["type", "name", "description", "database", "engine", "crawl_date",
              "row_count"]
)
def test_missing_required_table_field_is_an_error(db, field):
    table = db.table("track")
    table.frontmatter.fields = [f for f in table.frontmatter.fields if f.key != field]
    assert "T001" in codes(validate_bundle(db))


def test_empty_required_field_is_an_error(db):
    db.table("track").frontmatter["description"] = ""
    assert "T001" in codes(validate_bundle(db))


def test_missing_index_completeness_is_an_error(db):
    db.index.frontmatter.fields = [
        f for f in db.index.frontmatter.fields if f.key != "completeness"
    ]
    assert "I001" in codes(validate_bundle(db))


def test_unknown_completeness_value_is_an_error(db):
    db.index.frontmatter["completeness"] = "PROBABLY"
    assert "I005" in codes(validate_bundle(db))


def test_unqualified_table_name_is_an_error(db):
    db.table("track").frontmatter["name"] = "track"
    assert "T002" in codes(validate_bundle(db))


def test_name_that_disagrees_with_the_path_is_an_error(db):
    db.table("track").frontmatter["name"] = "OTHER.track"
    assert "T014" in codes(validate_bundle(db))


def test_database_that_disagrees_with_the_directory_is_an_error(db):
    db.table("track").frontmatter["database"] = "SOMETHING_ELSE"
    assert "T013" in codes(validate_bundle(db))


def test_non_boolean_description_confirmed_is_an_error(db):
    db.table("track").frontmatter["description_confirmed"] = "yes"
    assert "T004" in codes(validate_bundle(db))


def test_negative_row_count_is_an_error(db):
    db.table("track").frontmatter["row_count"] = -1
    assert "T005" in codes(validate_bundle(db))


def test_malformed_built_from_pin_is_an_error(rel):
    rel.relationships[0].frontmatter["built_from"] = ["db/MUSICSTORE_CORE"]
    assert "R004" in codes(validate_bundle(rel))


# -- provenance ------------------------------------------------------------


def _force(provenance, **kwargs):
    """Build an invalid tag the constructor would refuse, to prove the
    validator checks independently of the parser."""
    forced = copy.copy(provenance)
    for key, value in kwargs.items():
        object.__setattr__(forced, key, value)
    return forced


def test_inferred_without_confidence_is_an_error(db):
    line = db.table("track").columns[0].lines[-1]
    line.provenance = _force(line.provenance, confidence=None)
    assert "P002" in codes(validate_bundle(db))


def test_observed_with_a_confidence_is_an_error(db):
    line = db.table("track").columns[0].lines[0]
    line.provenance = _force(line.provenance, confidence="high")
    assert "P003" in codes(validate_bundle(db))


def test_unknown_provenance_kind_is_an_error(db):
    line = db.table("track").columns[0].lines[0]
    line.provenance = _force(line.provenance, kind="assumed")
    assert "P001" in codes(validate_bundle(db))


def test_column_without_an_annotator_description_is_an_error(db):
    column = db.table("track").column("composer")
    column.lines = [l for l in column.lines if not l.provenance.is_inferred]
    assert "T010" in codes(validate_bundle(db))


# -- index completeness ----------------------------------------------------


def test_child_file_missing_from_the_index_is_an_error(db):
    db.index.entries = db.index.entries[:-1]
    assert "B006" in codes(validate_bundle(db))


def test_index_entry_with_no_file_is_an_error(db):
    db.index.entries.append(IndexEntry("CORE/ghost.md", "Not on disk"))
    assert "B007" in codes(validate_bundle(db))


def test_duplicate_index_entry_is_an_error(db):
    db.index.entries.append(db.index.entries[0])
    assert "I009" in codes(validate_bundle(db))


def test_index_entry_that_drops_the_childs_description_is_an_error(db):
    db.index.entries[0].description = "Something else entirely"
    assert "B008" in codes(validate_bundle(db))


def test_index_without_a_bundle_description_is_an_error(db):
    db.index.summary = []
    assert "I006" in codes(validate_bundle(db))


def test_index_without_a_child_index_is_an_error(db):
    db.index.entries = []
    assert "I008" in codes(validate_bundle(db))


def test_overlong_one_liner_is_a_warning_not_an_error(db):
    db.index.entries[0].description = "x" * 200
    issues = validate_bundle(db)
    assert "I012" in codes(issues, WARNING)
    assert "I012" not in codes(errors(issues))


def test_short_bundle_description_is_a_warning(db):
    db.index.summary = [
        ContentLine(Provenance("inferred", "high"), "One sentence only.")
    ]
    assert "I007" in codes(validate_bundle(db), WARNING)


# -- data-in-OKF rules -----------------------------------------------------


def test_sensitive_column_with_top_values_is_an_error(root):
    sales = root.database("MUSICSTORE_SALES")
    column = sales.table("customer").column("email")
    assert column.is_sensitive
    column.lines.append(
        ContentLine(Provenance("observed"), "top_values: a@b.com(50%), c@d.com(50%)")
    )
    assert "T011" in codes(validate_bundle(sales))


def test_sensitive_column_with_a_fingerprint_is_an_error(root):
    sales = root.database("MUSICSTORE_SALES")
    column = sales.table("customer").column("email")
    column.lines.append(
        ContentLine(Provenance("observed"),
                    "fingerprint: sha256-set @ fingerprints/customer.email.json")
    )
    assert "T012" in codes(validate_bundle(sales))


def test_dangling_fingerprint_reference_is_an_error(db):
    column = db.table("track").column("track_id")
    line = column.find("fingerprint")
    line.text = "fingerprint: sha256-set @ fingerprints/track.gone.json"
    assert "T015" in codes(validate_bundle(db))


def test_normalization_disagreement_is_an_error(db):
    column = db.table("track").column("name")
    column.find("normalization").text = "normalization: [trim]"
    assert "T018" in codes(validate_bundle(db))


def test_orphan_fingerprint_payload_is_an_error(db):
    column = db.table("track").column("bytes")
    column.lines = [l for l in column.lines if l.key != "fingerprint"]
    assert "B002" in codes(validate_bundle(db))


# -- pair layout and drift -------------------------------------------------


def test_unsorted_database_pair_is_an_error(rel):
    rel.index.frontmatter["databases"] = ["MUSICSTORE_SALES", "MUSICSTORE_CORE"]
    assert {"I004", "B005"} & codes(validate_bundle(rel))


def test_relationship_table_outside_the_pair_is_an_error(rel):
    doc = rel.relationships[0]
    doc.frontmatter["tables"] = ["OTHER_DB.X.y", "MUSICSTORE_SALES.SALES.invoice_line"]
    assert "R007" in codes(validate_bundle(rel))


def test_stale_built_from_pin_is_an_error(root):
    root.database("MUSICSTORE_CORE").index.frontmatter["build_date"] = "2026-09-01"
    assert "X002" in codes(validate_root(root))


def test_built_from_pin_to_an_absent_bundle_is_a_warning(root):
    doc = root.relationships[0].relationships[0]
    doc.frontmatter["built_from"] = ["db/NOT_HERE@2026-08-23",
                                     "db/MUSICSTORE_SALES@2026-08-23"]
    assert "X001" in codes(validate_root(root), WARNING)


def test_incomplete_source_must_propagate(root):
    root.database("MUSICSTORE_CORE").index.frontmatter["completeness"] = "INCOMPLETE"
    assert "X003" in codes(validate_root(root))


def test_incomplete_source_with_a_propagated_flag_is_clean(root):
    root.database("MUSICSTORE_CORE").index.frontmatter["completeness"] = "INCOMPLETE"
    root.relationships[0].index.frontmatter["completeness"] = "INCOMPLETE"
    assert "X003" not in codes(validate_root(root))


def test_type_that_disagrees_with_the_document_class_is_an_error(db):
    from okf import validate_document

    table = db.table("track")
    table.frontmatter["type"] = "index"
    assert "D001" in codes(validate_document(table, bundle=db))
