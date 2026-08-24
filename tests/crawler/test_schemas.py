"""The schema-scope policy: system schemas are never crawled.

The catalog states it as a rule for adapters — "ALWAYS exclude engine system
schemas, without config" — so it is enforced in one place for every engine
and every query rather than left to each catalog block's WHERE clause. Two of
the adopted PostgreSQL blocks do carry the predicate in SQL; the rest do not,
and A1 could not, since a crawl has to see the system schemas to report that
it skipped them.
"""

from __future__ import annotations

import pytest

from crawler import ENGINES
from crawler.schemas import SYSTEM_SCHEMAS, describe, is_system_schema


@pytest.mark.parametrize(
    ("engine", "schema"),
    [
        ("postgres", "pg_catalog"),
        ("postgres", "information_schema"),
        ("sqlserver", "sys"),
        ("sqlserver", "INFORMATION_SCHEMA"),
        ("teradata", "DBC"),
        ("teradata", "SysAdmin"),
        ("teradata", "SystemFe"),
        ("oracle", "SYS"),
        ("oracle", "SYSTEM"),
        ("oracle", "SYSAUX"),
        ("db2", "SYSCAT"),
        ("db2", "SYSIBM"),
        ("db2", "SYSSTAT"),
    ],
)
def test_engine_internals_are_system_schemas(engine, schema):
    assert is_system_schema(engine, schema)


@pytest.mark.parametrize(
    ("engine", "schema"),
    [
        ("postgres", "public"),
        ("postgres", "sales"),
        ("sqlserver", "dbo"),
        ("teradata", "FINANCE"),
        ("oracle", "FIN_OWNER"),
        ("db2", "CORE"),
    ],
)
def test_business_schemas_are_not(engine, schema):
    assert not is_system_schema(engine, schema)


def test_matching_ignores_case_and_padding():
    """Dictionaries disagree about identifier case, and DBC pads its CHAR
    columns; a policy that missed 'PG_CATALOG' would not be a policy."""
    assert is_system_schema("postgres", "PG_CATALOG")
    assert is_system_schema("teradata", "dbc")
    assert is_system_schema("teradata", "  DBC  ")


def test_the_teradata_prefix_rule_is_taken_literally():
    """The catalog's rule is ``DBC/Sys*`` and Teradata identifiers are
    case-insensitive, so the prefix matches SysAdmin, SysUDTLib, SYSLIB —
    and also a business database called SYSTEM_OF_RECORD_X, which is an
    over-match the crawl makes visible rather than hides: the schema is
    named in the scope report and in a warning on every affected bundle.

    Narrowing the rule is a catalog decision, not an adapter one.
    """
    assert is_system_schema("teradata", "SysAdmin")
    assert is_system_schema("teradata", "SysUDTLib")
    assert is_system_schema("teradata", "SYSLIB")
    assert is_system_schema("teradata", "SYSTEM_OF_RECORD_X")
    assert not is_system_schema("teradata", "SALES")
    assert not is_system_schema("teradata", "S_ACCOUNT")


def test_an_unknown_engine_excludes_nothing():
    """Better to crawl a system schema and have it visible in the bundle than
    to silently drop a business schema on an engine we do not know."""
    assert not is_system_schema("informix", "sysmaster")
    assert describe("informix") == ""


@pytest.mark.parametrize("engine", ENGINES)
def test_every_supported_engine_has_a_policy(engine):
    assert engine in SYSTEM_SCHEMAS
    assert describe(engine)


def test_the_policy_reads_back_for_the_audit_note():
    assert describe("postgres") == "pg_catalog/information_schema"
    assert describe("teradata") == "DBC/Sys*"
    assert describe("oracle") == "SYS/SYSTEM/*AUX"
