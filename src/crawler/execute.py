"""Running catalog statements against a live connection.

Thin on purpose. The interesting parts of this crawler are which SQL exists
(:mod:`crawler.catalog`) and what the rows mean (:mod:`crawler.adapters`);
this module just hands a statement to a DB-API cursor and returns tuples.

The one rule it enforces: a parameterised statement never reaches a cursor
from here. Tier B and C templates have to be bound through the allow-list
first, and that binder does not exist yet — so an attempt to run one is a
crash, not a string format.
"""

from __future__ import annotations

from typing import Any, Protocol

from .catalog import Statement
from .errors import QueryError


class Cursor(Protocol):
    def execute(self, sql: str) -> Any: ...
    def fetchall(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


def run_statement(connection: Connection, statement: Statement) -> list[tuple]:
    """Execute one catalog statement and return its rows as tuples."""
    if statement.is_parameterised:
        raise QueryError(
            "refusing to execute a parameterised template directly; "
            "identifiers must be bound through the crawl's allow-list",
            query_id=statement.query_id,
            engine=statement.variant,
        )
    cursor = connection.cursor()
    try:
        cursor.execute(statement.sql)
        rows = cursor.fetchall() or []
        return [tuple(row) for row in rows]
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()
