"""Acceptance: the emitted bundle passes the session-1 okf validator.

    docker compose -f rig/docker-compose.yml up -d --wait
    python -m pytest tests/crawler/test_acceptance_emitted.py -v

Session 4's acceptance line made executable, against both live rig engines:
a measured crawl of Chinook, the annotation pass (no model attached — every
description the explicit unknown), full bundle emission, and then the okf
package's own validator over what landed on disk. Zero errors is the bar;
warnings are allowed (a one-sentence explicit-unknown database summary is
below the conventions' 3-6 sentence guideline, and says so).
"""

from __future__ import annotations

import pytest

from okf import errors as okf_errors
from okf import read_database_bundle, validate_bundle

from crawler import NullAnnotator, annotate, emit
from test_acceptance_measured import postgres_measured, sqlserver_measured  # noqa: F401

pytestmark = pytest.mark.acceptance

CHINOOK_TABLES = 11


@pytest.fixture(params=["postgres", "sqlserver"])
def measured(request):
    return request.getfixturevalue(f"{request.param}_measured")


def test_the_emitted_bundle_passes_the_validator(measured, tmp_path):
    annotations = annotate(measured, NullAnnotator())
    emission = emit(measured, annotations, tmp_path)

    bundle = read_database_bundle(emission.root)
    issues = validate_bundle(bundle)
    assert okf_errors(issues) == [], [str(i) for i in issues]

    assert len(bundle.tables) == CHINOOK_TABLES
    assert len(bundle.index.entries) == CHINOOK_TABLES
    assert bundle.index.completeness == "COMPLETE"

    # Every payload on disk is referenced and every reference resolves --
    # the validator checked resolution; orphans are checked here explicitly.
    assert bundle.orphan_fingerprints() == []

    # Sensitive columns landed with no values in any form, and the missing
    # fingerprint says why (specs/04 suppression vocabulary).
    customer = bundle.table("customer")
    email = customer.column("email")
    assert email.is_sensitive
    assert email.top_values is None
    assert email.fingerprint is None
    assert email.fingerprint_suppression == "sensitive"
    assert email.value("format") is not None  # the P14 category survives

    # With no model attached, every description is queued for review:
    # one per column, one Purpose per table, one database summary.
    columns = sum(len(t.columns) for t in bundle.tables)
    assert len(bundle.needs_review) == columns + CHINOOK_TABLES + 1


def test_join_intent_lines_land_on_both_sides_and_the_index_counts(
    measured, tmp_path
):
    """Session 6b's acceptance, emitted: the rig view's ON predicate as
    [observed] join-intent lines on album.artist_id and artist.artist_id,
    the unparsed count in index.md, and no definition text anywhere."""
    schema = {"postgres": "public", "sqlserver": "dbo"}[measured.engine]
    annotations = annotate(measured, NullAnnotator())
    emission = emit(measured, annotations, tmp_path)
    bundle = read_database_bundle(emission.root)
    assert okf_errors(validate_bundle(bundle)) == []

    album = bundle.table("album").column("artist_id")
    artist = bundle.table("artist").column("artist_id")
    source = f"{schema}.album_artist_names"
    assert (
        album.value("join-intent")
        == f"artist_id = {schema}.artist.artist_id (source: {source})"
    )
    assert (
        artist.value("join-intent")
        == f"artist_id = {schema}.album.artist_id (source: {source})"
    )
    for line in (album.find("join-intent"), artist.find("join-intent")):
        assert line.provenance.is_observed

    assert bundle.index.summary[1].text == "code_objects: 2; unparsed: 1"
    assert bundle.index.summary[1].provenance.is_observed

    # Definitions stay in the crawl JSON: no object SQL reaches the bundle.
    everything = "".join(
        p.read_text(encoding="utf-8")
        for p in emission.root.rglob("*.md")
    )
    assert "EXEC" not in everything
    assert "QUOTENAME" not in everything
