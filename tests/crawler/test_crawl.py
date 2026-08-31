"""The Tier A run: order, scope, the allow-list, and reconciliation.

Every test here drives the crawl through :class:`FakeConnection`, which
answers registered statement text and raises on anything else — so "the
crawler issued SQL nobody wrote down" is a test failure, not a code review
finding.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from crawler import CrawlConfig, CrawlResult, QueryError, catalog, crawl
from crawler.adapters import for_engine
from fakes import FakeConnection, responses_for

CRAWL_DATE = date(2026, 8, 23)


def pg_config(**overrides) -> CrawlConfig:
    settings = {
        "database": "CHINOOK_PG",
        "engine": "postgres",
        "schemas": {"include": ["public"]},
        "expected_table_count": 2,
    }
    settings.update(overrides)
    return CrawlConfig.from_obj(settings)


@pytest.fixture
def result(pg_connection):
    return crawl(pg_connection, pg_config(), today=CRAWL_DATE)


# -- what runs, and in what order ------------------------------------------


def test_runs_exactly_the_catalog_statements_for_the_engine(pg_connection, result):
    expected = [
        statement.sql for statement in catalog.statements_for("postgres")
    ]
    assert pg_connection.executed == expected


def test_reconciliation_runs_last(result):
    ran = [q.key for q in result.queries if q.status == "ok"]
    assert ran == ["A1", "A2", "A3", "A4", "A6", "A5"]


def test_nothing_is_skipped_now_the_catalog_is_complete(result):
    assert [q.key for q in result.queries if q.status != "ok"] == []


def test_the_audit_trail_records_the_sql_verbatim(result):
    a1 = next(q for q in result.queries if q.key == "A1")
    assert a1.sql == catalog.statement("postgres", "A1").sql
    assert a1.rows == 4
    assert a1.variant == "ansi"
    assert a1.query_id == "A1"


def test_no_gaps_apply_now_every_proposal_is_adopted(result):
    """P1-P13 are in the catalog and the P14/P15 rulings were adjudicated and
    implemented, so the register is empty. An empty gap list is a claim —
    this crawl is not quietly missing evidence — so it is asserted rather
    than assumed."""
    assert result.gaps == []


# -- scope ------------------------------------------------------------------


def test_system_schemas_are_excluded_without_being_asked(result):
    """The catalog's schema-scope policy: pg_catalog is never crawled, config
    or no config."""
    assert {t.schema for t in result.tables} == {"public"}
    assert "pg_class" not in {t.name for t in result.tables}
    assert result.scope.system_schemas == ("pg_catalog",)
    assert result.scope.system_tables == 1


def test_the_exclusion_is_recorded_not_silent(result):
    """"Deliberately skipped" and "was not there" have to read differently."""
    assert any(
        "system schemas excluded by policy: pg_catalog" in warning
        for warning in result.warnings
    )


def test_system_schemas_go_without_a_config_filter(pg_rows):
    result = crawl(
        FakeConnection(responses_for("postgres", pg_rows)),
        pg_config(schemas={}, expected_table_count=None),
        today=CRAWL_DATE,
    )
    assert {t.schema for t in result.tables} == {"public"}
    assert result.scope.system_schemas == ("pg_catalog",)
    assert result.scope.config_schemas == ()
    assert not result.scope.config_filtered


def test_config_exclusions_are_recorded_separately(pg_rows):
    pg_rows["A1"] = [
        *pg_rows["A1"],
        ("chinook", "archive", "album_2019", "BASE TABLE"),
    ]
    result = crawl(
        FakeConnection(responses_for("postgres", pg_rows)),
        pg_config(),
        today=CRAWL_DATE,
    )
    assert result.scope.config_schemas == ("archive",)
    assert result.scope.config_tables == 1
    assert result.scope.system_schemas == ("pg_catalog",)
    assert result.scope.config_filtered


# -- what gets cataloged ----------------------------------------------------


def test_views_are_cataloged_but_do_not_count_as_base_tables(result):
    assert "album_titles" in {t.name for t in result.tables}
    assert {t.name for t in result.base_tables} == {"album", "artist"}


def test_columns_and_constraints_are_read(result):
    assert len(result.columns) == 6
    album_id = next(c for c in result.columns if c.qualified == "public.album.album_id")
    assert album_id.type == "INT"
    assert {(c.kind, c.table) for c in result.constraints} == {
        ("PRIMARY KEY", "album"),
        ("PRIMARY KEY", "artist"),
        ("FOREIGN KEY", "album"),
    }


def test_foreign_keys_resolve_to_their_target(result):
    """What the adopted A3 block (P6) bought: a target, not just a name."""
    fk = next(c for c in result.constraints if c.kind == "FOREIGN KEY")
    assert fk.name == "fk_album_artist"
    assert fk.columns == ("artist_id",)
    assert fk.referenced_table == "public.artist"
    assert fk.referenced_columns == ("artist_id",)


def test_indexes_are_read(result):
    """What the adopted A4 block (P1) bought: PostgreSQL index evidence."""
    assert {(i.table, i.name, i.unique) for i in result.indexes} == {
        ("album", "pk_album", True),
        ("album", "ifk_album_artist_id", False),
        ("artist", "pk_artist", True),
    }


def test_dictionary_statistics_are_read(result):
    """What the adopted A6 block (P2) bought: stats-first on PostgreSQL."""
    tables = {s.table: s for s in result.table_stats}
    assert tables["album"].row_count == 347
    assert tables["album"].source == "stats-estimate"
    assert tables["album"].estimated is True

    columns = {(s.table, s.column): s for s in result.column_stats}
    # n_distinct -1.0 means every value distinct: 347 rows, 347 values.
    assert columns[("album", "album_id")].distinct_count == 347
    # -0.9 is a ratio, not a count.
    assert columns[("album", "title")].distinct_count == 312
    assert columns[("artist", "artist_id")].distinct_count == 275
    assert all(s.approximate for s in result.column_stats)
    assert columns[("album", "album_id")].null_rate == 0.0


def test_the_allowlist_is_built_from_this_crawls_own_inventory(result):
    from crawler import AllowList

    allowlist = AllowList.from_obj(result.allowlist)
    assert allowlist.table_names() == (
        "public.album",
        "public.album_titles",
        "public.artist",
    )
    assert allowlist.column_names("public", "album") == (
        "album_id",
        "title",
        "artist_id",
    )
    assert "pg_catalog.pg_class" not in allowlist


def test_a_column_whose_table_a1_never_returned_is_reported(pg_rows):
    pg_rows["A2"] = [
        *pg_rows["A2"],
        ("public", "hidden", "secret", 1, "integer", None, 32, 0, "NO", None),
    ]
    result = crawl(
        FakeConnection(responses_for("postgres", pg_rows)),
        pg_config(),
        today=CRAWL_DATE,
    )
    assert "public.hidden" not in {t.qualified for t in result.tables}
    assert not any(c.table == "hidden" for c in result.columns)
    assert any("A1 did not list" in w for w in result.warnings)


def test_warnings_about_excluded_schemas_are_not_this_bundles_problem(pg_rows):
    """Statements run over the whole account view, so a reader sees rows for
    schemas the run excludes. Their warnings belong to somebody else."""
    pg_rows["A3"] = [
        *pg_rows["A3"],
        ("UNIQUE", "pg_catalog", "pg_enum", "pg_enum_oid_index", "oid", 1,
         None, None, None, None),
        ("UNIQUE", "pg_catalog", "pg_enum", "pg_enum_oid_index", "enumtypid", 1,
         None, None, None, None),
    ]
    result = crawl(
        FakeConnection(responses_for("postgres", pg_rows)),
        pg_config(),
        today=CRAWL_DATE,
    )
    assert not any("pg_enum" in warning for warning in result.warnings)
    assert not any(c.schema == "pg_catalog" for c in result.constraints)


# -- reconciliation ---------------------------------------------------------


def test_matching_the_expected_count_is_complete(result):
    assert result.reconciliation.status == "COMPLETE"
    assert result.reconciliation.cataloged_tables == 2
    assert result.reconciliation.expected_tables == 2
    assert result.reconciliation.visible_tables == 11
    assert result.completeness == "COMPLETE"


def test_missing_tables_flag_the_bundle_incomplete(pg_connection):
    result = crawl(
        pg_connection, pg_config(expected_table_count=5), today=CRAWL_DATE
    )
    assert result.reconciliation.status == "INCOMPLETE"
    assert "cataloged 2 of 5 expected base tables" in result.reconciliation.note


def test_an_unfiltered_crawl_reconciles_by_adding_back_the_system_tables(pg_rows):
    """A5 counts every base table the account sees, system schemas included.
    Comparing it with a catalog that excludes them by policy needs the
    arithmetic spelled out, or every crawl reads INCOMPLETE."""
    pg_rows["A5"] = [(3,)]  # album, artist, pg_class
    result = crawl(
        FakeConnection(responses_for("postgres", pg_rows)),
        pg_config(schemas={}, expected_table_count=None),
        today=CRAWL_DATE,
    )
    assert result.reconciliation.status == "COMPLETE"
    assert result.reconciliation.cataloged_tables == 2
    assert "plus 1 in system schemas = 3 of 3 visible" in result.reconciliation.note


def test_an_unfiltered_crawl_that_does_not_add_up_is_incomplete(pg_rows):
    pg_rows["A5"] = [(9,)]  # six base tables this account cannot see
    result = crawl(
        FakeConnection(responses_for("postgres", pg_rows)),
        pg_config(schemas={}, expected_table_count=None),
        today=CRAWL_DATE,
    )
    assert result.reconciliation.status == "INCOMPLETE"


def test_a_config_filtered_crawl_reconciles_against_its_filtered_expectation(pg_rows):
    """expected_table_count is the DBA's count of what this run should
    catalog — the count *inside* the filter. A filtered crawl with one is
    verifiable; the account-wide A5 number is recorded but not compared."""
    pg_rows["A1"] = [
        *pg_rows["A1"],
        ("chinook", "archive", "album_2019", "BASE TABLE"),
    ]
    result = crawl(
        FakeConnection(responses_for("postgres", pg_rows)),
        pg_config(expected_table_count=2),
        today=CRAWL_DATE,
    )
    assert result.scope.config_filtered
    assert result.reconciliation.status == "COMPLETE"
    assert result.reconciliation.cataloged_tables == 2
    assert result.reconciliation.expected_tables == 2
    assert "cataloged 2 of 2 expected base tables" in result.reconciliation.note


def test_a_config_filtered_crawl_without_an_expected_count_is_unverified(pg_rows):
    """Config dropped a schema, so A5's count is not comparable with anything
    the crawl measured. Guessing they match would turn a grant gap into a
    clean bill of health."""
    pg_rows["A1"] = [
        *pg_rows["A1"],
        ("chinook", "archive", "album_2019", "BASE TABLE"),
    ]
    result = crawl(
        FakeConnection(responses_for("postgres", pg_rows)),
        pg_config(expected_table_count=None),
        today=CRAWL_DATE,
    )
    assert result.reconciliation.status == "UNVERIFIED"
    assert "config-filtered" in result.reconciliation.note
    assert "archive" in result.reconciliation.note


def test_a_grant_gap_shows_up_as_incomplete(pg_rows):
    responses = responses_for("postgres", pg_rows)
    responses[catalog.statement("postgres", "A3").sql] = PermissionError(
        "permission denied for table table_constraints"
    )
    result = crawl(FakeConnection(responses), pg_config(), today=CRAWL_DATE)
    failed = next(q for q in result.queries if q.key == "A3")
    assert failed.status == "failed"
    assert result.constraints == []
    assert result.reconciliation.status == "INCOMPLETE"
    assert "downgraded" in result.reconciliation.note
    assert any("A3 failed" in w for w in result.warnings)


def test_losing_the_inventory_stops_the_crawl(pg_rows):
    responses = responses_for("postgres", pg_rows)
    responses[catalog.statement("postgres", "A1").sql] = PermissionError("nope")
    with pytest.raises(QueryError, match="A1"):
        crawl(FakeConnection(responses), pg_config(), today=CRAWL_DATE)


# -- a catalog gap, if one ever reappears -----------------------------------


def test_a_query_with_no_catalog_block_is_skipped_and_explained(pg_rows):
    """The catalog covers every Tier A query today. The machinery for when it
    does not still has to work — a skipped query is recorded, explained, and
    does not pretend the evidence was collected and empty."""

    class NoIndexBlock(type(for_engine("postgres"))):
        def statement(self, key):
            return None if key == "A4" else super().statement(key)

    del pg_rows["A4"]
    result = crawl(
        FakeConnection(responses_for("postgres", pg_rows)),
        pg_config(),
        adapter=NoIndexBlock(),
        today=CRAWL_DATE,
    )
    skipped = next(q for q in result.queries if q.status == "skipped")
    assert skipped.key == "A4"
    assert skipped.sql == ""
    assert result.indexes == []
    assert any("A4 not run" in w for w in result.warnings)
    # A known limitation of our catalog, not an unknown hole in the crawl.
    assert result.reconciliation.status == "COMPLETE"


# -- engines ----------------------------------------------------------------


def test_sqlserver_runs_both_a6_blocks(mssql_rows):
    config = CrawlConfig.from_obj(
        {
            "database": "CHINOOK_MSSQL",
            "engine": "sqlserver",
            "expected_table_count": 1,
        }
    )
    result = crawl(
        FakeConnection(responses_for("sqlserver", mssql_rows)), config, today=CRAWL_DATE
    )
    assert [q.key for q in result.queries] == [
        "A1", "A2", "A3", "A4", "A6", "A6-columns", "A5",
    ]
    assert [q.query_id for q in result.queries].count("A6") == 2
    assert result.indexes[0].name == "pk_album"
    assert result.table_stats[0].row_count == 347
    assert result.table_stats[0].estimated is False
    assert result.column_stats[0].distinct_count == 347
    assert result.column_stats[0].approximate is True
    assert result.completeness == "COMPLETE"


def test_sqlserver_excludes_its_own_system_schemas(mssql_rows):
    config = CrawlConfig.from_obj(
        {"database": "CHINOOK_MSSQL", "engine": "sqlserver"}
    )
    result = crawl(
        FakeConnection(responses_for("sqlserver", mssql_rows)), config, today=CRAWL_DATE
    )
    assert {t.schema for t in result.tables} == {"dbo"}
    assert result.scope.system_schemas == ("INFORMATION_SCHEMA",)
    # The excluded object is a view, so it is not in the base table count.
    assert result.scope.system_tables == 0


def test_an_unverified_engine_flags_the_whole_crawl(teradata_rows):
    config = CrawlConfig.from_obj(
        {
            "database": "FINANCE_TD",
            "engine": "teradata",
            "expected_table_count": 1,
        }
    )
    result = crawl(
        FakeConnection(responses_for("teradata", teradata_rows)),
        config,
        today=CRAWL_DATE,
    )
    assert result.engine_verified is False
    assert result.completeness == "UNVERIFIED"
    assert result.indexes[0].primary_index is True
    assert result.scope.system_schemas == ("DBC",)
    assert result.table_stats[0].row_count == 4811002
    assert result.reconciliation.visible_tables == 1
    assert any("never been run against a live system" in w for w in result.warnings)
    assert "unverified" in result.reconciliation.note


def test_the_adapter_can_be_injected(pg_connection):
    """Session 6 swaps in a fixed adapter without touching the crawl."""
    result = crawl(
        pg_connection, pg_config(), adapter=for_engine("postgres"), today=CRAWL_DATE
    )
    assert result.engine == "postgres"


# -- the artifact -----------------------------------------------------------


def test_the_result_is_json_and_survives_a_round_trip(result):
    text = json.dumps(result.to_obj(), indent=2)
    restored = CrawlResult.from_obj(json.loads(text))
    assert restored.to_obj() == result.to_obj()
    assert restored.crawl_date == CRAWL_DATE
    assert restored.columns[0] == result.columns[0]
    assert restored.scope == result.scope


def test_two_runs_of_an_unchanged_database_are_identical(pg_rows):
    """The refresh diff is the drift signal; a crawl that reordered its own
    output between runs would drown it."""
    first = crawl(
        FakeConnection(responses_for("postgres", pg_rows)),
        pg_config(),
        today=CRAWL_DATE,
    )
    second = crawl(
        FakeConnection(responses_for("postgres", pg_rows)),
        pg_config(),
        today=CRAWL_DATE,
    )
    assert json.dumps(first.to_obj()) == json.dumps(second.to_obj())
