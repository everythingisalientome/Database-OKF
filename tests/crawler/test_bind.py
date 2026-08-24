"""The allow-list is the no-dynamic-SQL rule; :mod:`crawler.bind` is the only
door through it.

Every test here is a variation on one question: can an identifier that the
crawl did not observe reach a SQL string? The answer has to be no by
construction, not by convention, because Tier B and C are templates and a
template is one careless format call away from being dynamic SQL.
"""

from __future__ import annotations

import pytest

from crawler import AllowList, AllowListError, Column, Table, catalog
from crawler.bind import PLACEHOLDER, batches, bind

TABLES = [
    Table("CORE", "album", "BASE TABLE"),
    Table("CORE", "artist", "BASE TABLE"),
]
COLUMNS = [
    Column("CORE", "album", "album_id", 1, "INT", "integer", False),
    Column("CORE", "album", "title", 2, "VARCHAR(160)", "character varying", False),
    Column("CORE", "album", "artist_id", 3, "INT", "integer", False),
    Column("CORE", "artist", "artist_id", 1, "INT", "integer", False),
]


@pytest.fixture
def allowlist() -> AllowList:
    return AllowList.from_inventory(TABLES, COLUMNS)


def template(key: str, engine: str = "postgres"):
    return catalog.template(engine, key)


# -- what binding produces --------------------------------------------------


def test_a_per_table_template_binds_schema_and_table(allowlist):
    sql = bind(template("B1"), allowlist, schema="CORE", table="album")
    assert sql == "SELECT COUNT(*) AS row_count FROM CORE.album;"


def test_a_per_column_template_binds_every_occurrence(allowlist):
    sql = bind(template("B3"), allowlist, schema="CORE", table="album", column="title")
    assert sql.count("title") == 3  # the SELECT list, the WHERE, the GROUP BY
    assert "{column}" not in sql


def test_a_batched_template_renders_one_fragment_per_column(allowlist):
    sql = bind(
        template("B2"),
        allowlist,
        schema="CORE",
        table="album",
        columns=["album_id", "title", "artist_id"],
    )
    assert sql.startswith("SELECT COUNT(*),")
    assert sql.count("COUNT(DISTINCT ") == 3
    assert sql.endswith("FROM CORE.album;")


def test_nothing_leaves_a_placeholder_behind(allowlist):
    for key in catalog.TIER_BC:
        item = template(key)
        if item is None:
            continue
        kwargs = (
            {"columns": ["album_id"]} if item.is_batched else {"column": "album_id"}
        )
        sql = bind(item, allowlist, schema="CORE", table="album", **kwargs)
        assert not PLACEHOLDER.findall(sql), key


def test_the_observed_spelling_is_what_reaches_the_sql(allowlist):
    """A caller's casing is not the dictionary's, and the SQL gets the
    dictionary's — the allow-list resolves it rather than trusting either."""
    sql = bind(template("B1"), allowlist, schema="core", table="ALBUM")
    assert "CORE.album" in sql


# -- what binding refuses ---------------------------------------------------


@pytest.mark.parametrize(
    "schema,table",
    [
        ("CORE", "employee"),  # a table this crawl never saw
        ("SALES", "album"),  # right table, wrong schema
        ("CORE", "album; DROP TABLE artist --"),  # the obvious one
    ],
)
def test_an_unobserved_table_cannot_be_bound(allowlist, schema, table):
    with pytest.raises(AllowListError):
        bind(template("B1"), allowlist, schema=schema, table=table)


@pytest.mark.parametrize(
    "column",
    ["composer", "title) FROM CORE.artist --", "1; DELETE FROM CORE.album"],
)
def test_an_unobserved_column_cannot_be_bound(allowlist, column):
    with pytest.raises(AllowListError):
        bind(template("B3"), allowlist, schema="CORE", table="album", column=column)


def test_one_bad_column_fails_the_whole_batch(allowlist):
    """Not a partial statement over the columns that happened to check out:
    the batch is positional, so dropping one silently would attribute every
    later column's numbers to its neighbour."""
    with pytest.raises(AllowListError):
        bind(
            template("B2"),
            allowlist,
            schema="CORE",
            table="album",
            columns=["album_id", "nope"],
        )


def test_a_batched_template_refuses_a_single_column_call(allowlist):
    with pytest.raises(AllowListError):
        bind(template("B2"), allowlist, schema="CORE", table="album", column="title")


def test_a_per_column_template_refuses_a_batch_call(allowlist):
    with pytest.raises(AllowListError):
        bind(
            template("B3"), allowlist, schema="CORE", table="album", columns=["title"]
        )


def test_a_batch_of_no_columns_is_refused(allowlist):
    with pytest.raises(AllowListError):
        bind(template("B2"), allowlist, schema="CORE", table="album", columns=[])


def test_column_and_columns_together_are_refused(allowlist):
    with pytest.raises(AllowListError):
        bind(
            template("B3"),
            allowlist,
            schema="CORE",
            table="album",
            column="title",
            columns=["title"],
        )


def test_an_empty_allowlist_binds_nothing():
    """The state a crawl is in when A1 came back empty: nothing observed,
    so nothing may be profiled."""
    with pytest.raises(AllowListError):
        bind(template("B1"), AllowList(), schema="CORE", table="album")


# -- batching ---------------------------------------------------------------


def test_batches_preserve_order_and_size():
    assert batches(list("abcde"), 2) == [["a", "b"], ["c", "d"], ["e"]]


def test_one_batch_when_everything_fits():
    assert batches(list("abc"), 20) == [["a", "b", "c"]]


def test_no_columns_is_no_batches():
    assert batches([], 10) == []


def test_a_zero_batch_size_is_refused():
    with pytest.raises(ValueError):
        batches(["a"], 0)
