"""Canonical type rendering.

The vocabulary has to survive a round trip through step 2's type gate and
step 3's expected-type filter, on databases that may sit on different
engines. What matters here: the same logical type from two engines renders
the same string, and a type nobody anticipated is still legible.
"""

from __future__ import annotations

import pytest

from crawler import canonical_type


@pytest.mark.parametrize(
    ("raw", "kwargs", "expected"),
    [
        ("integer", {}, "INT"),
        ("int", {}, "INT"),
        ("int4", {}, "INT"),
        ("bigint", {}, "BIGINT"),
        ("smallint", {}, "SMALLINT"),
        ("character varying", {"length": 160}, "VARCHAR(160)"),
        ("varchar", {"length": 200}, "VARCHAR(200)"),
        ("nvarchar", {"length": 160}, "NVARCHAR(160)"),
        ("char", {"length": 2}, "CHAR(2)"),
        ("numeric", {"precision": 10, "scale": 2}, "NUMERIC(10,2)"),
        ("decimal", {"precision": 18, "scale": 0}, "DECIMAL(18,0)"),
        ("numeric", {"precision": 10}, "NUMERIC(10)"),
        ("timestamp without time zone", {}, "TIMESTAMP"),
        ("datetime", {}, "TIMESTAMP"),
        ("timestamp with time zone", {}, "TIMESTAMP WITH TIME ZONE"),
        ("date", {}, "DATE"),
        ("boolean", {}, "BOOLEAN"),
        ("text", {}, "TEXT"),
    ],
)
def test_engine_spellings_canonicalise(raw, kwargs, expected):
    assert canonical_type(raw, **kwargs) == expected


def test_decimal_and_numeric_stay_distinct():
    """Folding them would lose what the engine actually declared — and the
    spec's own Teradata example is DECIMAL(18,0), not NUMERIC(18,0)."""
    assert canonical_type("decimal", precision=18, scale=0) == "DECIMAL(18,0)"
    assert canonical_type("numeric", precision=18, scale=0) == "NUMERIC(18,0)"


def test_length_is_omitted_when_the_dictionary_gives_none():
    assert canonical_type("character varying") == "VARCHAR"


def test_sqlserver_max_length_sentinel_reads_as_max():
    assert canonical_type("varchar", length=-1) == "VARCHAR(MAX)"


def test_unknown_types_are_kept_not_dropped():
    """A type step 3 cannot filter on is a nuisance; a column that vanished
    because its type was unrecognised is a bug."""
    assert canonical_type("hstore") == "HSTORE"
    assert canonical_type("st_geometry", length=8) == "ST_GEOMETRY"
    assert canonical_type(None) == "UNKNOWN"


def test_length_is_ignored_for_types_that_have_no_length():
    assert canonical_type("integer", length=32) == "INT"
