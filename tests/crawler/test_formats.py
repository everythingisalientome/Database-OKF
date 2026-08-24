"""Format-pattern classification (adopted P14).

The vocabulary and the boundaries are the fixture bundles': every assertion
about a whole column here uses a value set the fixtures pin — Chinook's
reference lists, or an integer range the profile numbers determine — and the
category the fixture file publishes for it.
"""

from __future__ import annotations

import pytest

from crawler.formats import (
    ALL_DIGITS,
    ALPHA,
    EMAIL,
    MIXED,
    PHONE_LIKE,
    classify_column,
    classify_value,
)


# -- per-value rules ----------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("347", ALL_DIGITS),
        ("0", ALL_DIGITS),
        ("ROCK", ALPHA),
        ("SÃO PAULO", ALPHA),  # unicode letters are letters
        ("APPLE INC.", ALPHA),  # dots and dashes are name punctuation
        ("O'BRIEN", ALPHA),
        ("EMBRAER - EMPRESA BRASILEIRA DE AERONÁUTICA S.A.", ALPHA),
        ("LUISG@EMBRAER.COM.BR", EMAIL),
        ("+55 (12) 3923-5555", PHONE_LIKE),
        ("555-0100", PHONE_LIKE),
        ("(403) 262-3443", PHONE_LIKE),
        ("ALTERNATIVE & PUNK", MIXED),  # & is not name punctuation
        ("R&B/SOUL", MIXED),
        ("A. F. IOMMI, W. WARD", MIXED),  # a comma is a list, not a name
        ("X1A 1N6", MIXED),  # letters and digits together
        ("00-358", MIXED),  # five digits is a postcode, not a phone
        ("123456789.99", MIXED),  # a dot is not phone punctuation
        ("0.99", MIXED),
        ("2002/4/1", MIXED),  # every rendered temporal
        ("2002/4/1 8:30:05", MIXED),
        ("", MIXED),
    ],
)
def test_one_value_classifies(value, expected):
    assert classify_value(value) == expected


def test_a_plain_digit_string_is_digits_not_a_phone():
    """The digit rule outranks the phone rule: ``5550100`` with no separator
    is just a number, and a surrogate key must never read as phone-like."""
    assert classify_value("5550100") == ALL_DIGITS


# -- plurality over a column ----------------------------------------------------


def test_an_empty_sample_classifies_as_nothing():
    assert classify_column([]) is None


def test_plurality_wins_not_unanimity():
    """``album.title`` is ``alpha`` in the fixtures although titles like
    ``[1997] Black Light Syndrome`` exist — most titles are names."""
    values = ["BACK IN BLACK", "LET THERE BE ROCK", "[1997] SYNDROME"]
    assert classify_column(values) == ALPHA


def test_a_tie_is_mixed():
    """Two equally common shapes is what mixed means."""
    assert classify_column(["ROCK", "42"]) == MIXED


# -- the fixture columns whose values the repo pins -----------------------------

GENRES = [
    "ROCK", "JAZZ", "METAL", "ALTERNATIVE & PUNK", "ROCK AND ROLL", "BLUES",
    "LATIN", "REGGAE", "POP", "SOUNDTRACK", "BOSSA NOVA", "EASY LISTENING",
    "HEAVY METAL", "R&B/SOUL", "ELECTRONICA/DANCE", "WORLD", "HIP HOP/RAP",
    "SCIENCE FICTION", "TV SHOWS", "SCI FI & FANTASY", "DRAMA", "COMEDY",
    "ALTERNATIVE", "CLASSICAL", "OPERA",
]

MEDIA_TYPES = [
    "MPEG AUDIO FILE", "PROTECTED AAC AUDIO FILE",
    "PROTECTED MPEG-4 VIDEO FILE", "PURCHASED AAC AUDIO FILE",
    "AAC AUDIO FILE",
]

EMPLOYEE_TITLES = [
    "GENERAL MANAGER", "SALES MANAGER", "SALES SUPPORT AGENT", "IT MANAGER",
    "IT STAFF",
]

HIRE_DATES = [
    "2002/8/14", "2002/5/1", "2002/4/1", "2003/5/3", "2003/10/17",
    "2004/1/2", "2004/3/4",
]


@pytest.mark.parametrize(
    "values,expected",
    [
        # fixture: genre.name — format: alpha (20 of 25 values are pure names)
        (GENRES, ALPHA),
        # fixture: media_type.name — format: alpha (MPEG-4 is the odd one out)
        (MEDIA_TYPES, ALPHA),
        # fixture: employee.title — format: alpha
        (EMPLOYEE_TITLES, ALPHA),
        # fixture: every integer surrogate — format: all-digits
        ([str(n) for n in range(1, 348)], ALL_DIGITS),
        # fixture: employee.hire_date — format: mixed (rendered temporals)
        (HIRE_DATES, MIXED),
        # fixture: invoice.total / track.unit_price — format: mixed
        (["0.99", "1.98", "3.96", "13.86"], MIXED),
    ],
)
def test_a_fixture_column_classifies_to_its_published_format(values, expected):
    assert classify_column(values) == expected
