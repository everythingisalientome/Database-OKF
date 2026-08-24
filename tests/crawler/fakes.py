"""Fakes for the crawler tests.

The fake connection is deliberately strict: it answers only SQL it was given
in advance and raises on anything else. A crawler that composed SQL — even
slightly, even by adding a WHERE clause — fails every test that uses it,
which is the cheapest possible guard on the no-dynamic-SQL rule.
"""

from __future__ import annotations

from crawler import catalog


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows: list[tuple] = []
        self.closed = False

    def execute(self, sql):
        self.connection.executed.append(sql)
        if sql not in self.connection.responses:
            raise AssertionError(
                "the crawler issued SQL the test did not register:\n" + sql
            )
        answer = self.connection.responses[sql]
        if isinstance(answer, Exception):
            raise answer
        self.rows = list(answer)

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class FakeConnection:
    """Replays canned rows for exact statement text and nothing else."""

    def __init__(self, responses: dict):
        self.responses = dict(responses)
        self.executed: list[str] = []
        self.cursors: list[FakeCursor] = []

    def cursor(self):
        cursor = FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    def close(self):
        pass


def responses_for(engine: str, rows_by_query: dict) -> dict:
    """Map ``{"A1": rows}`` onto the SQL text that engine runs for A1."""
    responses = {}
    for query_id, rows in rows_by_query.items():
        statement = catalog.statement(engine, query_id)
        assert statement is not None, f"{engine} has no catalog block for {query_id}"
        responses[statement.sql] = rows
    return responses
