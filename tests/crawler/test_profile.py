"""Derived numbers, flags, gates, and the stats-first decision.

:mod:`test_fixture_parity` checks these rules against the ground-truth
bundles, which is the acceptance. This module checks the edges the fixtures
happen not to contain — empty tables, unknown counts, stale statistics — and
the reasoning each decision reports, because a gate that refuses without
saying why turns an absence of evidence into an absence of data.
"""

from __future__ import annotations

from datetime import date

import pytest

from crawler import Column, ColumnStats, TableStats, profile
from crawler.results import GATE_CARDINALITY, GATE_DISTINCT, SENSITIVE

TODAY = date(2026, 8, 23)


def column(name="c", type_="INT", scale=None) -> Column:
    return Column("CORE", "t", name, 1, type_, type_.lower(), True, scale=scale)


# -- rates ------------------------------------------------------------------


def test_null_rate_is_nulls_over_rows():
    assert profile.null_rate(49, 59) == 0.8305


def test_distinct_ratio_is_distincts_over_non_nulls():
    """Not over rows. ``customer.company`` has ten distinct values in the ten
    rows that are not NULL, and its ratio is 1.0, not 0.17."""
    assert profile.distinct_ratio(10, 10) == 1.0


@pytest.mark.parametrize(
    "null_count,row_count", [(None, 59), (5, None), (5, 0)]
)
def test_a_rate_over_an_unknown_or_empty_denominator_is_unknown(
    null_count, row_count
):
    """Zero rows means undefined, not zero: a bundle reporting null_rate 0.0
    for an empty table is claiming a measurement it did not make."""
    assert profile.null_rate(null_count, row_count) is None


def test_rates_round_to_four_places():
    assert profile.distinct_ratio(204, 347) == 0.5879
    assert profile.distinct_ratio(3257, 3503) == 0.9298


def test_average_length_rounds_to_one_place():
    assert profile.average_length(22.66) == 22.7
    assert profile.average_length(None) is None


# -- dense_sequence ---------------------------------------------------------


def test_a_full_range_from_one_is_dense():
    assert profile.dense_sequence(column(), 347, 1, 347)


def test_a_range_with_gaps_is_not():
    assert not profile.dense_sequence(column(), 204, 1, 275)


def test_a_range_not_starting_at_one_is_not():
    assert not profile.dense_sequence(column(), 3, 3, 5)


def test_a_single_value_is_not_a_sequence():
    assert not profile.dense_sequence(column(), 1, 1, 1)


def test_a_text_column_is_never_dense():
    assert not profile.dense_sequence(column(type_="VARCHAR(10)"), 5, 1, 5)


def test_a_scale_zero_decimal_counts_as_an_integer():
    """The legacy surrogate key the specs/01 example is built on:
    ``ACCT_ID DECIMAL(18,0)``."""
    assert profile.dense_sequence(column(type_="DECIMAL(18,0)", scale=0), 10, 1, 10)


def test_a_decimal_with_a_scale_does_not():
    assert not profile.dense_sequence(column(type_="NUMERIC(10,2)", scale=2), 10, 1, 10)


def test_missing_bounds_leave_the_flag_off():
    assert not profile.dense_sequence(column(), 10, None, 10)
    assert not profile.dense_sequence(column(), None, 1, 10)


def test_a_range_just_under_the_fill_threshold_is_not_dense():
    assert profile.dense_sequence(column(), 95, 1, 100)
    assert not profile.dense_sequence(column(), 94, 1, 100)


# -- junk tables ------------------------------------------------------------


PATTERNS = ("_BKP", "_TEST", "_OLD", "TMP_")


@pytest.mark.parametrize(
    "name,rows,expected",
    [
        ("account", 100, False),
        ("account", 0, True),
        ("account_bkp", 100, True),
        ("ACCOUNT_BKP", 100, True),
        ("account_bkp_2019", 100, True),
        ("tmp_load", 100, True),
        ("account_old", 100, True),
        ("bookmark", 100, False),
        ("account", None, False),
    ],
)
def test_junk_tables_are_the_empty_and_the_backups(name, rows, expected):
    assert profile.is_junk_table(name, rows, PATTERNS) is expected


# -- gates ------------------------------------------------------------------


def test_the_fingerprint_gate_passes_a_high_ratio_column():
    gate = profile.fingerprint_gate(
        column(), ratio=0.93, indexed=False, sensitive=False
    )
    assert gate and gate.reason == ""


def test_the_fingerprint_gate_passes_an_indexed_low_ratio_column():
    """``track.genre_id`` has a distinct_ratio of 0.0071 and is fingerprinted:
    somebody built an index on it, which is the join intent the missing
    foreign key would have declared."""
    assert profile.fingerprint_gate(
        column(), ratio=0.0071, indexed=True, sensitive=False
    )


def test_the_fingerprint_gate_refuses_a_status_code_and_says_so():
    gate = profile.fingerprint_gate(
        column(), ratio=0.0006, indexed=False, sensitive=False
    )
    assert not gate
    assert gate.reason == GATE_CARDINALITY


def test_a_sensitive_column_is_refused_before_anything_else_is_considered():
    gate = profile.fingerprint_gate(
        column(), ratio=1.0, indexed=True, sensitive=True
    )
    assert not gate
    assert gate.reason == SENSITIVE


def test_a_temporal_column_passes_the_gate_like_anything_else():
    """Adopted P15: temporal values render canonically before hashing, so a
    date-keyed join candidate is no longer invisible to step 2. The fixtures
    fingerprint employee.hire_date and invoice.invoice_date, and the gate has
    to let them through."""
    assert profile.fingerprint_gate(
        column(type_="TIMESTAMP"), ratio=0.86, indexed=True, sensitive=False
    )
    assert profile.fingerprint_gate(
        column(type_="DATE"), ratio=0.875, indexed=False, sensitive=False
    )


def test_an_unknown_ratio_does_not_pass_the_gate():
    gate = profile.fingerprint_gate(
        column(), ratio=None, indexed=False, sensitive=False
    )
    assert not gate


def test_the_top_n_gate_passes_a_code_list():
    assert profile.top_n_gate(distinct_count=25, sensitive=False)


def test_the_top_n_gate_is_inclusive_at_its_threshold():
    assert profile.top_n_gate(distinct_count=30, sensitive=False)
    assert not profile.top_n_gate(distinct_count=31, sensitive=False)


def test_the_top_n_gate_refuses_a_sensitive_code_list():
    gate = profile.top_n_gate(distinct_count=5, sensitive=True)
    assert not gate
    assert gate.reason == SENSITIVE


def test_an_unknown_distinct_count_does_not_pass_the_top_n_gate():
    gate = profile.top_n_gate(distinct_count=None, sensitive=False)
    assert not gate
    assert gate.reason == GATE_DISTINCT


# -- top-N values -----------------------------------------------------------


def test_percentages_are_against_the_non_null_count():
    values = profile.top_values([("3", 25), ("4", 24)], 70, 20)
    assert [(v.value, v.percent) for v in values] == [("3", 36), ("4", 34)]


def test_the_engines_order_is_kept():
    values = profile.top_values([("b", 1), ("a", 1)], 2, 20)
    assert [v.value for v in values] == ["b", "a"]


def test_the_limit_is_applied():
    rows = [(str(n), 1) for n in range(50)]
    assert len(profile.top_values(rows, 50, 20)) == 20


def test_a_value_too_rare_to_round_to_a_percent_is_still_listed():
    """``track.media_type_id`` lists ``4(0%)``: seven rows in three and a half
    thousand. The frequency is the fact; the percentage is a rendering."""
    values = profile.top_values([("4", 7)], 3503, 20)
    assert values[0].percent == 0
    assert values[0].frequency == 7


# -- stats-first ------------------------------------------------------------


def stats(days_old=0, source="stats"):
    return TableStats(
        "CORE", "t", 100, source=source,
        stats_date=date.fromordinal(TODAY.toordinal() - days_old),
    )


def test_fresh_exact_row_counts_are_believed():
    needed, reason = profile.needs_row_scan(
        stats(), TODAY, max_age_days=90, trust_estimates=False
    )
    assert not needed and "fresh" in reason


def test_stale_row_counts_are_re_measured():
    needed, _ = profile.needs_row_scan(
        stats(days_old=200), TODAY, max_age_days=90, trust_estimates=False
    )
    assert needed


def test_a_planner_estimate_is_counted_rather_than_believed():
    """specs/01 calls row counts load-bearing — rate denominators, overlap
    confidence, the junk filter, step 3's sequencing. A belief is not a
    denominator."""
    needed, reason = profile.needs_row_scan(
        stats(source="stats-estimate"), TODAY, max_age_days=90,
        trust_estimates=False,
    )
    assert needed and "estimate" in reason


def test_an_estimate_can_be_trusted_by_config():
    needed, _ = profile.needs_row_scan(
        stats(source="stats-estimate"), TODAY, max_age_days=90,
        trust_estimates=True,
    )
    assert not needed


def test_no_row_statistics_at_all_means_a_scan():
    needed, _ = profile.needs_row_scan(
        None, TODAY, max_age_days=90, trust_estimates=False
    )
    assert needed


def column_stats(approximate=False, days_old=0, distinct=1000):
    return ColumnStats(
        "CORE", "t", "c",
        distinct_count=distinct,
        stats_date=date.fromordinal(TODAY.toordinal() - days_old),
        approximate=approximate,
    )


def test_approximate_column_statistics_plan_a_scan_rather_than_replace_it():
    """PostgreSQL's n_distinct and SQL Server's histogram sums are estimates.
    specs/01 is explicit that they are for gating; publishing one as an
    ``[observed]`` distinct count would be publishing a guess as a fact."""
    needed, reason = profile.needs_column_scan(
        column_stats(approximate=True), TODAY, max_age_days=90
    )
    assert needed and "approximate" in reason


def test_fresh_exact_column_statistics_save_the_scan():
    """Which is the estate the stats-first policy was written for: Oracle,
    DB2 and Teradata report exact per-column counts."""
    needed, _ = profile.needs_column_scan(
        column_stats(distinct=1000), TODAY, max_age_days=90
    )
    assert not needed


def test_an_exact_count_near_a_gate_is_re_measured_anyway():
    needed, reason = profile.needs_column_scan(
        column_stats(distinct=28), TODAY, max_age_days=90
    )
    assert needed and "gate boundary" in reason


def test_stale_exact_statistics_are_re_measured():
    needed, _ = profile.needs_column_scan(
        column_stats(days_old=400), TODAY, max_age_days=90
    )
    assert needed


def test_no_column_statistics_means_a_scan():
    needed, _ = profile.needs_column_scan(None, TODAY, max_age_days=90)
    assert needed


def test_statistics_with_no_collection_date_are_not_fresh():
    assert not profile.stats_are_fresh(None, TODAY, 90)


def test_freshness_is_inclusive_at_the_threshold():
    edge = date.fromordinal(TODAY.toordinal() - 90)
    assert profile.stats_are_fresh(edge, TODAY, 90)
    assert not profile.stats_are_fresh(
        date.fromordinal(TODAY.toordinal() - 91), TODAY, 90
    )
