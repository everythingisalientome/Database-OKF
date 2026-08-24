"""Normalization, and the record of which rules applied.

The record is the part worth testing hard. specs/00 decision 7 has step 3
re-applying these rules in generated SQL, so a rule that was applied and not
recorded is a query that will not match, and a rule that was recorded and not
applied is the same bug facing the other way.
"""

from __future__ import annotations

import pytest

from crawler.normalize import (
    STRIP_LEADING_ZEROS,
    TRIM,
    UPPERCASE,
    normalize_sample,
    strip_leading_zeros,
)


# -- the leading-zero rule --------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0012", "12"),
        ("12", "12"),
        ("000", "0"),
        ("0", "0"),
        ("", ""),
        ("007A", "007A"),  # not all digits: an account format, left alone
        ("00-358", "00-358"),
        ("1.50", "1.50"),
    ],
)
def test_leading_zeros_go_only_from_all_digit_values(value, expected):
    assert strip_leading_zeros(value) == expected


def test_a_column_of_zeros_keeps_one():
    """``"000"`` is the number zero. Stripping it to nothing would make it
    collide with a genuinely empty value, which is a different fact."""
    assert strip_leading_zeros("000") == "0"


# -- what the rule record says ----------------------------------------------


def test_a_rule_that_changed_nothing_is_not_recorded():
    """C1 applies TRIM and UPPER to every column, so a declared rule list
    would say so for all of them. The fixtures record what *happened*:
    ``genre.name`` carries ``[uppercase]`` and not ``[trim, uppercase]``."""
    sample = [("ROCK", "Rock"), ("JAZZ", "Jazz")]
    assert normalize_sample(sample).rules == (UPPERCASE,)


def test_trimming_is_recorded_when_a_value_needed_it():
    sample = [("OSLO", " Oslo "), ("BERLIN", "Berlin")]
    assert normalize_sample(sample).rules == (TRIM, UPPERCASE)


def test_a_numeric_column_records_no_rules():
    """Casting an integer to text produces no whitespace and no letters, so
    neither rule can bite — which is why the fixtures' integer fingerprints
    carry an empty normalization list."""
    sample = [("1", "1"), ("2", "2"), ("347", "347")]
    assert normalize_sample(sample).rules == ()


def test_leading_zero_stripping_is_recorded():
    sample = [("00012", "00012"), ("13", "13")]
    normalized = normalize_sample(sample)
    assert normalized.rules == (STRIP_LEADING_ZEROS,)
    assert normalized.values == ("12", "13")


def test_rules_are_recorded_in_pipeline_order():
    sample = [("0012", " 0012 "), ("ABC", "abc")]
    assert normalize_sample(sample).rules == (TRIM, UPPERCASE, STRIP_LEADING_ZEROS)


def test_the_engines_own_upper_casing_is_the_authority():
    """The comparison is the engine's normalized value against the raw one,
    not Python's idea of upper-casing. A engine that folds a character
    differently is still reported correctly."""
    sample = [("STRASSE", "Straße")]
    assert normalize_sample(sample).rules == (UPPERCASE,)


def test_a_missing_raw_representative_records_no_text_rules():
    """An engine whose C1 block predates P13 returns only the normalized
    value. Recording rules that cannot be observed would be a guess."""
    assert normalize_sample([("ROCK",), ("JAZZ",)]).rules == ()


# -- the values themselves --------------------------------------------------


def test_selection_order_survives_normalization():
    """The rows arrive ranked by the engine's selection hash, and that order
    is what makes the first k of them a bottom-k rather than a slice."""
    sample = [("C", "c"), ("A", "a"), ("B", "b")]
    assert normalize_sample(sample).values == ("C", "A", "B")


def test_normalization_can_merge_values_and_says_how_many():
    sample = [("12", "0012"), ("12", "12"), ("13", "13")]
    normalized = normalize_sample(sample)
    assert normalized.values == ("12", "13")
    assert normalized.collapsed == 1


def test_head_keeps_the_front_of_the_ranking():
    normalized = normalize_sample([(str(i), str(i)) for i in range(10)])
    assert normalized.head(3).values == ("0", "1", "2")
    assert normalized.head(3).rules == normalized.rules


def test_head_beyond_the_sample_is_the_sample():
    normalized = normalize_sample([("A", "A")])
    assert normalized.head(500) is normalized


def test_a_null_normalized_value_is_dropped_not_hashed():
    """C1 filters NULLs, so one arriving means a driver quirk. Hashing the
    string "None" would put a fake value in the fingerprint."""
    assert normalize_sample([(None, None), ("A", "A")]).values == ("A",)
