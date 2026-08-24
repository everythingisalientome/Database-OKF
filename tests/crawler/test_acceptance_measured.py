"""Acceptance: the measured numbers against the fixture numbers, digit for digit.

    docker compose -f rig/docker-compose.yml up -d --wait
    python -m pytest tests/crawler/test_acceptance_measured.py -v

This is session 3's acceptance line made executable. Both rig databases hold
the same Chinook 1.4.5 data the fixture bundles were generated from
(``tests/fixtures/source/``), loaded under binary collation so text
comparisons mean what the generator's Python comparisons meant. The measuring
pass runs live — B1 counts, batched B2 with length statistics, B3 top-N, B4
format classification, C1 fingerprints — and every measured number is
compared with the number the fixture bundle publishes for the same column.

The fixtures simulate a *split* estate (CORE and SALES as separate SORs);
the rig is one undivided Chinook per engine. Per-column numbers do not care
about that split, which is why this comparison is legitimate where comparing
bundle structure would not be.

One measured value knowingly disagrees with the fixtures and is asserted at
its measured value instead: the fixture generator computed temporal min/max
over the *rendered strings*, where ``'2025/9/7'`` sorts after
``'2025/12/22'``. B2 measures MIN/MAX on the timestamp column itself —
chronologically — and reports ``2025/12/22``, which is the invoice that
actually exists last. Reported per the ground rules; the fixture is not
changed here, and this module documents the one datum where the two differ
(``invoice.invoice_date`` max; every other bound agrees in both orders).
"""

from __future__ import annotations

import json
import os

import pytest

from crawler import CrawlConfig, crawl
from test_fixture_parity import TOP_VALUE, FixtureColumn

pytestmark = pytest.mark.acceptance

CONFIG_DIR = "rig/config"

#: Rig credentials — in rig/docker-compose.yml on purpose; see rig/README.md.
RIG_PASSWORDS = {
    "RIG_PG_PASSWORD": "crawler",
    "RIG_MSSQL_PASSWORD": "Crawler!Rig2026",
}

#: Which fixture bundle each Chinook table's numbers live in.
BUNDLE_OF_TABLE = {
    "artist": "MUSICSTORE_CORE", "album": "MUSICSTORE_CORE",
    "track": "MUSICSTORE_CORE", "genre": "MUSICSTORE_CORE",
    "media_type": "MUSICSTORE_CORE", "playlist": "MUSICSTORE_CORE",
    "playlist_track": "MUSICSTORE_CORE",
    "customer": "MUSICSTORE_SALES", "employee": "MUSICSTORE_SALES",
    "invoice": "MUSICSTORE_SALES", "invoice_line": "MUSICSTORE_SALES",
}

#: The known fixture artifact (see the module docstring): fixture value on
#: the left, chronologically measured value on the right.
KNOWN_ARTIFACTS = {
    ("invoice", "invoice_date", "max"): ("2025/9/7", "2025/12/22"),
}


def rig_result(engine: str, request):
    """Measured crawl of the rig database for ``engine``, or skip."""
    for name, value in RIG_PASSWORDS.items():
        os.environ.setdefault(name, value)
    config = CrawlConfig.load(
        request.config.rootpath / CONFIG_DIR / f"chinook-{engine}.json"
    )
    driver = {"postgres": "psycopg", "sqlserver": "pymssql"}[engine]
    pytest.importorskip(driver, reason=f"{driver} is not installed")

    from crawler import connect

    try:
        connection = connect(config)
    except Exception as exc:  # noqa: BLE001 — any driver error means no rig
        pytest.skip(f"{engine} rig is not reachable: {exc}")
    try:
        return crawl(connection, config, measure=True)
    finally:
        connection.close()


@pytest.fixture(scope="module")
def postgres_measured(request):
    return rig_result("postgres", request)


@pytest.fixture(scope="module")
def sqlserver_measured(request):
    return rig_result("sqlserver", request)


@pytest.fixture(params=["postgres", "sqlserver"])
def any_measured(request):
    return request.getfixturevalue(f"{request.param}_measured")


@pytest.fixture(scope="module")
def fixture_columns(db_bundles):
    columns = {}
    for bundle in db_bundles:
        for table_doc in bundle.tables:
            for column in table_doc.columns:
                fixture = FixtureColumn(bundle.database, table_doc, column)
                columns[(fixture.table, fixture.name)] = (bundle, fixture)
    return columns


def measured_profile(result, table, column):
    return next(
        p
        for p in result.column_profiles
        if p.table == table and p.column == column
    )


def each_fixture_column(result, fixture_columns):
    for (table, name), (bundle, fixture) in sorted(fixture_columns.items()):
        yield bundle, fixture, measured_profile(result, table, name)


# -- the tables -------------------------------------------------------------


def test_the_measuring_pass_ran_clean(any_measured):
    assert any_measured.measured is True
    assert any_measured.completeness == "COMPLETE"
    assert not [w for w in any_measured.warnings if "failed" in w]


def test_row_counts_match_digit_for_digit(any_measured, db_bundles):
    fixture_rows = {
        doc.table: int(doc.frontmatter.get("row_count"))
        for bundle in db_bundles
        for doc in bundle.tables
    }
    measured_rows = {
        p.table: (p.row_count, p.source) for p in any_measured.table_profiles
    }
    assert measured_rows == {
        table: (rows, "live") for table, rows in fixture_rows.items()
    }
    assert not any(p.flags for p in any_measured.table_profiles)


# -- the columns --------------------------------------------------------------


def test_counts_and_rates_match_digit_for_digit(any_measured, fixture_columns):
    for _bundle, fixture, measured in each_fixture_column(
        any_measured, fixture_columns
    ):
        where = f"{fixture.table}.{fixture.name}"
        assert measured.distinct_count == fixture.distinct_count, where
        assert measured.distinct_ratio == fixture.distinct_ratio, where
        assert measured.null_rate == fixture.null_rate, where
        assert measured.dense_sequence == fixture.dense_sequence, where
        assert measured.sensitive == fixture.sensitive, where


def test_bounds_match_digit_for_digit(any_measured, fixture_columns):
    for _bundle, fixture, measured in each_fixture_column(
        any_measured, fixture_columns
    ):
        where = f"{fixture.table}.{fixture.name}"
        if fixture.sensitive:
            assert measured.min_value is None, where
            assert measured.max_value is None, where
            continue
        for side, measured_value in (
            ("min", measured.min_value),
            ("max", measured.max_value),
        ):
            expected = getattr(fixture, f"{side}_value")
            artifact = KNOWN_ARTIFACTS.get((fixture.table, fixture.name, side))
            if artifact is not None:
                assert (expected, measured_value) == artifact, where
                continue
            assert measured_value == expected, f"{where} {side}"


def test_length_statistics_match_digit_for_digit(any_measured, fixture_columns):
    for _bundle, fixture, measured in each_fixture_column(
        any_measured, fixture_columns
    ):
        if fixture.min_length is None:
            continue
        where = f"{fixture.table}.{fixture.name}"
        assert measured.min_length == fixture.min_length, where
        assert measured.max_length == fixture.max_length, where
        assert measured.avg_length == fixture.avg_length, where


def test_formats_match(any_measured, fixture_columns):
    for _bundle, fixture, measured in each_fixture_column(
        any_measured, fixture_columns
    ):
        assert measured.format == fixture.format, f"{fixture.table}.{fixture.name}"


def test_top_n_values_match_the_published_lines(any_measured, fixture_columns):
    """The fixture's ``top_values:`` line shows the six most frequent values;
    the measured pass keeps twenty. A shown value must be measured with the
    same percentage — unless it sits AT the cut frequency of a column with
    more distinct values than the cut keeps. ``ORDER BY freq DESC`` has no
    tiebreaker, so which of ~22 once-occurring states fill the last slots is
    the engine's coin toss, and the generator's Counter tossed its own
    (customer.state's ``DF(3%)`` is exactly such a value). Above the cut
    frequency, membership is a fact and is asserted."""
    checked = 0
    for _bundle, fixture, measured in each_fixture_column(
        any_measured, fixture_columns
    ):
        where = f"{fixture.table}.{fixture.name}"
        if fixture.column.top_values is None:
            assert measured.top_values == (), where
            continue
        measured_pct = {v.value: v.percent for v in measured.top_values}
        cut = (
            min(v.frequency for v in measured.top_values)
            if fixture.distinct_count > len(measured.top_values)
            else 0
        )
        cut_pct = (
            round(100 * cut / fixture.non_null_count) if cut else -1
        )
        for value, percent in TOP_VALUE.findall(fixture.column.top_values):
            value = value.strip(", ")
            if value not in measured_pct:
                assert int(percent) == cut_pct, (
                    f"{where}: {value!r} missing from the measured top-N and "
                    "not at the tie boundary"
                )
                continue
            assert measured_pct[value] == int(percent), f"{where}: {value!r}"
        checked += 1
    assert checked >= 20


def test_fingerprint_payloads_match_byte_for_byte(any_measured, fixture_columns):
    """The whole point of the exercise: same values, same normalization, same
    hashes, same payload shape — so step 2's offline overlap on rig-crawled
    bundles equals overlap on the fixtures."""
    matched = 0
    for bundle, fixture, measured in each_fixture_column(
        any_measured, fixture_columns
    ):
        where = f"{fixture.table}.{fixture.name}"
        if fixture.column.fingerprint is None:
            assert measured.fingerprint is None, where
            continue
        expected = json.loads(
            bundle.resolve(fixture.column.fingerprint).read_text(encoding="utf-8")
        )
        assert measured.fingerprint is not None, where
        assert measured.fingerprint.to_payload() == expected, where
        matched += 1
    assert matched == 39  # every fingerprint file in both fixture bundles


def test_sensitive_columns_contribute_no_value_in_any_form(any_measured):
    """The serialized crawl result of a live database with real addresses,
    phone numbers and emails in it — none may appear anywhere."""
    serialised = json.dumps(any_measured.to_obj())
    for leak in (
        "andrew@chinookcorp.com",  # employee.email
        "11120 Jasper Ave",  # employee.address
        "428-9482",  # employee.phone
        "T5K 2N1",  # employee.postal_code
        "1962/2/18",  # employee.birth_date, rendered
        "luisg@embraer.com.br",  # customer.email
    ):
        assert leak not in serialised, leak
