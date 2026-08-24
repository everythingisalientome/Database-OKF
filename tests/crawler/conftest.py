"""Shared fixtures for the crawler tests.

The canned rows are a small slice of the Chinook schema in the shape each
engine's dictionary returns it — positionally, in the SELECT order of the
catalog block, padding and all. They include rows the crawl is expected to
drop (a system schema, an archive schema), because dropping the right rows is
half of what the crawl does.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from fakes import FakeConnection, responses_for

CATALOG_PATH = Path(__file__).resolve().parents[2] / "catalog" / "step1-query-catalog.md"

STATS_DATE = date(2026, 8, 20)


@pytest.fixture
def catalog_text() -> str:
    return CATALOG_PATH.read_text(encoding="utf-8")


# -- PostgreSQL -------------------------------------------------------------

PG_A1_ROWS = [
    ("chinook", "public", "album", "BASE TABLE"),
    ("chinook", "public", "artist", "BASE TABLE"),
    ("chinook", "public", "album_titles", "VIEW"),
    ("chinook", "pg_catalog", "pg_class", "BASE TABLE"),
]

PG_A2_ROWS = [
    ("public", "album", "album_id", 1, "integer", None, 32, 0, "NO", None),
    ("public", "album", "title", 2, "character varying", 160, None, None, "NO", None),
    ("public", "album", "artist_id", 3, "integer", None, 32, 0, "NO", None),
    ("public", "artist", "artist_id", 1, "integer", None, 32, 0, "NO", None),
    ("public", "artist", "name", 2, "character varying", 120, None, None, "YES", None),
    ("public", "album_titles", "title", 1, "character varying", 160, None, None,
     "YES", None),
    ("pg_catalog", "pg_class", "oid", 1, "oid", None, None, None, "NO", None),
]

PG_A3_ROWS = [
    ("PRIMARY KEY", "public", "album", "pk_album", "album_id", 1,
     None, None, None, None),
    ("PRIMARY KEY", "public", "artist", "pk_artist", "artist_id", 1,
     None, None, None, None),
    ("FOREIGN KEY", "public", "album", "fk_album_artist", "artist_id", 1,
     "public", "pk_artist", "public", "artist"),
]

#: (schema, table, index_name, is_unique, column_name, key_ordinal, is_included)
PG_A4_ROWS = [
    ("public", "album", "pk_album", True, "album_id", 1, False),
    ("public", "album", "ifk_album_artist_id", False, "artist_id", 1, False),
    ("public", "artist", "pk_artist", True, "artist_id", 1, False),
]

#: (schemaname, tablename, attname, n_distinct, null_frac, est_rows, stats_date)
PG_A6_ROWS = [
    ("public", "album", "album_id", -1.0, 0.0, 347, STATS_DATE),
    ("public", "album", "title", -0.9, 0.0, 347, STATS_DATE),
    ("public", "artist", "artist_id", 275.0, 0.0, 275, STATS_DATE),
]

PG_A5_ROWS = [(11,)]


@pytest.fixture
def pg_a1_rows() -> list:
    return list(PG_A1_ROWS)


@pytest.fixture
def pg_a2_rows() -> list:
    return list(PG_A2_ROWS)


@pytest.fixture
def pg_a3_rows() -> list:
    return list(PG_A3_ROWS)


@pytest.fixture
def pg_a6_rows() -> list:
    return list(PG_A6_ROWS)


@pytest.fixture
def pg_rows() -> dict:
    return {
        "A1": list(PG_A1_ROWS),
        "A2": list(PG_A2_ROWS),
        "A3": list(PG_A3_ROWS),
        "A4": list(PG_A4_ROWS),
        "A6": list(PG_A6_ROWS),
        "A5": list(PG_A5_ROWS),
    }


@pytest.fixture
def pg_connection(pg_rows) -> FakeConnection:
    return FakeConnection(responses_for("postgres", pg_rows))


# -- SQL Server -------------------------------------------------------------

MSSQL_ROWS = {
    "A1": [
        ("chinook", "dbo", "album", "BASE TABLE"),
        ("chinook", "INFORMATION_SCHEMA", "tables", "VIEW"),
    ],
    "A2": [("dbo", "album", "album_id", 1, "int", None, 10, 0, "NO", None)],
    "A3": [
        ("PRIMARY KEY", "dbo", "album", "pk_album", "album_id", 1,
         None, None, None, None),
    ],
    "A4": [("dbo", "album", "pk_album", True, "album_id", 1)],
    "A6": [("dbo", "album", 347)],
    "A6-columns": [("dbo", "album", "album_id", 347, 347, STATS_DATE, 347)],
    "A5": [(1,)],
}


@pytest.fixture
def mssql_rows() -> dict:
    return {key: list(rows) for key, rows in MSSQL_ROWS.items()}


# -- Teradata ---------------------------------------------------------------

TERADATA_ROWS = {
    "A1": [
        ("FINANCE   ", "ACCOUNT   ", "T"),
        ("DBC       ", "TablesV   ", "V"),
    ],
    "A2": [("FINANCE", "ACCOUNT", "ACCT_ID", 1, "I", 4, None, None, "N", None)],
    "A3": [("FINANCE", "ACCOUNT", "PK_ACCOUNT", 1, "K", "ACCT_ID", 1)],
    "A4": [("FINANCE", "ACCOUNT", None, 1, "P", "N", "ACCT_ID", 1)],
    "A6": [("FINANCE", "ACCOUNT", "ACCT_ID", 4811002, 4811002, 0, STATS_DATE)],
    "A5": [(1,)],
}


@pytest.fixture
def teradata_rows() -> dict:
    return {key: list(rows) for key, rows in TERADATA_ROWS.items()}
