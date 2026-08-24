"""Byte-exact round-trip.

Bundles are git-committed per refresh and the diff between runs is the drift
signal (specs/04, "Versioning & drift"). A writer that reformats -- reorders
frontmatter keys, drops a comment, normalises a final newline -- would emit
drift where nothing changed. So parse->render must be the identity.
"""

from __future__ import annotations

import json

from okf import parse_document, read_document, render_document
from okf.fingerprints import read_fingerprint, write_fingerprint


def test_document_round_trips_byte_for_byte(fixture_document):
    source = fixture_document.read_text(encoding="utf-8")
    assert render_document(parse_document(source, path=fixture_document)) == source


def test_reparse_is_stable(fixture_document):
    """A second parse/render cycle changes nothing either."""
    doc = read_document(fixture_document)
    once = render_document(doc)
    twice = render_document(parse_document(once))
    assert once == twice


def test_final_newline_is_preserved(fixture_document):
    source = fixture_document.read_text(encoding="utf-8")
    doc = parse_document(source, path=fixture_document)
    assert doc.trailing_newline == source.endswith("\n")


def test_frontmatter_comments_survive(fixture_document):
    source = fixture_document.read_text(encoding="utf-8")
    doc = parse_document(source, path=fixture_document)
    for line in source.split("\n---", 2)[0].splitlines():
        if "  #" in line:
            assert line in render_document(doc)


def test_fingerprint_payload_round_trips(fixture_fingerprint, tmp_path):
    source = fixture_fingerprint.read_text(encoding="utf-8")
    payload = read_fingerprint(fixture_fingerprint)
    out = tmp_path / fixture_fingerprint.name
    write_fingerprint(payload, out)
    assert json.loads(out.read_text()) == json.loads(source)
    assert out.read_text(encoding="utf-8") == source
