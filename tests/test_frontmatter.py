"""Unit tests for the frontmatter subset parser."""

from __future__ import annotations

import datetime as dt

import pytest

from okf import FrontMatter, ParseError
from okf.frontmatter import format_scalar, parse_scalar, split_frontmatter


@pytest.mark.parametrize(
    "text,expected",
    [
        ("true", True),
        ("false", False),
        ("3503", 3503),
        ("-1", -1),
        ("2026-08-23", dt.date(2026, 8, 23)),
        ("COMPLETE", "COMPLETE"),
        ("Track catalog: album, genre, media type", "Track catalog: album, genre, media type"),
        ("2026-13-45", "2026-13-45"),  # not a real date -> stays a string
    ],
)
def test_scalar_typing(text, expected):
    assert parse_scalar(text) == expected
    assert format_scalar(parse_scalar(text)) == text


def test_lists_and_empty_lists():
    fm = FrontMatter.parse(
        ["flags: []", "databases: [A, B]", "built_from: [db/A@2026-08-23]"]
    )
    assert fm["flags"] == []
    assert fm["databases"] == ["A", "B"]
    assert fm["built_from"] == ["db/A@2026-08-23"]


def test_trailing_comment_is_preserved_verbatim():
    line = "completeness: COMPLETE  # reconciliation: visible == cataloged"
    fm = FrontMatter.parse([line])
    assert fm["completeness"] == "COMPLETE"
    assert fm.comment("completeness") == "reconciliation: visible == cataloged"
    assert fm.render() == ["---", line, "---"]


def test_colon_in_value_is_not_a_key_split():
    fm = FrontMatter.parse(["description: Track catalog: album, genre, price"])
    assert fm["description"] == "Track catalog: album, genre, price"


def test_key_order_is_preserved_on_write():
    keys = ["type", "name", "description", "database"]
    fm = FrontMatter.parse([f"{k}: v" for k in keys])
    assert fm.keys() == keys
    fm["engine"] = "teradata"
    assert fm.keys() == keys + ["engine"]


def test_setitem_updates_in_place():
    fm = FrontMatter.parse(["type: table", "row_count: 1"])
    fm["row_count"] = 42
    assert fm.render() == ["---", "type: table", "row_count: 42", "---"]


@pytest.mark.parametrize(
    "lines",
    [
        ["not a frontmatter entry"],
        ["key = value"],
        ["type: table", ""],
        ["type: table", "type: index"],
    ],
)
def test_malformed_frontmatter_raises(lines):
    with pytest.raises(ParseError):
        FrontMatter.parse(lines)


def test_missing_fence_raises():
    with pytest.raises(ParseError):
        split_frontmatter("type: table\n")
    with pytest.raises(ParseError):
        split_frontmatter("---\ntype: table\n")
