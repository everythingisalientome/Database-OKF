"""The canonical temporal rendering (adopted P15).

The rendering is a cross-engine contract: step 2 compares hashes that came
from different engines, so ``2002-04-01 00:00:00`` (PostgreSQL),
``Apr  1 2002 12:00AM`` (SQL Server) and a driver's native ``datetime`` all
have to become the same string before hashing. The contract is pinned by the
fixtures — the last test reproduces ``employee.hire_date``'s committed
payload digit for digit from Chinook's hire dates.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from crawler.temporal import parse_temporal, render_temporal


# -- the rendering ------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        # driver-native objects
        (datetime(2002, 4, 1), "2002/4/1"),
        (datetime(2003, 10, 17), "2003/10/17"),
        (date(2004, 3, 4), "2004/3/4"),
        # PostgreSQL / Teradata cast shapes
        ("2002-04-01 00:00:00", "2002/4/1"),
        ("2002-04-01", "2002/4/1"),
        ("2002-04-01T00:00:00", "2002/4/1"),
        ("2002-04-01 00:00:00.000000", "2002/4/1"),
        # SQL Server's legacy varchar rendering, double space and all
        ("Apr  1 2002 12:00AM", "2002/4/1"),
        ("Oct 17 2003 12:00AM", "2003/10/17"),
    ],
)
def test_a_midnight_instant_renders_as_the_bare_date(value, expected):
    assert render_temporal(value) == expected


def test_no_zero_padding_anywhere():
    """``2002/04/01`` and ``2002/4/1`` hash differently; the fixtures use the
    unpadded form and the offline payload check below proves it."""
    assert render_temporal(datetime(2002, 4, 1)) == "2002/4/1"
    assert render_temporal(datetime(2002, 12, 25)) == "2002/12/25"


@pytest.mark.parametrize(
    "value,expected",
    [
        (datetime(2002, 4, 1, 8, 30, 5), "2002/4/1 8:30:05"),
        (datetime(2002, 4, 1, 13, 5, 0), "2002/4/1 13:05:00"),
        ("2002-04-01 08:30:05", "2002/4/1 8:30:05"),
        ("Apr  1 2002 08:30AM", "2002/4/1 8:30:00"),
    ],
)
def test_a_nonzero_time_of_day_is_kept(value, expected):
    """Dropping the time part would merge distinct timestamps into one hash —
    an invented overlap. Midnight appends nothing; anything else does."""
    assert render_temporal(value) == expected


def test_fractional_seconds_are_dropped():
    assert render_temporal(datetime(2002, 4, 1, 8, 30, 5, 999999)) == (
        "2002/4/1 8:30:05"
    )


def test_a_time_only_value_renders_without_a_date():
    assert render_temporal(time(8, 30, 5)) == "8:30:05"
    assert render_temporal("08:30:05") == "8:30:05"


def test_rendering_is_idempotent():
    """A re-crawl that reads its own rendering back must not drift."""
    once = render_temporal(datetime(2003, 10, 17))
    assert render_temporal(once) == once


def test_an_unreadable_value_is_none_not_a_guess():
    """None is the signal the measuring pass withholds the fingerprint on: a
    sample where some values parsed and some did not is no longer a
    bottom-k."""
    assert render_temporal("not a date") is None
    assert render_temporal("") is None
    assert render_temporal(None) is None
    assert parse_temporal("2002-99-99") is None


# -- the fixture pin ----------------------------------------------------------


def test_the_fixture_payload_is_reproduced_from_native_datetimes(fixture_root):
    """Chinook's seven distinct hire dates, as a driver would hand them over,
    through the real renderer and the real hasher, against the committed
    ``employee.hire_date`` payload. This is the P15 ruling as a test."""
    import json

    from crawler.fingerprint import Hasher
    from crawler.normalize import normalize_sample

    hire_dates = [
        datetime(2002, 8, 14),
        datetime(2002, 5, 1),
        datetime(2002, 4, 1),
        datetime(2003, 5, 3),
        datetime(2003, 10, 17),
        datetime(2004, 1, 2),
        datetime(2004, 3, 4),
    ]
    rendered = [render_temporal(value) for value in hire_dates]
    normalized = normalize_sample([(value, value) for value in rendered])

    expected = json.loads(
        (
            fixture_root
            / "db/MUSICSTORE_SALES/fingerprints/SALES.employee.hire_date.json"
        ).read_text(encoding="utf-8")
    )
    assert list(normalized.rules) == expected["normalization"] == []
    assert len(normalized.values) == expected["count"]
    assert list(Hasher(key=None).hash_all(normalized.values)) == expected["hashes"]
