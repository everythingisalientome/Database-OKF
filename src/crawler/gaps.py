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

Session 2 opened P1–P9 and all nine were adopted into the catalog; the
adoption log is in the proposals document. The register is empty as a
result, which is a claim in itself: every Tier A query is covered for every
supported engine, and the crawler is not quietly missing evidence.
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


#: No open gaps. Every Tier A query is covered for every supported engine,
#: and the adopted blocks answer them in full — P1-P9 were opened by session
#: 2 and all nine were adopted. An entry here means the crawler knows it
#: cannot collect something; the empty tuple means it believes it can.
GAPS: tuple[CatalogGap, ...] = ()


def gaps_for(engine: str) -> tuple[CatalogGap, ...]:
    """Gaps that apply to a crawl on ``engine``.

    ``ansi``-scoped gaps apply to every engine that runs the ANSI blocks.
    """
    ansi_engines = ("postgres", "sqlserver")
    return tuple(
        gap
        for gap in GAPS
        if gap.engine == engine or (gap.engine == "ansi" and engine in ansi_engines)
    )
