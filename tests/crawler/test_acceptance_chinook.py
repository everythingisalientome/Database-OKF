"""Acceptance: crawl the live rig and check it against known Chinook facts.

    docker compose -f rig/docker-compose.yml up -d --wait
    python -m pytest tests/crawler/test_acceptance_chinook.py -v

Skipped when the rig is not running, so the default suite stays offline.

What is asserted is Chinook — 11 tables, its columns and types, its keys, its
indexes — not the OKF fixtures. The fixtures under ``tests/fixtures/okf``
simulate an estate where Chinook has been split across two databases, and
checking a single-database crawl against them would be checking the wrong
thing.
"""

from __future__ import annotations

import os

import pytest

from crawler import AllowList, AllowListError, CrawlConfig, catalog, crawl

pytestmark = pytest.mark.acceptance

CONFIG_DIR = "rig/config"

#: Rig credentials. Not secrets — they are in rig/docker-compose.yml, which
#: is what makes a one-command acceptance run possible.
RIG_PASSWORDS = {
    "RIG_PG_PASSWORD": "crawler",
    "RIG_MSSQL_PASSWORD": "Crawler!Rig2026",
}

CHINOOK_TABLES = {
    "album", "artist", "customer", "employee", "genre", "invoice",
    "invoice_line", "media_type", "playlist", "playlist_track", "track",
}

#: Chinook 1.4.5's row count per table — the same numbers the fixture
#: bundles publish, because both came from the same data.
CHINOOK_ROW_COUNTS = {
    "artist": 275, "album": 347, "track": 3503, "genre": 25, "media_type": 5,
    "playlist": 18, "playlist_track": 8715, "customer": 59, "employee": 8,
    "invoice": 412, "invoice_line": 2240,
}

#: Chinook's column count per table.
CHINOOK_COLUMN_COUNTS = {
    "album": 3, "artist": 2, "customer": 13, "employee": 15, "genre": 2,
    "invoice": 9, "invoice_line": 5, "media_type": 2, "playlist": 2,
    "playlist_track": 2, "track": 9,
}

#: Every foreign key in Chinook: (table, column) -> (referenced table, column).
CHINOOK_FOREIGN_KEYS = {
    ("album", "artist_id"): ("artist", "artist_id"),
    ("customer", "support_rep_id"): ("employee", "employee_id"),
    ("employee", "reports_to"): ("employee", "employee_id"),
    ("invoice", "customer_id"): ("customer", "customer_id"),
    ("invoice_line", "invoice_id"): ("invoice", "invoice_id"),
    ("invoice_line", "track_id"): ("track", "track_id"),
    ("playlist_track", "playlist_id"): ("playlist", "playlist_id"),
    ("playlist_track", "track_id"): ("track", "track_id"),
    ("track", "album_id"): ("album", "album_id"),
    ("track", "genre_id"): ("genre", "genre_id"),
    ("track", "media_type_id"): ("media_type", "media_type_id"),
}

#: Chinook's secondary indexes, one per foreign key column.
CHINOOK_SECONDARY_INDEXES = {
    "ifk_album_artist_id", "ifk_customer_support_rep_id",
    "ifk_employee_reports_to", "ifk_invoice_customer_id",
    "ifk_invoice_line_invoice_id", "ifk_invoice_line_track_id",
    "ifk_playlist_track_track_id", "ifk_track_album_id",
    "ifk_track_genre_id", "ifk_track_media_type_id",
}


def _schema(crawl_result) -> str:
    """The one business schema the rig database has, per engine."""
    return {"postgres": "public", "sqlserver": "dbo"}[crawl_result.engine]


def rig_result(engine: str, request):
    """Crawl the rig database for ``engine``, or skip if it is not up."""
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
        return crawl(connection, config)
    finally:
        connection.close()


@pytest.fixture(scope="module")
def postgres_crawl(request):
    return rig_result("postgres", request)


@pytest.fixture(scope="module")
def sqlserver_crawl(request):
    return rig_result("sqlserver", request)


@pytest.fixture(params=["postgres", "sqlserver"])
def any_crawl(request):
    return request.getfixturevalue(f"{request.param}_crawl")


# -- facts both engines must report the same way ---------------------------


def test_all_eleven_chinook_tables_are_cataloged(any_crawl):
    assert {t.name for t in any_crawl.base_tables} == CHINOOK_TABLES
    assert len(any_crawl.base_tables) == 11


def test_every_table_has_its_columns(any_crawl):
    base = {t.name for t in any_crawl.base_tables}
    counts = {}
    for column in any_crawl.columns:
        if column.table in base:
            counts[column.table] = counts.get(column.table, 0) + 1
    assert counts == CHINOOK_COLUMN_COUNTS


def test_column_order_follows_the_dictionary(any_crawl):
    track = sorted(
        (c for c in any_crawl.columns if c.table == "track"), key=lambda c: c.ordinal
    )
    assert [c.name for c in track] == [
        "track_id", "name", "album_id", "media_type_id", "genre_id",
        "composer", "milliseconds", "bytes", "unit_price",
    ]


def test_nullability_is_read_from_the_dictionary(any_crawl):
    by_name = {(c.table, c.name): c for c in any_crawl.columns}
    assert by_name[("track", "track_id")].nullable is False
    assert by_name[("track", "composer")].nullable is True
    assert by_name[("customer", "email")].nullable is False


def test_engine_neutral_types_canonicalise_identically(any_crawl):
    by_name = {(c.table, c.name): c for c in any_crawl.columns}
    assert by_name[("track", "track_id")].type == "INT"
    assert by_name[("track", "unit_price")].type == "NUMERIC(10,2)"
    assert by_name[("invoice", "invoice_date")].type == "TIMESTAMP"
    assert by_name[("employee", "hire_date")].type == "TIMESTAMP"


def test_every_table_has_its_primary_key(any_crawl):
    pks = {
        c.table: c.columns
        for c in any_crawl.constraints
        if c.kind == "PRIMARY KEY"
    }
    assert set(pks) == CHINOOK_TABLES
    assert pks["album"] == ("album_id",)
    assert pks["playlist_track"] == ("playlist_id", "track_id")


def test_every_declared_foreign_key_is_found(any_crawl):
    fks = {
        (c.table, c.columns[0])
        for c in any_crawl.constraints
        if c.kind == "FOREIGN KEY"
    }
    assert fks == set(CHINOOK_FOREIGN_KEYS)


def test_foreign_key_targets_resolve_to_table_and_column(any_crawl):
    """P6 adopted, live: the OKF table format needs
    ``FOREIGN KEY -> CORE.album.album_id``, and the crawl can now produce
    every part of it."""
    schema = _schema(any_crawl)
    resolved = {
        (c.table, c.columns[0]): (c.referenced_table, c.referenced_columns)
        for c in any_crawl.constraints
        if c.kind == "FOREIGN KEY"
    }
    assert resolved == {
        source: (f"{schema}.{table}", (column,))
        for source, (table, column) in CHINOOK_FOREIGN_KEYS.items()
    }


def test_the_self_referencing_foreign_key_resolves(any_crawl):
    """employee.reports_to points at employee: the resolver has to handle a
    constraint whose target is its own table."""
    schema = _schema(any_crawl)
    fk = next(
        c
        for c in any_crawl.constraints
        if c.kind == "FOREIGN KEY" and c.table == "employee"
    )
    assert fk.referenced_table == f"{schema}.employee"
    assert fk.referenced_columns == ("employee_id",)


def test_every_constraint_is_named(any_crawl):
    """The other half of P6: constraint_name is selected, so a constraint is
    identified rather than inferred from its shape."""
    assert all(c.name for c in any_crawl.constraints)
    assert {c.name for c in any_crawl.constraints if c.kind == "PRIMARY KEY"} == {
        f"pk_{table}" for table in CHINOOK_TABLES
    }


def test_reconciliation_passes_against_the_expected_count(any_crawl):
    reconciliation = any_crawl.reconciliation
    assert reconciliation.cataloged_tables == 11
    assert reconciliation.expected_tables == 11
    assert reconciliation.status == "COMPLETE"
    assert any_crawl.completeness == "COMPLETE"


def test_the_crawl_only_issued_catalog_sql(any_crawl):
    for query in any_crawl.queries:
        if query.status == "skipped":
            continue
        # by key, not query id: SQL Server answers A6 with two blocks
        statement = catalog.statement(any_crawl.engine, query.key)
        assert query.sql == statement.sql


def test_indexes_are_read_on_both_engines(any_crawl):
    """P1 adopted: PostgreSQL has index evidence now, and both engines report
    the same 21 indexes — 11 primary keys and Chinook's 10 foreign key
    indexes."""
    by_name = {i.name: i for i in any_crawl.indexes}
    assert CHINOOK_SECONDARY_INDEXES <= set(by_name)
    assert len(any_crawl.indexes) == 21

    assert by_name["ifk_track_album_id"].columns == ("album_id",)
    assert by_name["ifk_track_album_id"].unique is False
    assert by_name["pk_playlist_track"].columns == ("playlist_id", "track_id")
    assert by_name["pk_playlist_track"].unique is True
    # The Primary Index is a Teradata concept and must not appear elsewhere.
    assert not any(i.primary_index for i in any_crawl.indexes)


def test_system_schemas_are_excluded_and_recorded(any_crawl):
    """The catalog's schema-scope policy, live. The crawl configs no longer
    name a schema at all: the 11 tables come from excluding the engine's own
    schemas and keeping everything else."""
    schema = _schema(any_crawl)
    assert {t.schema for t in any_crawl.tables} == {schema}
    assert any_crawl.scope.config_schemas == ()
    assert not any_crawl.scope.config_filtered


def test_the_allowlist_holds_this_crawls_identifiers(any_crawl):
    allowlist = AllowList.from_obj(any_crawl.allowlist)
    schema = _schema(any_crawl)
    assert len(allowlist) == 12  # 11 base tables + the 6b rig view
    assert allowlist.qualify(schema, "invoice_line") == f"{schema}.invoice_line"
    assert allowlist.column_names(schema, "album") == (
        "album_id", "title", "artist_id",
    )
    assert allowlist.rejected == []


def test_the_allowlist_rejects_what_the_crawl_did_not_see(any_crawl):
    """The rejection path, against a real dictionary: a table that exists in
    the engine but not in this crawl's inventory is still refused."""
    allowlist = AllowList.from_obj(any_crawl.allowlist)
    schema = _schema(any_crawl)
    with pytest.raises(AllowListError):
        allowlist.table("information_schema", "tables")
    with pytest.raises(AllowListError):
        allowlist.table(schema, "sysobjects")
    with pytest.raises(AllowListError):
        allowlist.column(schema, "album", "album_id; DROP TABLE artist")


# -- engine-specific facts --------------------------------------------------


def test_postgres_reports_its_own_type_spellings(postgres_crawl):
    by_name = {(c.table, c.name): c for c in postgres_crawl.columns}
    title = by_name[("album", "title")]
    assert title.type == "VARCHAR(160)"
    assert title.raw_type == "character varying"
    assert by_name[("invoice", "invoice_date")].raw_type == (
        "timestamp without time zone"
    )


def test_postgres_runs_every_tier_a_query(postgres_crawl):
    """Nothing is skipped any more: P1 and P2 closed the last two holes."""
    assert [q.key for q in postgres_crawl.queries] == [
        "A1", "A2", "A3", "A4", "A7", "A7-routines", "A8", "A6", "A5",
    ]
    assert all(q.status == "ok" for q in postgres_crawl.queries)


def test_postgres_reads_a_row_estimate_for_every_table(postgres_crawl):
    """P9 adopted, live: every table reports a reltuples-based estimate. The
    *values* are whatever the planner believes when the crawl runs — index
    builds and autovacuum both move them — which is exactly why they are
    recorded as estimates and why the measuring pass recounts (B1) instead
    of believing them. The measured acceptance asserts the real counts."""
    stats = {s.table: s for s in postgres_crawl.table_stats}
    assert set(stats) == CHINOOK_TABLES
    assert all(s.row_count is not None for s in stats.values())
    assert all(s.source == "stats-estimate" for s in stats.values())
    assert all(s.estimated for s in stats.values())


def test_postgres_column_statistics_are_marked_approximate(postgres_crawl):
    """pg_stats holds a row per *analyzed* column, and autovacuum decides
    when that happens — so coverage here is timing, not truth, and nothing
    asserts it. What is truth: everything read this way is an estimate, so
    B2 still runs and the measured numbers never come from here."""
    assert all(s.approximate for s in postgres_crawl.column_stats)
    a6 = next(q for q in postgres_crawl.queries if q.key == "A6")
    assert a6.status == "ok" and a6.rows >= 11


def test_postgres_sees_more_tables_than_it_catalogs(postgres_crawl):
    """A5 counts every base table the account can see, pg_catalog included.
    Reconciliation adds the policy-excluded tables back before comparing."""
    reconciliation = postgres_crawl.reconciliation
    assert reconciliation.visible_tables >= 11
    assert set(postgres_crawl.scope.system_schemas) == {
        "pg_catalog",
        "information_schema",
    }
    assert postgres_crawl.scope.system_tables > 0
    assert (
        reconciliation.cataloged_tables + postgres_crawl.scope.system_tables
        == reconciliation.visible_tables
    )


def test_sqlserver_reports_its_own_type_spellings(sqlserver_crawl):
    by_name = {(c.table, c.name): c for c in sqlserver_crawl.columns}
    title = by_name[("album", "title")]
    assert title.type == "NVARCHAR(160)"
    assert title.raw_type == "nvarchar"
    assert by_name[("invoice", "invoice_date")].raw_type == "datetime"


def test_sqlserver_runs_both_a6_blocks(sqlserver_crawl):
    """P3 adopted, live: A6 is two statements on this engine."""
    assert [q.key for q in sqlserver_crawl.queries] == [
        "A1", "A2", "A3", "A4", "A7", "A8", "A8-synonyms",
        "A6", "A6-columns", "A5",
    ]
    assert all(q.status == "ok" for q in sqlserver_crawl.queries)


def test_sqlserver_reads_dictionary_row_counts(sqlserver_crawl):
    stats = {s.table: s for s in sqlserver_crawl.table_stats}
    assert set(stats) == CHINOOK_TABLES
    assert all(s.source == "stats" for s in stats.values())
    # dm_db_partition_stats counts rows rather than estimating them, so
    # unlike PostgreSQL's estimates these ARE the Chinook row counts.
    assert all(not s.estimated for s in stats.values())
    assert {t: s.row_count for t, s in stats.items()} == CHINOOK_ROW_COUNTS


def test_sqlserver_column_statistics_are_marked_approximate(sqlserver_crawl):
    """Histogram sums are estimates whatever they hold, and B2 must still
    run near a gate boundary — which on this rig means everywhere, since
    approximate statistics never satisfy the stats-first check."""
    assert all(s.approximate for s in sqlserver_crawl.column_stats)


def test_sqlserver_excludes_its_own_system_schemas(sqlserver_crawl):
    assert "INFORMATION_SCHEMA" not in {t.schema for t in sqlserver_crawl.tables}
    assert "sys" not in {t.schema for t in sqlserver_crawl.tables}


# -- session 6b: A7/A8 against the rig's own code objects -------------------


def test_the_rig_view_is_cataloged_as_a_view(any_crawl):
    views = {t.name for t in any_crawl.tables if not t.is_base_table}
    assert views == {"album_artist_names"}
    assert len(any_crawl.base_tables) == 11  # the view moved no goalposts


def test_the_views_join_is_mined_into_one_intent_fact(any_crawl):
    schema = _schema(any_crawl)
    facts = {
        (f.qualified, f.other_qualified, f.source)
        for f in any_crawl.join_intents
    }
    assert facts == {
        (
            f"{schema}.album.artist_id",
            f"{schema}.artist.artist_id",
            f"{schema}.album_artist_names",
        )
    }


def test_the_dynamic_object_is_counted_as_unparsed(any_crawl):
    by_name = {o.name: o for o in any_crawl.code_objects}
    assert by_name["album_artist_names"].status == "parsed"
    assert by_name["album_artist_names"].kind == "VIEW"
    assert by_name["rig_dynamic_count"].status == "unparsed"
    assert by_name["rig_dynamic_count"].reason == "dynamic-sql"
    assert any_crawl.unparsed_code_objects == 1


def test_a7_and_a8_ran_and_the_rig_has_no_lineage(any_crawl):
    ran = {q.query_id for q in any_crawl.queries if q.status == "ok"}
    assert {"A7", "A8"} <= ran
    assert any_crawl.external_references == []
