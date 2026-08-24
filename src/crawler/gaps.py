"""Known catalog gaps — queries the crawler needs and the catalog lacks.

The build rule is: missing catalog queries get proposed and flagged for
adoption, never silently invented (BUILD-PLAN.md, session protocol step 4).
This module is the machine-readable half of that rule. Each entry names an
engine, the Tier A query it affects, what the crawler consequently cannot
produce, and the proposal that would close it in
``catalog/proposals/step1-catalog-gaps.md``.

A gap is not a failure. The crawler records the applicable gaps in its
result, so every downstream consumer can see exactly which evidence is
missing and why, instead of reading an empty index list as "this database has
no indexes".

Session 2 opened P1-P9 and all nine were adopted into the catalog. Session 3
opened P10-P15; P10-P13 were adopted directly and P14/P15 — the two that
needed rulings rather than SQL — were adjudicated in-session (2026-08-24) and
adopted too: B4 samples every column transiently for format classification,
sensitive ones included with only the category persisting, and temporal
values render canonically (``YYYY/M/D``) before any value work. The register
is empty again, which is a claim in itself: every query the crawler needs is
covered for every supported engine, and nothing is quietly missing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogGap:
    """One query the catalog does not (fully) cover for one engine."""

    engine: str
    query_id: str
    #: True when the catalog has a block but it returns less than the query
    #: is specified to return; False when there is no block at all.
    partial: bool
    #: Why the catalog cannot answer this today.
    reason: str
    #: What the crawl output loses because of it.
    impact: str
    #: Proposal id in catalog/proposals/step1-catalog-gaps.md.
    proposal: str


#: No open gaps. An entry here means the crawler knows it cannot collect
#: something, and every crawl result carries the ones that applied to it — so
#: a bundle says "no format was classified, gap Pn" rather than looking like
#: a database whose columns have no discernible shape. The empty tuple means
#: the crawler believes it can collect everything the spec asks for.
GAPS: tuple[CatalogGap, ...] = ()


def gaps_for(engine: str) -> tuple[CatalogGap, ...]:
    """Gaps that apply to a crawl on ``engine``.

    ``ansi``-scoped gaps apply to every engine that runs the ANSI blocks;
    ``all``-scoped ones apply everywhere, which is where a gap lands when what
    is missing is a decision rather than one engine's dialect.
    """
    ansi_engines = ("postgres", "sqlserver")
    return tuple(
        gap
        for gap in GAPS
        if gap.engine in (engine, "all")
        or (gap.engine == "ansi" and engine in ansi_engines)
    )
