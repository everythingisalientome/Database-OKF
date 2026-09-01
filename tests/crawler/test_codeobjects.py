"""The conservative extractor (session 6b).

One rule under test from every angle: an object is either read exactly —
plain FROM/JOIN references, equality predicates in ON/WHERE, aliases
resolved, every identifier verified against the crawl's own inventory — or
it is counted as unparsed and contributes nothing. There is no third
outcome, because the third outcome would be a guess wearing an [observed]
tag.
"""

from __future__ import annotations

import pytest

from crawler.codeobjects import mine
from crawler.results import CodeObject, Column, Table

CHINOOK_TABLES = [
    Table("public", "album", "BASE TABLE"),
    Table("public", "artist", "BASE TABLE"),
    Table("public", "employee", "BASE TABLE"),
    Table("public", "album_titles", "VIEW"),
]

CHINOOK_COLUMNS = [
    Column("public", "album", "album_id", 1, "INT", "integer", False),
    Column("public", "album", "title", 2, "VARCHAR(160)", "varchar", False),
    Column("public", "album", "artist_id", 3, "INT", "integer", False),
    Column("public", "artist", "artist_id", 1, "INT", "integer", False),
    Column("public", "artist", "name", 2, "VARCHAR(120)", "varchar", True),
    Column("public", "employee", "employee_id", 1, "INT", "integer", False),
    Column("public", "employee", "reports_to", 2, "INT", "integer", True),
    Column("public", "album_titles", "title", 1, "VARCHAR(160)", "varchar", True),
]


def view(definition, *, schema="public", name="v", kind="VIEW", **kwargs):
    return CodeObject(schema=schema, name=name, kind=kind,
                      definition=definition, **kwargs)


def mined(*objects, tables=None, columns=None):
    return mine(
        list(objects),
        tables if tables is not None else CHINOOK_TABLES,
        columns if columns is not None else CHINOOK_COLUMNS,
    )


def the_fact(result):
    assert len(result.join_intents) == 1, [
        f.to_obj() for f in result.join_intents
    ]
    return result.join_intents[0]


# -- what parses ------------------------------------------------------------


def test_an_on_join_with_aliases_yields_the_fact():
    result = mined(view(
        "SELECT al.title, ar.name FROM album al "
        "JOIN artist ar ON al.artist_id = ar.artist_id;"
    ))
    fact = the_fact(result)
    assert (fact.qualified, fact.other_qualified) == (
        "public.album.artist_id", "public.artist.artist_id",
    )
    assert fact.source == "public.v"
    assert result.objects[0].status == "parsed"
    assert result.unparsed == 0


def test_postgres_view_rendering_parses():
    """pg_get_viewdef wraps join trees in parens and predicates in double
    parens; the rig's live view arrives exactly like this."""
    result = mined(view(
        " SELECT al.title,\n    ar.name\n"
        "   FROM (album al\n"
        "     JOIN artist ar ON ((al.artist_id = ar.artist_id)));"
    ))
    assert the_fact(result).other_qualified == "public.artist.artist_id"


def test_a_where_style_comma_join_yields_the_fact():
    """Legacy estates join in WHERE; that is half the point of A7."""
    result = mined(view(
        "SELECT a.title FROM album a, artist b "
        "WHERE a.artist_id = b.artist_id"
    ))
    assert the_fact(result).qualified == "public.album.artist_id"


def test_schema_qualified_and_bare_references_resolve_alike():
    result = mined(view(
        "SELECT a.title FROM public.album a JOIN artist "
        "ON a.artist_id = artist.artist_id"
    ))
    assert the_fact(result).other_qualified == "public.artist.artist_id"


def test_a_fully_qualified_predicate_side_resolves_without_an_alias():
    result = mined(view(
        "SELECT title FROM album, artist "
        "WHERE public.album.artist_id = public.artist.artist_id"
    ))
    assert the_fact(result).qualified == "public.album.artist_id"


def test_resolution_is_case_insensitive_but_records_observed_spelling():
    result = mined(view(
        "SELECT AL.TITLE FROM ALBUM AL JOIN ARTIST AR "
        "ON AL.ARTIST_ID = AR.ARTIST_ID"
    ))
    assert the_fact(result).qualified == "public.album.artist_id"


def test_a_self_join_is_a_fact():
    result = mined(view(
        "SELECT e.employee_id FROM employee e JOIN employee m "
        "ON e.reports_to = m.employee_id"
    ))
    fact = the_fact(result)
    assert fact.qualified == "public.employee.employee_id"
    assert fact.other_qualified == "public.employee.reports_to"


def test_a_tautology_is_not_a_join():
    result = mined(view(
        "SELECT a.title FROM album a WHERE a.album_id = a.album_id"
    ))
    assert result.join_intents == []
    assert result.objects[0].status == "parsed"


def test_the_same_predicate_from_one_source_deduplicates():
    result = mined(view(
        "SELECT a.title FROM album a JOIN artist b "
        "ON a.artist_id = b.artist_id AND b.artist_id = a.artist_id"
    ))
    assert len(result.join_intents) == 1


def test_two_sources_declaring_one_join_are_two_facts():
    """The line names its source, so each declaring object is evidence."""
    result = mined(
        view("SELECT a.title FROM album a JOIN artist b "
             "ON a.artist_id = b.artist_id", name="v1"),
        view("SELECT b.name FROM artist b JOIN album a "
             "ON b.artist_id = a.artist_id", name="v2"),
    )
    assert [f.source for f in result.join_intents] == ["public.v1", "public.v2"]


def test_strings_and_comments_are_not_sql():
    result = mined(view(
        "SELECT a.title -- x.y = z.w\n"
        "FROM album a /* JOIN ghost g ON g.a = g.b */ "
        "WHERE a.title = 'artist.artist_id = album.artist_id'"
    ))
    assert result.join_intents == []
    assert result.objects[0].status == "parsed"


def test_a_literal_comparison_is_not_a_fact():
    result = mined(view(
        "SELECT a.title FROM album a WHERE a.album_id = 42"
    ))
    assert result.join_intents == []
    assert result.objects[0].status == "parsed"


# -- external references ----------------------------------------------------


def test_a_multi_part_name_is_recorded_lineage_and_never_a_join():
    result = mined(view(
        "SELECT a.title FROM album a "
        "JOIN otherdb.dbo.account x ON a.album_id = x.album_id"
    ))
    assert result.join_intents == []  # cross-database joins: never
    assert result.objects[0].status == "parsed"
    [reference] = result.external_references
    assert reference.kind == "multi-part-name"
    assert reference.target == "otherdb.dbo.account"
    assert reference.source == "public.v"


def test_a_four_part_predicate_is_not_misread_as_three():
    """srv.db.schema.table.col on one side must not resolve its tail as a
    local schema.table.column. It matches nothing — the lineage is still
    recorded from the FROM clause, and no fact is invented."""
    result = mined(
        view(
            "SELECT a.title FROM album a JOIN srv.otherdb.public.album x "
            "ON srv.otherdb.public.album.album_id = a.album_id"
        )
    )
    assert result.join_intents == []
    assert result.objects[0].status == "parsed"
    assert [r.target for r in result.external_references] == [
        "srv.otherdb.public.album"
    ]


# -- what refuses, and how it is counted ------------------------------------


@pytest.mark.parametrize(
    ("obj", "reason"),
    [
        (view(None), "no-definition"),
        (view("   "), "no-definition"),
        (view("SELECT 1 FROM album", truncated=True), "truncated"),
        (view("BEGIN EXECUTE 'SELECT * FROM ' || tbl; END",
              kind="FUNCTION"), "dynamic-sql"),
        (view("CREATE PROCEDURE p AS EXEC(@sql);",
              kind="PROCEDURE"), "dynamic-sql"),
        (view("SELECT a.x FROM (SELECT 1 AS x) a"), "nested-select"),
        (view("WITH t AS (SELECT 1) SELECT * FROM t"), "nested-select"),
        (view("SELECT title FROM album UNION SELECT name FROM artist"),
         "multiple-selects"),
        (view("SELECT g.x FROM generate_series(1, 10) g"), "unsupported-from"),
        (view("SELECT a.title FROM albums a"), "unresolved-identifier: albums"),
        (view("SELECT a.ghost FROM album a JOIN artist b "
              "ON a.ghost = b.artist_id"), "unresolved-identifier: a.ghost"),
        (view("SELECT a.title FROM album a JOIN artist a "
              "ON a.artist_id = a.artist_id"), "ambiguous-alias: a"),
    ],
)
def test_refused_objects_are_counted_with_their_reason(obj, reason):
    result = mined(obj)
    [record] = result.objects
    assert record.status == "unparsed"
    assert record.reason == reason
    assert result.unparsed == 1
    assert result.join_intents == []
    assert any("counted as unparsed" in w for w in result.warnings)


def test_a_refused_object_contributes_nothing_at_all():
    """Even the join it does contain: partial trust is how guesses leak."""
    result = mined(view(
        "SELECT a.title FROM album a JOIN artist b "
        "ON a.artist_id = b.artist_id; "
        "EXECUTE 'anything'"
    ))
    assert result.join_intents == []
    assert result.objects[0].reason == "dynamic-sql"


def test_one_bad_object_does_not_poison_a_good_one():
    result = mined(
        view("SELECT a.title FROM album a JOIN artist b "
             "ON a.artist_id = b.artist_id", name="good"),
        view("BEGIN EXECUTE x; END", name="bad", kind="PROCEDURE"),
    )
    assert len(result.join_intents) == 1
    assert result.unparsed == 1
    statuses = {o.name: o.status for o in result.objects}
    assert statuses == {"good": "parsed", "bad": "unparsed"}


def test_an_unqualified_name_that_is_ambiguous_resolves_nothing():
    tables = CHINOOK_TABLES + [Table("archive", "artist", "BASE TABLE")]
    columns = CHINOOK_COLUMNS + [
        Column("archive", "artist", "artist_id", 1, "INT", "integer", False),
    ]
    result = mined(
        view("SELECT a.title FROM album a JOIN artist b "
             "ON a.artist_id = b.artist_id", schema="elsewhere"),
        tables=tables,
        columns=columns,
    )
    assert result.objects[0].status == "unparsed"
    assert result.objects[0].reason == "unresolved-identifier: artist"


def test_the_objects_own_schema_wins_an_ambiguous_name():
    tables = CHINOOK_TABLES + [Table("archive", "artist", "BASE TABLE")]
    columns = CHINOOK_COLUMNS + [
        Column("archive", "artist", "artist_id", 1, "INT", "integer", False),
    ]
    result = mined(
        view("SELECT b.artist_id FROM artist b JOIN public.album a "
             "ON b.artist_id = a.artist_id", schema="archive"),
        tables=tables,
        columns=columns,
    )
    fact = the_fact(result)
    assert fact.qualified == "archive.artist.artist_id"
    assert fact.other_qualified == "public.album.artist_id"


def test_facts_and_records_survive_the_crawl_json_round_trip():
    from crawler.results import CodeObject as CO
    from crawler.results import ExternalReference, JoinIntent

    for item in (
        CO(schema="s", name="n", kind="VIEW", definition="SELECT 1",
           truncated=True, status="unparsed", reason="truncated"),
        JoinIntent(schema="s", table="t", column="c", other_schema="s2",
                   other_table="t2", other_column="c2", source="s.v"),
        ExternalReference(target="srv", kind="linked-server",
                          source="sys.servers", detail="tcp:host"),
    ):
        assert type(item).from_obj(item.to_obj()) == item
