"""Conservative join-intent extraction from A7 code-object SQL.

Legacy estates declare joins in code — view SQL, procedure bodies, the
trigger-enforced "FKs" of systems that never declared constraints — and A7
reads those definitions at catalog cost. This module mines them, under one
rule stated in the catalog and specs/01: **unparseable objects are counted,
never guessed**. The extractor is regex-conservative on purpose (BUILD-PLAN
6b): it reads exactly the SQL shapes it can be right about and refuses whole
objects it cannot, so every fact it does emit is worth the ``[observed]`` tag
emission puts on it.

What it reads:

* ``FROM``/``JOIN`` clauses of plain table references — optionally
  schema-qualified, optionally aliased, comma-separated legacy join lists
  included, parenthesised join trees (PostgreSQL's ``pg_get_viewdef``
  rendering) included.
* Equality predicates in ``ON``/``WHERE`` whose both sides are qualified
  column references (``alias.col`` / ``table.col`` / ``schema.table.col``),
  resolved through the alias map and then verified against the crawl's own
  A1/A2 inventory. An identifier the inventory did not produce resolves
  nothing.
* Multi-part names (3+ parts) in ``FROM``/``JOIN`` — a reference into
  another database or server. Those are recorded as external references,
  and predicates touching them are never join facts: no cross-database
  joins, ever.

What it refuses, per object, with the reason recorded:

* ``no-definition`` — the dictionary returned no text (encrypted module,
  missing grant).
* ``truncated`` — the dictionary cut the text (Teradata RequestText);
  partial SQL parses into wrong facts, so it is not parsed at all.
* ``dynamic-sql`` — ``EXEC``/``EXECUTE``/``sp_executesql`` anywhere in the
  body. SQL assembled at run time cannot be mined at rest.
* ``nested-select`` / ``multiple-selects`` — subqueries, CTEs, unions,
  multi-statement bodies with several SELECTs. Alias resolution across
  scopes is where guessing starts, so the extractor stays single-scope.
* ``unsupported-from`` — a FROM/JOIN item that is not a plain table
  reference (a function call, a subquery).
* ``ambiguous-alias`` — one alias bound to two different tables.
* ``unresolved-identifier`` — a table or predicate side that neither the
  inventory nor the alias map accounts for.

A refused object contributes nothing — not even the external references its
text may contain — only its entry in the unparsed count that emission puts
into index.md. Facts and the count both derive from the same records, so
nothing is dropped silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .results import (
    PARSED,
    UNPARSED,
    CodeObject,
    Column,
    ExternalReference,
    JoinIntent,
    Table,
)

#: A FROM/JOIN reference to a name of 3+ parts resolves to this sentinel:
#: known to be outside this database, recorded, never joined to.
EXTERNAL = "external"

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
#: String literals, '' escaping included. Removed before any other reading so
#: SQL inside a string (the dynamic-SQL case) is not mistaken for SQL.
_STRING_RE = re.compile(r"'(?:[^']|'')*'")

_DYNAMIC_RE = re.compile(r"\b(?:EXEC|EXECUTE|SP_EXECUTESQL)\b", re.IGNORECASE)
_SELECT_RE = re.compile(r"\bSELECT\b", re.IGNORECASE)
_NESTED_SELECT_RE = re.compile(r"\(\s*SELECT\b", re.IGNORECASE)
_FROM_JOIN_RE = re.compile(r"\b(FROM|JOIN)\b", re.IGNORECASE)

#: One identifier part: bare, "quoted" or [quoted].
_IDENT_PART_RE = re.compile(r'"([^"]+)"|\[([^\]]+)\]|([A-Za-z_][\w$#]*)')

#: A qualified column reference of 2 or 3 parts. The lookbehind/lookahead
#: keep a longer dotted name (a 4-part cross-server column) from being read
#: as a shorter internal one, and keep function calls out.
_IDENT = r'(?:[A-Za-z_][\w$#]*|"[^"]+"|\[[^\]]+\])'
_PREDICATE_RE = re.compile(
    rf'(?<![\w.$#\]"])({_IDENT}(?:\.{_IDENT}){{1,2}})'
    rf'\s*=\s*'
    rf'({_IDENT}(?:\.{_IDENT}){{1,2}})(?![\w.$#(])'
)

#: Words that end a table-reference list or cannot be an alias.
_KEYWORDS = frozenset(
    """
    all and any as asc between by case cross cube delete desc distinct else
    end except escape exists fetch for from full group having in inner
    insert intersect into is join lateral left like limit merge minus
    natural not null offset on or order outer over qualify returning right
    rollup sample select set some straight tablesample then top union
    update using values when where window with
    """.split()
)


@dataclass(frozen=True)
class _TableRef:
    """One resolved FROM/JOIN item."""

    schema: str
    table: str
    alias: str | None


@dataclass
class MiningResult:
    """Everything one pass over the A7 objects produced."""

    #: The input objects, each finalised with its parse status and reason.
    objects: list[CodeObject] = field(default_factory=list)
    join_intents: list[JoinIntent] = field(default_factory=list)
    external_references: list[ExternalReference] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def unparsed(self) -> int:
        return sum(1 for o in self.objects if o.status == UNPARSED)


class _Unparseable(Exception):
    """Raised inside one object's extraction; becomes its recorded reason."""


class _Inventory:
    """A1/A2 lookup: exact spelling first, case-insensitive when unique.

    Mirrors :class:`crawler.allowlist.AllowList` matching, over the same
    inventory: nothing resolves unless this crawl observed it.
    """

    def __init__(self, tables: list[Table], columns: list[Column]):
        self._tables: dict[tuple[str, str], tuple[str, str]] = {}
        self._by_name: dict[str, list[tuple[str, str]]] = {}
        self._columns: dict[tuple[str, str], dict[str, str]] = {}
        for table in tables:
            key = (table.schema.casefold(), table.name.casefold())
            self._tables[key] = (table.schema, table.name)
            self._by_name.setdefault(table.name.casefold(), []).append(
                (table.schema, table.name)
            )
        for column in columns:
            self._columns.setdefault(
                (column.schema, column.table), {}
            )[column.name.casefold()] = column.name

    def table(self, schema: str, name: str) -> tuple[str, str] | None:
        return self._tables.get((schema.casefold(), name.casefold()))

    def find_table(self, name: str, near_schema: str) -> tuple[str, str] | None:
        """An unqualified name: the object's own schema wins, then a match
        that is unique across the whole inventory; ambiguity resolves
        nothing — guessing a schema is how wrong facts get made."""
        own = self.table(near_schema, name)
        if own is not None:
            return own
        matches = self._by_name.get(name.casefold(), [])
        return matches[0] if len(matches) == 1 else None

    def column(self, schema: str, table: str, name: str) -> str | None:
        return self._columns.get((schema, table), {}).get(name.casefold())


def mine(
    objects: list[CodeObject],
    tables: list[Table],
    columns: list[Column],
) -> MiningResult:
    """Extract join facts and external references from ``objects``.

    ``tables``/``columns`` are the crawl's own in-scope A1/A2 inventory —
    the only names anything may resolve against.
    """
    inventory = _Inventory(tables, columns)
    result = MiningResult()
    facts: dict[tuple, JoinIntent] = {}
    externals: dict[tuple, ExternalReference] = {}

    for obj in sorted(objects, key=lambda o: (o.schema, o.name, o.kind)):
        try:
            obj_facts, obj_externals = _extract(obj, inventory)
        except _Unparseable as exc:
            result.objects.append(
                CodeObject(
                    schema=obj.schema,
                    name=obj.name,
                    kind=obj.kind,
                    definition=obj.definition,
                    truncated=obj.truncated,
                    status=UNPARSED,
                    reason=str(exc),
                )
            )
            result.warnings.append(
                f"{obj.qualified}: {obj.kind.lower()} not mined for "
                f"join intent ({exc}); counted as unparsed"
            )
            continue
        result.objects.append(
            CodeObject(
                schema=obj.schema,
                name=obj.name,
                kind=obj.kind,
                definition=obj.definition,
                truncated=obj.truncated,
                status=PARSED,
            )
        )
        for fact in obj_facts:
            facts.setdefault(_fact_key(fact), fact)
        for reference in obj_externals:
            externals.setdefault(
                (reference.kind, reference.target, reference.source), reference
            )

    result.join_intents = sorted(facts.values(), key=_fact_key)
    result.external_references = sorted(
        externals.values(), key=lambda r: (r.kind, r.target, r.source)
    )
    return result


def _fact_key(fact: JoinIntent) -> tuple:
    return (
        fact.schema, fact.table, fact.column,
        fact.other_schema, fact.other_table, fact.other_column,
        fact.source,
    )


def _extract(obj: CodeObject, inventory: _Inventory):
    """One object's facts, or raise :class:`_Unparseable` with the reason."""
    if obj.truncated:
        raise _Unparseable("truncated")
    if obj.definition is None or not obj.definition.strip():
        raise _Unparseable("no-definition")

    text = _strip(obj.definition)
    if _DYNAMIC_RE.search(text):
        raise _Unparseable("dynamic-sql")
    if _NESTED_SELECT_RE.search(text):
        raise _Unparseable("nested-select")
    if len(_SELECT_RE.findall(text)) > 1:
        raise _Unparseable("multiple-selects")

    refs, externals = _table_refs(obj, text, inventory)
    aliases = _alias_map(refs)

    facts = []
    for left_raw, right_raw in _PREDICATE_RE.findall(text):
        left = _resolve_side(left_raw, aliases, obj, inventory)
        right = _resolve_side(right_raw, aliases, obj, inventory)
        if left is EXTERNAL or right is EXTERNAL:
            continue  # recorded as an external reference; never a join fact
        if left == right:
            continue  # a tautology, not a join
        (schema, table, column), (o_schema, o_table, o_column) = sorted(
            (left, right)
        )
        facts.append(
            JoinIntent(
                schema=schema,
                table=table,
                column=column,
                other_schema=o_schema,
                other_table=o_table,
                other_column=o_column,
                source=obj.qualified,
            )
        )
    return facts, externals


def _strip(definition: str) -> str:
    """The definition with comments and string literals blanked out."""
    text = _BLOCK_COMMENT_RE.sub(" ", definition)
    text = _LINE_COMMENT_RE.sub(" ", text)
    return _STRING_RE.sub(" ", text)


def _table_refs(obj: CodeObject, text: str, inventory: _Inventory):
    """Every FROM/JOIN item: resolved refs, plus the external references."""
    refs: list[_TableRef] = []
    externals: list[ExternalReference] = []
    for match in _FROM_JOIN_RE.finditer(text):
        keyword = match.group(1).upper()
        pos = match.end()
        while True:
            parts, alias, pos = _read_ref(text, pos)
            if len(parts) >= 3:
                externals.append(
                    ExternalReference(
                        target=".".join(parts),
                        kind="multi-part-name",
                        source=obj.qualified,
                    )
                )
                refs.append(_TableRef(EXTERNAL, EXTERNAL, alias))
            else:
                refs.append(_resolve_ref(obj, parts, alias, inventory))
            # Only a FROM clause carries a comma-separated legacy join list.
            if keyword != "FROM" or not _next_is_comma(text, pos):
                break
            pos = text.index(",", pos) + 1
    return refs, externals


def _next_is_comma(text: str, pos: int) -> bool:
    remainder = text[pos:].lstrip()
    return remainder.startswith(",")


def _read_ref(text: str, pos: int):
    """One table reference at ``pos``: ``(parts, alias, new_pos)``.

    Skips the open-parens PostgreSQL's view rendering wraps join trees in.
    Anything that is not a plain (possibly qualified, possibly aliased)
    identifier — a subquery, a function call — refuses the object.
    """
    length = len(text)
    while pos < length and (text[pos].isspace() or text[pos] == "("):
        pos += 1
    parts, pos = _read_name(text, pos)
    if parts is None:
        raise _Unparseable("unsupported-from")
    if pos < length and text[pos] == "(":
        raise _Unparseable("unsupported-from")  # a function call, not a table

    alias = None
    after = _skip_space(text, pos)
    word, word_end = _peek_word(text, after)
    if word is not None and word.lower() == "as":
        after = _skip_space(text, word_end)
        word, word_end = _peek_word(text, after)
        if word is None:
            raise _Unparseable("unsupported-from")
        alias, pos = word, word_end
    elif word is not None and word.lower() not in _KEYWORDS:
        alias, pos = word, word_end
    return parts, alias, pos


def _read_name(text: str, pos: int):
    """A dotted identifier at ``pos``: ``(parts, new_pos)`` or ``(None, pos)``."""
    parts = []
    while True:
        match = _IDENT_PART_RE.match(text, pos)
        if match is None:
            return (None, pos) if not parts else (parts, pos)
        parts.append(next(g for g in match.groups() if g is not None))
        pos = match.end()
        if pos < len(text) and text[pos] == ".":
            pos += 1
            continue
        return parts, pos


def _skip_space(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _peek_word(text: str, pos: int):
    match = re.compile(r"[A-Za-z_][\w$#]*").match(text, pos)
    if match is None:
        return None, pos
    return match.group(0), match.end()


def _resolve_ref(
    obj: CodeObject, parts: list[str], alias: str | None, inventory: _Inventory
) -> _TableRef:
    if len(parts) == 1:
        resolved = inventory.find_table(parts[0], obj.schema)
    else:
        resolved = inventory.table(parts[0], parts[1])
    if resolved is None:
        raise _Unparseable(f"unresolved-identifier: {'.'.join(parts)}")
    return _TableRef(resolved[0], resolved[1], alias)


def _alias_map(refs: list[_TableRef]) -> dict[str, _TableRef]:
    """Every name a predicate may qualify a column with."""
    aliases: dict[str, _TableRef] = {}
    for ref in refs:
        if ref.alias:
            names = (ref.alias,)
        elif ref.schema is not EXTERNAL:
            names = (ref.table,)
        else:
            # An unaliased multi-part reference: its parts are not legal
            # qualifiers here, and registering the sentinel would be.
            names = ()
        for key in names:
            existing = aliases.get(key.casefold())
            if existing is not None and (existing.schema, existing.table) != (
                ref.schema,
                ref.table,
            ):
                raise _Unparseable(f"ambiguous-alias: {key}")
            aliases[key.casefold()] = ref
    return aliases


def _resolve_side(raw: str, aliases, obj: CodeObject, inventory: _Inventory):
    """One predicate side -> ``(schema, table, column)`` or ``EXTERNAL``."""
    parts, end = _read_name(raw, 0)
    if parts is None or end != len(raw):
        raise _Unparseable(f"unresolved-identifier: {raw}")
    if len(parts) == 2:
        qualifier, column = parts
        ref = aliases.get(qualifier.casefold())
        if ref is None:
            resolved = inventory.find_table(qualifier, obj.schema)
            if resolved is None:
                raise _Unparseable(f"unresolved-identifier: {raw}")
            ref = _TableRef(resolved[0], resolved[1], None)
    else:  # 3 parts: schema.table.column, spelled out
        resolved = inventory.table(parts[0], parts[1])
        if resolved is None:
            raise _Unparseable(f"unresolved-identifier: {raw}")
        ref = _TableRef(resolved[0], resolved[1], None)
        column = parts[2]
    if ref.schema is EXTERNAL:
        return EXTERNAL
    observed = inventory.column(ref.schema, ref.table, column)
    if observed is None:
        raise _Unparseable(f"unresolved-identifier: {raw}")
    return (ref.schema, ref.table, observed)


__all__ = ["EXTERNAL", "MiningResult", "mine"]
