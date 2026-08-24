"""Filling a Tier B/C template — the one place identifiers reach SQL.

Tier A runs verbatim and takes no parameters. Tier B and C are templates with
``{schema}``, ``{table}`` and ``{column}`` holes, and specs/00 decision 1 says
nothing may fill a hole unless the crawl's own A1/A2 output produced it.
:mod:`crawler.allowlist` is that rule as data; this module is that rule as the
only code path that formats a template.

It is deliberately narrow:

* Identifiers come from :class:`~crawler.allowlist.AllowList` lookups, which
  raise rather than return for anything unobserved, and which hand back the
  *observed* spelling — so what reaches the SQL is the dictionary's own text,
  not the caller's.
* The rendered statement is checked for leftover placeholders afterwards. A
  template that grew a hole nobody bound is a crash here, not a syntax error
  three layers down at a live database.
* Nothing else is interpolated. Row limits and top-N sizes are literals in
  the catalog blocks (:data:`crawler.catalog.SAMPLE_CAP`,
  :data:`crawler.catalog.TOP_N`); a configured sample size smaller than the
  cap is applied to the rows that come back, never to the SQL.
"""

from __future__ import annotations

import re

from .allowlist import AllowList
from .catalog import Template
from .errors import AllowListError

#: Any ``{placeholder}`` left in a rendered statement.
PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def bind(
    template: Template,
    allowlist: AllowList,
    *,
    schema: str,
    table: str,
    column: str | None = None,
    columns=None,
) -> str:
    """Render ``template`` for one table, with every identifier allow-listed.

    Pass ``column`` for a per-column template (B3, C1) and ``columns`` for a
    batched one (B2). Raises :class:`~crawler.errors.AllowListError` for any
    identifier this crawl did not observe.
    """
    observed_schema, observed_table = allowlist.table(schema, table)

    names: tuple[str, ...] = ()
    if columns is not None:
        if column is not None:
            raise AllowListError(
                "bind takes either column or columns, not both"
            )
        names = tuple(
            allowlist.column(observed_schema, observed_table, name)[2]
            for name in columns
        )
        if not names:
            raise AllowListError(
                f"{observed_schema}.{observed_table}: a batched statement "
                "needs at least one allow-listed column"
            )
    elif column is not None:
        names = (allowlist.column(observed_schema, observed_table, column)[2],)

    if template.is_batched and columns is None:
        raise AllowListError(f"{template.key} is batched; pass columns")
    if not template.is_batched and columns is not None:
        raise AllowListError(f"{template.key} is not batched; pass column")

    sql = template.render(names) if template.is_batched else template.sql
    sql = sql.replace("{schema}", observed_schema).replace(
        "{table}", observed_table
    )
    if names and not template.is_batched:
        sql = sql.replace("{column}", names[0])

    leftover = PLACEHOLDER.findall(sql)
    if leftover:
        raise AllowListError(
            f"{template.key}/{template.variant} still holds "
            f"{', '.join(sorted(set(leftover)))} after binding; refusing to "
            "run a half-filled template"
        )
    return sql


def batches(columns, size: int):
    """Split ``columns`` into batches of at most ``size``, order preserved.

    B2's rule is one scan for many columns (10-20 per statement), so this is
    what turns a table's column list into statements. Order is preserved
    because the result row is positional: batch order is column order.
    """
    columns = list(columns)
    if size < 1:
        raise ValueError("batch size must be at least 1")
    return [columns[i : i + size] for i in range(0, len(columns), size)]


__all__ = ["bind", "batches", "PLACEHOLDER"]
