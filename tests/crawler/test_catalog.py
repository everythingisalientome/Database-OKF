"""The catalog is the only SQL that exists.

These tests re-derive the catalog from the markdown on every run and compare
it with what the crawler can issue. They are the mechanical half of
"no dynamic SQL anywhere": if someone adds a statement to
:mod:`crawler.catalog` that is not in ``catalog/step1-query-catalog.md``, or
edits a query in the catalog without updating the code, the build stops here.
"""

from __future__ import annotations

import re

import pytest

from crawler import catalog, gaps

PROPOSALS = "catalog/proposals/step1-catalog-gaps.md"


def extract_blocks(text: str):
    """Every fenced ```sql block in the catalog, with its heading and context.

    Returns ``[(heading, context, sql)]``. ``context`` is the prose since the
    last block or heading — which is where a block says which engine it is
    for, sometimes as a bare ``ANSI:`` label and sometimes as a sentence.
    Empty blocks are skipped: they carry no SQL to check.
    """
    lines = text.split("\n")
    heading = ""
    context: list[str] = []
    blocks = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("#"):
            heading, context = line.lstrip("#").strip(), []
        elif line.startswith("```sql"):
            body = []
            index += 1
            while not lines[index].startswith("```"):
                body.append(lines[index])
                index += 1
            sql = "\n".join(body).strip()
            if sql:
                blocks.append((heading, "\n".join(context), sql))
            context = []
        elif line.strip():
            context.append(line.strip())
        index += 1
    return blocks


@pytest.fixture(scope="module")
def blocks(request):
    text = (
        request.config.rootpath / "catalog" / "step1-query-catalog.md"
    ).read_text(encoding="utf-8")
    return extract_blocks(text)


ALL_STATEMENTS = catalog.all_statements()
STATEMENT_IDS = [f"{s.key}-{s.variant}" for s in ALL_STATEMENTS]


@pytest.mark.parametrize("statement", ALL_STATEMENTS, ids=STATEMENT_IDS)
def test_statement_is_verbatim_from_the_catalog(statement, blocks):
    catalog_sql = [sql for _heading, _context, sql in blocks]
    assert statement.sql in catalog_sql, (
        f"{statement.key}/{statement.variant} is not a verbatim catalog "
        "block. Statements are copies, not paraphrases — fix the copy, or "
        "propose the change in the catalog first."
    )


@pytest.mark.parametrize("statement", ALL_STATEMENTS, ids=STATEMENT_IDS)
def test_statement_records_the_heading_it_came_from(statement, blocks):
    headings = {heading for heading, _context, sql in blocks if sql == statement.sql}
    assert statement.heading in headings


#: How each catalog variant announces itself in a block's heading or the
#: prose above it.
VARIANT_WORDS = {
    "ansi": ("ansi",),
    "postgres": ("postgresql", "postgres"),
    "sqlserver": ("sql server",),
    "teradata": ("teradata", "-td"),
}


@pytest.mark.parametrize("statement", ALL_STATEMENTS, ids=STATEMENT_IDS)
def test_statement_variant_matches_the_catalog(statement, blocks):
    """Registering Oracle's block as the ANSI variant is the failure this
    catches: the SQL would be verbatim and still wrong for the engine."""
    where = " ".join(
        f"{heading}\n{context}"
        for heading, context, sql in blocks
        if sql == statement.sql
    ).casefold()
    assert any(word in where for word in VARIANT_WORDS[statement.variant]), (
        f"{statement.key} is registered as the {statement.variant} variant, "
        f"but its catalog block never says so: {where!r}"
    )
    for variant, words in VARIANT_WORDS.items():
        if variant == statement.variant or variant == "ansi":
            continue
        if statement.variant == "ansi" and variant in ("postgres", "sqlserver"):
            continue  # the ANSI blocks are explicitly labelled for both
        assert not any(word in where for word in words), (
            f"{statement.key} is registered as {statement.variant} but its "
            f"block is labelled for {variant}"
        )


@pytest.mark.parametrize("statement", ALL_STATEMENTS, ids=STATEMENT_IDS)
def test_tier_a_statements_take_no_parameters(statement):
    assert not statement.is_parameterised
    assert not re.search(r"\{[a-z_]+\}", statement.sql), (
        "a Tier A statement must not contain an identifier placeholder"
    )


@pytest.mark.parametrize("statement", ALL_STATEMENTS, ids=STATEMENT_IDS)
def test_registered_statements_are_tier_a(statement, blocks):
    """No Tier B/C template is reachable from the Tier A registry."""
    headings = {heading for heading, _context, sql in blocks if sql == statement.sql}
    assert all(re.match(r"^A\d", heading) for heading in headings), (
        f"{statement.key} resolves to a non-Tier-A catalog block: {headings}"
    )
    assert statement.key in catalog.TIER_A
    assert statement.query_id in catalog.QUERY_IDS
    assert statement.provides


@pytest.mark.parametrize("engine", catalog.ENGINES)
def test_every_engine_can_inventory(engine):
    """A1 and A2 are the crawl; an engine without them is not supported."""
    assert catalog.statement(engine, "A1") is not None
    assert catalog.statement(engine, "A2") is not None


@pytest.mark.parametrize("engine", catalog.ENGINES)
def test_the_catalog_covers_every_tier_a_query(engine):
    """Since the P1-P7 adoptions, every engine has a block for every query."""
    covered = {stmt.query_id for stmt in catalog.statements_for(engine)}
    assert covered == set(catalog.QUERY_IDS)


@pytest.mark.parametrize("engine", catalog.ENGINES)
def test_missing_statements_are_declared_gaps(engine):
    """Every Tier A query the catalog cannot answer has a proposal behind it.

    This is the rule from BUILD-PLAN's session protocol, as a test: a query
    is either in the catalog or in the gap register, never simply absent.
    """
    declared = {
        gap.query_id for gap in gaps.gaps_for(engine) if not gap.partial
    }
    covered = {stmt.query_id for stmt in catalog.statements_for(engine)}
    for query_id in catalog.QUERY_IDS:
        if query_id not in covered:
            assert query_id in declared, (
                f"{engine} has no catalog block for {query_id} and no gap "
                "declared for it"
            )


@pytest.mark.parametrize("engine", catalog.ENGINES)
def test_statements_run_in_the_spec_order(engine):
    """A5 reconciles last; it is what catches a grant gap in everything
    before it."""
    keys = [stmt.key for stmt in catalog.statements_for(engine)]
    assert keys[0] == "A1"
    assert keys[1] == "A2"
    assert keys[-1] == "A5"


def test_one_query_id_can_have_several_blocks():
    """SQL Server answers A6 with two: row counts, and column distincts."""
    a6 = [s for s in catalog.statements_for("sqlserver") if s.query_id == "A6"]
    assert [s.key for s in a6] == ["A6", "A6-columns"]
    assert a6[0].provides == (catalog.TABLE_STATS,)
    assert a6[1].provides == (catalog.COLUMN_STATS,)
    # PostgreSQL and Teradata answer both halves with one block.
    for engine in ("postgres", "teradata"):
        single = [s for s in catalog.statements_for(engine) if s.query_id == "A6"]
        assert len(single) == 1
        assert set(single[0].provides) == {catalog.TABLE_STATS, catalog.COLUMN_STATS}


@pytest.mark.parametrize("engine", catalog.ENGINES)
def test_declared_gaps_are_real(engine):
    """A non-partial gap must not name a query the catalog does cover."""
    covered = {stmt.query_id for stmt in catalog.statements_for(engine)}
    for gap in gaps.gaps_for(engine):
        if not gap.partial:
            assert gap.query_id not in covered, (
                f"gap {gap.proposal} claims {engine}/{gap.query_id} is "
                "missing, but a catalog block is registered for it"
            )


def test_adopted_proposals_are_no_longer_open_gaps():
    """P1-P7 were adopted into the catalog; carrying them as live gaps would
    make every crawl report evidence missing that is now collected."""
    open_proposals = {gap.proposal for gap in gaps.GAPS}
    assert open_proposals.isdisjoint(
        {"P1", "P2", "P3", "P4", "P5", "P6", "P7"}
    )


def test_every_gap_has_a_written_proposal(request):
    text = (request.config.rootpath / PROPOSALS).read_text(encoding="utf-8")
    for gap in gaps.GAPS:
        assert f"## {gap.proposal}" in text, (
            f"gap {gap.proposal} ({gap.engine}/{gap.query_id}) has no section "
            f"in {PROPOSALS}"
        )


def test_proposed_sql_is_not_registered(request):
    """Proposals are proposals until adopted — none of their SQL may run.

    Vacuous while nothing is open, which is the current state: every
    proposal session 2 filed has been adopted, and adopted SQL lives in the
    catalog rather than here.
    """
    text = (request.config.rootpath / PROPOSALS).read_text(encoding="utf-8")
    proposed = {sql for _heading, _context, sql in extract_blocks(text)}
    for statement in ALL_STATEMENTS:
        assert statement.sql not in proposed, (
            f"{statement.key}/{statement.variant} runs SQL that is only "
            "proposed, not adopted into the catalog"
        )


def test_unverified_engines_are_flagged():
    from crawler.adapters import for_engine

    for engine in catalog.ENGINES:
        assert for_engine(engine).verified == (
            engine not in catalog.UNVERIFIED_ENGINES
        )
