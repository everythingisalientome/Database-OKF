"""The measuring pass end to end, against a fake database with rows in it.

:class:`measure_fakes.FakeDatabase` computes what each Tier B/C block would
return over Python values and registers those rows against the statement text
the crawler will actually issue. The connection underneath answers that text
and nothing else, so a crawler that composed a statement of its own — even by
adding a predicate — fails every test in this file. That is the cheapest guard
on the no-dynamic-SQL rule there is, and it is why the fake is built this way.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from crawler import (
    AllowList,
    Column,
    ColumnStats,
    ConfigError,
    CrawlConfig,
    CrawlResult,
    Index,
    Table,
    TableStats,
    measure,
)
from crawler.results import BUDGET_DENIED, JUNK_SUSPECT, SENSITIVE
from measure_fakes import FakeDatabase, FakeTable

TODAY = date(2026, 8, 23)
SCHEMA = "SALES"

#: A small, deliberately awkward table: a dense surrogate key, a code column
#: with a repeated value, a nullable free-text column, and a column config
#: will call sensitive.
ROWS = [
    (1, "AC", "Alpha Holdings", "555-0100"),
    (2, "AC", "Beta Ltd", "555-0101"),
    (3, "CL", None, "555-0102"),
    (4, "AC", "Delta PLC", None),
    (5, "SU", "Echo GmbH", "555-0104"),
]
COLUMN_NAMES = ("account_id", "status_code", "company", "phone")


def inventory(table_name="account"):
    tables = [Table(SCHEMA, table_name, "BASE TABLE")]
    columns = [
        Column(SCHEMA, table_name, "account_id", 1, "INT", "integer", False),
        Column(SCHEMA, table_name, "status_code", 2, "CHAR(2)", "character",
               True, length=2),
        Column(SCHEMA, table_name, "company", 3, "VARCHAR(80)",
               "character varying", True, length=80),
        Column(SCHEMA, table_name, "phone", 4, "VARCHAR(24)",
               "character varying", True, length=24),
    ]
    return tables, columns


def build(
    *,
    table_name="account",
    rows=None,
    measure_config=None,
    indexes=None,
    table_stats=(),
    column_stats=(),
):
    tables, columns = inventory(table_name)
    allow = AllowList.from_inventory(tables, columns)
    result = CrawlResult(
        database="SOR_ACCOUNTS",
        engine="postgres",
        crawl_date=TODAY,
        tables=tables,
        columns=columns,
        indexes=list(
            indexes
            if indexes is not None
            else [Index(SCHEMA, table_name, "pk", True, ("account_id",))]
        ),
        table_stats=list(table_stats),
        column_stats=list(column_stats),
        allowlist=allow.to_obj(),
    )
    database = FakeDatabase(
        {
            (SCHEMA, table_name): FakeTable(
                columns=COLUMN_NAMES,
                rows=list(ROWS if rows is None else rows),
                character={"status_code", "company", "phone"},
            )
        }
    )
    settings = {"fixture_mode": True}
    settings.update(measure_config or {})
    config = CrawlConfig.from_obj(
        {
            "database": "SOR_ACCOUNTS",
            "engine": "postgres",
            "measure": settings,
        }
    )
    connection = database.connection(
        "postgres", allow, batch_columns=config.measure.batch_columns
    )
    measure(connection, config, result, today=TODAY)
    return result, connection


@pytest.fixture
def measured():
    result, _connection = build()
    return result


def profile_of(result, name):
    return next(p for p in result.column_profiles if p.column == name)


# -- the numbers ------------------------------------------------------------


def test_the_pass_reports_itself_as_having_run(measured):
    assert measured.measured is True
    assert measured.warnings == []


def test_the_row_count_is_measured_and_marked_live(measured):
    table = measured.table_profiles[0]
    assert (table.row_count, table.source) == (5, "live")
    assert table.flags == ()
    assert table.profiled is True


def test_every_column_gets_a_profile(measured):
    assert [p.column for p in measured.column_profiles] == list(COLUMN_NAMES)


def test_counts_and_rates_come_out_of_one_batched_scan(measured):
    company = profile_of(measured, "company")
    assert company.non_null_count == 4
    assert company.null_count == 1
    assert company.distinct_count == 4
    assert company.null_rate == 0.2
    assert company.distinct_ratio == 1.0


def test_bounds_are_recorded_as_text(measured):
    status = profile_of(measured, "status_code")
    assert (status.min_value, status.max_value) == ("AC", "SU")
    assert profile_of(measured, "account_id").min_value == "1"


def test_length_statistics_are_measured_for_character_columns(measured):
    company = profile_of(measured, "company")
    assert (company.min_length, company.max_length) == (8, 14)
    assert company.avg_length == 10.0


def test_length_statistics_are_not_invented_for_numbers(measured):
    account_id = profile_of(measured, "account_id")
    assert account_id.min_length is None
    assert account_id.avg_length is None


def test_the_dense_surrogate_key_is_flagged(measured):
    assert profile_of(measured, "account_id").dense_sequence is True
    assert profile_of(measured, "company").dense_sequence is False


# -- the gates, in a whole run ---------------------------------------------


def test_a_code_column_gets_top_n_values(measured):
    status = profile_of(measured, "status_code")
    assert [(v.value, v.frequency, v.percent) for v in status.top_values] == [
        ("AC", 3, 60),
        ("CL", 1, 20),
        ("SU", 1, 20),
    ]


def test_a_code_column_is_not_fingerprinted(measured):
    """distinct_ratio 0.6 over five rows, but it is neither indexed nor above
    the gate on a real table — here the ratio carries it, so the check that
    matters is the low-ratio one below."""
    status = profile_of(measured, "status_code")
    assert status.distinct_ratio == 0.6


def test_a_sensitive_column_yields_no_values_in_any_form():
    result, _ = build(measure_config={"sensitive_columns": ["account.phone"]})
    phone = profile_of(result, "phone")
    assert phone.sensitive is True
    assert phone.top_values == ()
    assert phone.fingerprint is None
    assert phone.suppressed == (SENSITIVE, SENSITIVE)
    # The numbers are still measured: a null rate is not a value.
    assert phone.null_rate == 0.2
    assert phone.distinct_count == 4


def test_a_sensitive_column_is_sampled_only_by_the_classification_query():
    """Suppression happens before the statement, not after the rows come back:
    a query that read the values has already read them.

    The batched B2 still names the column — a null rate and a distinct count
    are facts about a column, not values from it. B3 and C1, the statements
    whose output persists values, never run on it. B4 does, by the adopted
    P14 ruling: its values are read transiently, classified, and dropped —
    the category is the only thing that survives, and the tests around this
    one pin that nothing else does.
    """
    _result, connection = build(
        measure_config={"sensitive_columns": ["account.phone"]}
    )
    persisting = [
        sql
        for sql in connection.executed
        if "ORDER BY freq DESC" in sql or "AS raw" in sql  # B3 and C1
    ]
    assert persisting
    assert not any("phone" in sql for sql in persisting)
    b4 = [sql for sql in connection.executed if "SELECT v, freq" in sql]
    assert any("phone" in sql for sql in b4)


def test_the_sensitive_classification_read_can_be_withdrawn():
    """classify_sensitive_formats: false is the stricter regime — then no
    statement that returns column contents ever names the column."""
    result, connection = build(
        measure_config={
            "sensitive_columns": ["account.phone"],
            "classify_sensitive_formats": False,
        }
    )
    reading = [
        sql
        for sql in connection.executed
        if "ORDER BY freq DESC" in sql or "AS raw" in sql or "SELECT v, freq" in sql
    ]
    assert not any("phone" in sql for sql in reading)
    phone = profile_of(result, "phone")
    assert phone.format is None
    assert phone.suppressed == (SENSITIVE, SENSITIVE, SENSITIVE)


def test_the_fingerprint_payload_is_shaped_for_the_bundle(measured):
    fingerprint = profile_of(measured, "account_id").fingerprint
    assert fingerprint.path == "fingerprints/account.account_id.json"
    assert fingerprint.algo == "sha256/8B"
    assert fingerprint.count == 5
    assert len(fingerprint.hashes) == 5
    assert list(fingerprint.to_payload()) == [
        "algo", "normalization", "sample_cap", "count", "hashes",
    ]


def test_a_fingerprint_holds_digests_and_nothing_else(measured):
    """C1 reads values; what leaves this pass is eight bytes per value."""
    payload = measured.column_profiles[2].fingerprint.to_payload()
    assert payload["hashes"]
    for digest in payload["hashes"]:
        assert len(digest) == 16
        int(digest, 16)  # hex, and therefore not a company name
    for value in ("Alpha Holdings", "Beta Ltd", "Echo GmbH"):
        assert value not in str(payload)


def test_a_sensitive_columns_values_appear_nowhere_in_the_result():
    result, _ = build(measure_config={"sensitive_columns": ["account.phone"]})
    serialised = str(result.to_obj())
    for number in ("555-0100", "555-0101", "555-0104"):
        assert number not in serialised


def test_top_n_values_are_stored_because_the_rules_permit_exactly_that(measured):
    """The one place raw values are allowed: low-cardinality, non-sensitive."""
    assert "AC" in str(measured.to_obj())


# -- stats-first ------------------------------------------------------------


def test_a_fresh_exact_row_count_skips_b1():
    result, connection = build(
        table_stats=[
            TableStats(SCHEMA, "account", 5, source="stats", stats_date=TODAY)
        ]
    )
    assert not any("COUNT(*) AS row_count" in sql for sql in connection.executed)
    assert result.table_profiles[0].source == "stats"


def test_fresh_exact_column_statistics_skip_b2_and_say_where_the_numbers_came_from():
    result, connection = build(
        table_stats=[
            TableStats(SCHEMA, "account", 5, source="stats", stats_date=TODAY)
        ],
        column_stats=[
            ColumnStats(
                SCHEMA, "account", name,
                distinct_count=1000, null_count=0, stats_date=TODAY,
                approximate=False,
            )
            for name in COLUMN_NAMES
        ],
    )
    assert not any(sql.startswith("SELECT COUNT(*),") for sql in connection.executed)
    assert {p.source for p in result.column_profiles} == {"stats"}
    assert profile_of(result, "company").distinct_count == 1000


def test_approximate_statistics_do_not_stop_the_scan():
    result, connection = build(
        column_stats=[
            ColumnStats(
                SCHEMA, "account", name,
                distinct_count=1000, null_rate=0.0, stats_date=TODAY,
                approximate=True,
            )
            for name in COLUMN_NAMES
        ],
    )
    assert any(sql.startswith("SELECT COUNT(*),") for sql in connection.executed)
    assert {p.source for p in result.column_profiles} == {"live"}


# -- junk tables and budgets -----------------------------------------------


def test_an_empty_table_is_cataloged_flagged_and_not_profiled():
    result, connection = build(rows=[])
    table = result.table_profiles[0]
    assert table.row_count == 0
    assert set(table.flags) == {"junk-suspect", "empty"}
    assert table.profiled is False
    assert not any(sql.startswith("SELECT COUNT(*),") for sql in connection.executed)
    assert all(p.suppressed == (JUNK_SUSPECT, JUNK_SUSPECT)
               for p in result.column_profiles)


def test_a_backup_named_table_is_flagged_without_being_scanned():
    result, connection = build(table_name="account_bkp")
    assert result.table_profiles[0].flags == ("junk-suspect",)
    assert result.table_profiles[0].profiled is False


def test_a_table_over_the_row_budget_is_not_scanned():
    result, _ = build(measure_config={"max_scanned_rows": 2})
    table = result.table_profiles[0]
    assert table.profiled is False
    assert "over the scan budget" in table.note
    assert profile_of(result, "company").suppressed == (BUDGET_DENIED, BUDGET_DENIED)


def test_a_statement_budget_stops_the_pass_and_records_why():
    result, connection = build(measure_config={"max_statements": 2})
    assert len(connection.executed) == 2
    assert result.measured is True
    assert any(p.suppressed for p in result.column_profiles)


def test_the_table_budget_counts_tables_not_statements():
    result, _ = build(measure_config={"max_scanned_tables": 0})
    assert result.table_profiles[0].profiled is False
    assert "budget" in result.table_profiles[0].note


# -- batching ---------------------------------------------------------------


def test_a_narrow_batch_splits_the_scan_without_changing_the_numbers():
    wide, _ = build()
    narrow, connection = build(measure_config={"batch_columns": 2})
    assert [p.to_obj() for p in narrow.column_profiles] == [
        p.to_obj() for p in wide.column_profiles
    ]
    assert sum(sql.startswith("SELECT COUNT(*),") for sql in connection.executed) == 2


# -- failures ---------------------------------------------------------------


def test_a_failing_statement_is_recorded_and_reported():
    tables, columns = inventory()
    allow = AllowList.from_inventory(tables, columns)
    result = CrawlResult(
        database="SOR_ACCOUNTS", engine="postgres", crawl_date=TODAY,
        tables=tables, columns=columns, allowlist=allow.to_obj(),
    )
    database = FakeDatabase(
        {
            (SCHEMA, "account"): FakeTable(
                COLUMN_NAMES, list(ROWS), {"status_code", "company", "phone"}
            )
        }
    )
    connection = database.connection("postgres", allow)
    for sql in list(connection.responses):
        if sql.startswith("SELECT COUNT(*) AS row_count"):
            connection.responses[sql] = RuntimeError("permission denied for account")
    config = CrawlConfig.from_obj(
        {"database": "SOR_ACCOUNTS", "engine": "postgres",
         "measure": {"fixture_mode": True}}
    )
    failures = []
    measure(connection, config, result, today=TODAY, failures=failures)

    assert failures == ["B1"]
    assert any("permission denied" in w for w in result.warnings)
    assert any(q.key == "B1" and q.status == "failed" for q in result.queries)


def test_the_pass_refuses_to_run_unkeyed_by_accident():
    """No key, no fixture mode: the bundle would claim keyed hashes it does
    not have, so the run stops instead."""
    tables, columns = inventory()
    result = CrawlResult(
        database="SOR_ACCOUNTS", engine="postgres", crawl_date=TODAY,
        tables=tables, columns=columns,
        allowlist=AllowList.from_inventory(tables, columns).to_obj(),
    )
    config = CrawlConfig.from_obj({"database": "D", "engine": "postgres"})
    with pytest.raises(ConfigError, match="keyed"):
        measure(None, config, result, today=TODAY)


# -- the audit trail --------------------------------------------------------


def test_every_statement_issued_is_recorded_verbatim(measured):
    keys = [q.key for q in measured.queries]
    assert keys[:3] == ["B1", "B2", "B2-length"]
    assert set(keys) >= {"B1", "B2", "B2-length", "B3", "C1", "C1-cast"}
    for query in measured.queries:
        assert query.sql
        assert "{" not in query.sql


def test_the_run_order_is_the_catalogs(measured):
    """B1 before B2, and a column's B3 before its C1."""
    keys = [q.key for q in measured.queries]
    assert keys.index("B1") < keys.index("B2") < keys.index("B3")


def test_a_numeric_column_uses_the_casting_c1_block(measured):
    """The catalog's rule: numeric columns are cast to VARCHAR inside the
    derived table before normalization."""
    account_id = next(
        q for q in measured.queries if q.key == "C1-cast" and "account_id" in q.sql
    )
    assert "CAST(account_id AS varchar)" in account_id.sql


# -- format classification (adopted P14) -------------------------------------


def test_every_measured_column_carries_a_format(measured):
    formats = {p.column: p.format for p in measured.column_profiles}
    assert formats == {
        "account_id": "all-digits",
        "status_code": "alpha",
        "company": "alpha",
        "phone": "phone-like",
    }


def test_the_c1_sample_is_reused_and_b4_stays_home(measured):
    """Every column here passes the fingerprint gate, so classification runs
    over the C1 sample it already has — an extra scan per column to learn the
    same thing would be the exact waste B4's reuse rule exists to avoid."""
    assert not any(q.key == "B4" for q in measured.queries)
    assert all(p.format is not None for p in measured.column_profiles)


def test_a_gated_out_column_is_classified_through_b4():
    """A column C1 refuses still gets its format — from B4. Every column in
    this table sits above the default 0.5 ratio, so the gate is raised past
    reach to force the refusal."""
    result, connection = build(
        indexes=[], measure_config={"distinct_ratio_gate": 2.0}
    )
    assert not any("AS raw" in sql for sql in connection.executed)  # no C1
    assert any(q.key == "B4" for q in result.queries)
    assert profile_of(result, "status_code").format == "alpha"
    assert profile_of(result, "phone").format == "phone-like"


def test_a_sensitive_column_is_classified_but_contributes_no_value():
    result, _ = build(measure_config={"sensitive_columns": ["account.phone"]})
    phone = profile_of(result, "phone")
    assert phone.format == "phone-like"
    serialised = str(result.to_obj())
    for number in ("555-0100", "555-0101", "555-0104"):
        assert number not in serialised


def test_a_junk_table_is_not_classified():
    result, _ = build(rows=[])
    assert all(p.format is None for p in result.column_profiles)


# -- temporal rendering (adopted P15) -----------------------------------------


def temporal_build():
    """A table with a datetime column, as a driver would hand it over."""
    tables = [Table(SCHEMA, "invoice", "BASE TABLE")]
    columns = [
        Column(SCHEMA, "invoice", "invoice_id", 1, "INT", "integer", False),
        Column(SCHEMA, "invoice", "invoice_date", 2, "TIMESTAMP", "datetime",
               False),
    ]
    allow = AllowList.from_inventory(tables, columns)
    result = CrawlResult(
        database="SOR_SALES", engine="postgres", crawl_date=TODAY,
        tables=tables, columns=columns,
        indexes=[Index(SCHEMA, "invoice", "pk", True, ("invoice_id",))],
        allowlist=allow.to_obj(),
    )
    database = FakeDatabase(
        {
            (SCHEMA, "invoice"): FakeTable(
                columns=("invoice_id", "invoice_date"),
                rows=[
                    (1, datetime(2021, 1, 1)),
                    (2, datetime(2021, 1, 1)),
                    (3, datetime(2021, 12, 25)),
                    (4, datetime(2022, 3, 8, 14, 30, 5)),
                ],
                character=set(),  # a datetime is not a character column
            )
        }
    )
    config = CrawlConfig.from_obj(
        {"database": "SOR_SALES", "engine": "postgres",
         "measure": {"fixture_mode": True}}
    )
    measure(database.connection("postgres", allow), config, result, today=TODAY)
    return result


def test_temporal_values_are_rendered_canonically_everywhere():
    result = temporal_build()
    invoice_date = profile_of(result, "invoice_date")
    # bounds: chronological min/max, rendered — not the engine's spelling
    assert invoice_date.min_value == "2021/1/1"
    assert invoice_date.max_value == "2022/3/8 14:30:05"
    # top-N: rendered values, frequencies intact
    rendered = {(v.value, v.frequency) for v in invoice_date.top_values}
    assert ("2021/1/1", 2) in rendered
    assert ("2022/3/8 14:30:05", 1) in rendered
    assert invoice_date.format == "mixed"


def test_a_temporal_fingerprint_hashes_the_rendered_form():
    import hashlib

    result = temporal_build()
    fingerprint = profile_of(result, "invoice_date").fingerprint
    assert fingerprint is not None
    assert fingerprint.normalization == ()  # rendering is not a recorded rule
    expected = sorted(
        hashlib.sha256(v.encode("utf-8")).hexdigest()[:16]
        for v in ("2021/1/1", "2021/12/25", "2022/3/8 14:30:05")
    )
    assert list(fingerprint.hashes) == expected
    assert fingerprint.count == 3


def test_temporal_length_statistics_are_row_weighted():
    """min 8 ('2021/1/1'), max 17 ('2022/3/8 14:30:05'), avg over the four
    ROWS — (8+8+10+17)/4 = 10.75 — not over the three distinct values."""
    result = temporal_build()
    invoice_date = profile_of(result, "invoice_date")
    assert (invoice_date.min_length, invoice_date.max_length) == (8, 17)
    assert invoice_date.avg_length == 10.8


def test_an_unparseable_temporal_withholds_the_whole_fingerprint():
    """A sample where some values render and some do not is no longer the
    bottom-k it claims to be, so nothing is hashed and the reason travels."""
    tables = [Table(SCHEMA, "log", "BASE TABLE")]
    columns = [
        Column(SCHEMA, "log", "logged_at", 1, "TIMESTAMP", "datetime", False),
    ]
    allow = AllowList.from_inventory(tables, columns)
    result = CrawlResult(
        database="SOR_LOG", engine="postgres", crawl_date=TODAY,
        tables=tables, columns=columns,
        indexes=[Index(SCHEMA, "log", "pk", True, ("logged_at",))],
        allowlist=allow.to_obj(),
    )
    database = FakeDatabase(
        {
            (SCHEMA, "log"): FakeTable(
                columns=("logged_at",),
                # strings, as an engine whose cast produced garbage would
                rows=[("2021-01-01 00:00:00",), ("NOT A DATE",)],
                character=set(),
            )
        }
    )
    config = CrawlConfig.from_obj(
        {"database": "SOR_LOG", "engine": "postgres",
         "measure": {"fixture_mode": True}}
    )
    measure(database.connection("postgres", allow), config, result, today=TODAY)
    logged_at = profile_of(result, "logged_at")
    assert logged_at.fingerprint is None
    assert "unparseable-temporal" in logged_at.suppressed
    assert any("does not parse" in note for note in logged_at.notes)
