"""Emit the OKF bundle — one crawl result plus its annotations, on disk.

This is the last step of a step 1 run: everything measured lands as
``[observed]`` lines, everything the annotator said as ``[inferred:conf]``
lines, and the two never mix (specs/04 — LLM output is never ``[observed]``).
The output is the layout specs/01 promises::

    <okf-root>/db/<dbname>/index.md
    <okf-root>/db/<dbname>/<schema>/<table>.md
    <okf-root>/db/<dbname>/fingerprints/<schema>.<table>.<column>.json

Rendering follows the fixture bundles under ``tests/fixtures/okf`` line for
line — they are the ground truth for what a table file looks like. A gated
column that carries no fingerprint says why, in the specs/04 suppression
vocabulary (``suppressed (sensitive|budget|unparseable-temporal)``); a
column that failed the cardinality/index gate carries no fingerprint line
at all.

Emission is deterministic: the same result and annotations produce the same
bytes, because bundles are git-committed per refresh and the diff is the
drift signal. Tables are emitted sorted, columns in ordinal order, and
nothing here consults a clock. Emission also never invents: a table whose
row count the crawl could not establish is emitted without one and reported,
which the validator then flags — an honest hole, not a guessed number.
Views (A1 kind ``VIEW``) are cataloged in the crawl result but not emitted;
they have no rows to count or profile, and the spec's table-file contract
requires both. Each one is recorded in the emission's warnings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from okf import (
    ContentLine,
    Field,
    FrontMatter,
    IndexDocument,
    IndexEntry,
    TableDocument,
    write_document,
)
from okf import Column as OkfColumn
from okf.provenance import INFERRED, OBSERVED, Provenance

from .annotate import Annotations, ColumnView, TableView, table_views
from .results import UNVERIFIED, CrawlResult

#: Top-N values shown on the ``top_values:`` line. The measuring pass keeps
#: up to 20 in the crawl result; the fixture bundles publish six. The full
#: list stays in the crawl JSON.
TOP_VALUES_SHOWN = 6

#: Canonical base types whose min/max render bare; everything else is quoted.
#: (The fixtures: ``range: [1 .. 412]`` for INT, ``[0.99 .. 25.86]`` for
#: NUMERIC, ``['2021/1/1' .. '2025/9/7']`` for the rendered TIMESTAMP.)
_NUMERIC_BASES = frozenset(
    {
        "INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT",
        "DECIMAL", "NUMERIC", "NUMBER", "FLOAT", "REAL", "DOUBLE",
        "DOUBLE PRECISION",
    }
)

_DENSE_COMMENT = (
    "  # contiguous surrogate range - value overlap non-distinctive"
)

_SENSITIVE_LINE = "sensitive-listed: top-N and fingerprint suppressed"


@dataclass
class PreparedBundle:
    """A bundle built in memory, ready to write (or to inspect in a test)."""

    database: str
    index: IndexDocument
    tables: list[TableDocument] = field(default_factory=list)
    #: ``crawler.results.Fingerprint`` payloads, each knowing its own
    #: bundle-relative path.
    fingerprints: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Emission:
    """What one emit run wrote."""

    root: Path
    documents: list[Path] = field(default_factory=list)
    fingerprints: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def prepare(result: CrawlResult, annotations: Annotations) -> PreparedBundle:
    """Build every document of the bundle, without touching disk."""
    warnings: list[str] = []
    views = table_views(result)
    profiles = {(p.schema, p.table, p.column): p for p in result.column_profiles}

    for table in result.tables:
        if not table.is_base_table:
            warnings.append(
                f"{table.qualified}: cataloged as {table.kind} but not "
                "emitted; the table-file contract needs a row count and a "
                "profile, which a view does not have"
            )

    tables: list[TableDocument] = []
    fingerprints: list = []
    entries: list[IndexEntry] = []
    for view in views:
        annotation = annotations.for_table(view.schema, view.table)
        document = _table_document(result, view, annotation, profiles, warnings)
        tables.append(document)
        entries.append(
            IndexEntry(
                path=f"{view.schema}/{view.table}.md",
                description=_entry_description(annotation.description, view),
            )
        )
        for column in view.columns:
            profile = profiles.get((view.schema, view.table, column.name))
            fingerprint = profile.fingerprint if profile else None
            if fingerprint is not None:
                fingerprints.append(fingerprint)

    index = _index_document(result, annotations, entries)
    return PreparedBundle(
        database=result.database,
        index=index,
        tables=tables,
        fingerprints=fingerprints,
        warnings=warnings,
    )


def emit(result: CrawlResult, annotations: Annotations, okf_root) -> Emission:
    """Write the bundle under ``<okf_root>/db/<database>`` and report it.

    Existing files are overwritten in place; nothing is deleted. Comparing a
    refresh against the previous bundle is the refresh diff's job (session 5),
    and it works on git history, not on this function tidying up.
    """
    prepared = prepare(result, annotations)
    root = Path(okf_root) / "db" / prepared.database
    emission = Emission(root=root, warnings=list(prepared.warnings))

    emission.documents.append(
        write_document(prepared.index, root / "index.md")
    )
    for document in prepared.tables:
        schema, table = document.name.split(".", 1)
        emission.documents.append(
            write_document(document, root / schema / f"{table}.md")
        )
    for fingerprint in prepared.fingerprints:
        target = root / fingerprint.path
        target.parent.mkdir(parents=True, exist_ok=True)
        # json.dumps with default separators — byte-identical to the okf
        # package's writer and to the committed fixture payloads.
        target.write_text(
            json.dumps(fingerprint.to_payload()), encoding="utf-8"
        )
        emission.fingerprints.append(target)
    return emission


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------


def _observed(text: str) -> ContentLine:
    return ContentLine(Provenance(OBSERVED), text)


def _inferred(confidence: str, text: str) -> ContentLine:
    return ContentLine(Provenance(INFERRED, confidence), text)


def _table_document(
    result, view: TableView, annotation, profiles, warnings
) -> TableDocument:
    fields = [
        Field("type", "table"),
        Field("name", view.qualified),
        Field("description", annotation.description),
        Field("description_confirmed", False),
        Field("database", result.database),
        Field("engine", result.engine),
    ]
    if view.row_count is None:
        warnings.append(
            f"{view.qualified}: no row count from any source; emitted "
            "without one, which the validator will flag"
        )
    else:
        fields.append(Field("row_count", view.row_count))
        fields.append(Field("row_count_source", view.row_count_source))
        if view.stats_date is not None:
            fields.append(Field("stats_date", view.stats_date))
    fields.append(Field("crawl_date", result.crawl_date))
    fields.append(Field("flags", list(view.flags)))

    document = TableDocument(
        frontmatter=FrontMatter(fields),
        lines=[_inferred(annotation.confidence, f"Purpose: {annotation.purpose}")],
    )
    for column in view.columns:
        profile = profiles.get((view.schema, view.table, column.name))
        document.columns.append(
            _column_block(column, profile, annotation.columns[column.name])
        )
    return document


def _column_block(column: ColumnView, profile, annotation):
    lines: list[ContentLine] = [
        _observed(
            f"type: {column.type}, "
            + ("nullable" if column.nullable else "not null")
        )
    ]

    counts = [
        f"{key}: {value}"
        for key, value in (
            ("distinct_count", column.distinct_count),
            ("distinct_ratio", column.distinct_ratio),
            ("null_rate", column.null_rate),
        )
        if value is not None
    ]
    if counts:
        text = "; ".join(counts)
        if column.source != "live":
            # specs/01 step 1: profile lines record their source and date.
            stamp = column.source
            if column.stats_date is not None:
                stamp += f" {column.stats_date.isoformat()}"
            text += f"  # source: {stamp}"
        lines.append(_observed(text))

    if column.min_length is not None and column.max_length is not None:
        lines.append(
            _observed(
                f"length: min {column.min_length}, max {column.max_length}, "
                f"avg {column.avg_length}"
            )
        )

    lines.extend(_format_line(column))

    if column.dense_sequence:
        lines.append(_observed("dense_sequence: true" + _DENSE_COMMENT))
    for constraint in column.constraints:
        lines.append(_observed(f"constraint: {constraint}"))
    for index in column.indexes:
        lines.append(_observed(f"index: {index}"))
    for intent in column.join_intents:
        # A7: code-declared join evidence, deterministic extraction — the
        # one [observed] line in the file that came from somebody's SQL
        # rather than the dictionary, which is why it names its source.
        lines.append(_observed(f"join-intent: {intent}"))
    if column.sensitive:
        lines.append(_observed(_SENSITIVE_LINE))

    if column.top_values:
        shown = ", ".join(
            f"{value}({percent}%)"
            for value, percent in column.top_values[:TOP_VALUES_SHOWN]
        )
        lines.append(_observed(f"top_values: {shown}"))

    fingerprint = profile.fingerprint if profile else None
    if fingerprint is not None:
        lines.append(
            _observed(f"fingerprint: {fingerprint.algo} @ {fingerprint.path}")
        )
        rules = ", ".join(fingerprint.normalization) or "none"
        lines.append(_observed(f"normalization: [{rules}]"))
    elif column.fingerprint_suppressed:
        # specs/04 suppression vocabulary: step 2 reads absence of
        # measurement, not absence of evidence.
        lines.append(
            _observed(f"fingerprint: suppressed ({column.fingerprint_suppressed})")
        )

    lines.append(_inferred(annotation.confidence, annotation.text))
    return OkfColumn(column.name, lines)


def _format_line(column: ColumnView):
    if column.sensitive:
        if column.format is None:
            # classify_sensitive_formats withdrawn for this run (P14): the
            # category itself was not persisted, so there is nothing to say.
            return []
        return [
            _observed(
                f"format: {column.format}  (sensitive-listed: range suppressed)"
            )
        ]
    parts = []
    if column.format is not None:
        parts.append(f"format: {column.format}")
    if column.min_value is not None and column.max_value is not None:
        low, high = _bound(column, column.min_value), _bound(column, column.max_value)
        parts.append(f"range: [{low} .. {high}]")
    return [_observed("; ".join(parts))] if parts else []


def _bound(column: ColumnView, value: str) -> str:
    base = column.type.split("(", 1)[0].strip().upper()
    if base in _NUMERIC_BASES:
        return value
    return repr(str(value))


def _entry_description(description: str, view: TableView) -> str:
    """The child-index one-liner: the frontmatter description (extracted
    mechanically, per the conventions) plus the row count the fixtures append."""
    if view.row_count is None:
        return description
    return f"{description} ({view.row_count} rows)"


def _index_document(result, annotations: Annotations, entries) -> IndexDocument:
    completeness = Field("completeness", result.completeness)
    reconciliation = result.reconciliation
    if reconciliation is not None and reconciliation.note:
        completeness.comment = f"  # reconciliation: {reconciliation.note}"
    elif result.completeness == UNVERIFIED:
        completeness.comment = "  # reconciliation: never ran"

    database = annotations.database
    return IndexDocument(
        frontmatter=FrontMatter(
            [
                Field("type", "index"),
                Field("database", result.database),
                Field("description", database.description),
                Field("engine", result.engine),
                Field("build_date", result.crawl_date),
                completeness,
            ]
        ),
        summary=[
            _inferred(database.confidence, database.summary),
            *_code_object_lines(result),
        ],
        section="Tables",
        entries=list(entries),
    )


def _code_object_lines(result) -> list[ContentLine]:
    """The specs/01 index.md lines A7/A8 owe: the unparsed-object count and
    the recorded external references. Derived facts only — the definitions
    themselves stay in the crawl JSON and never reach a bundle.

    The count line renders whenever A7 ran, zero included: "no code objects"
    and "A7 never ran" must not read identically.
    """
    ran_a7 = any(
        q.query_id == "A7" and q.status == "ok" for q in result.queries
    )
    lines = []
    if ran_a7 or result.code_objects:
        lines.append(
            _observed(
                f"code_objects: {len(result.code_objects)}; "
                f"unparsed: {result.unparsed_code_objects}"
            )
        )
    for reference in result.external_references:
        text = f"external-reference: {reference.kind} {reference.target}"
        if reference.detail:
            text += f" [{reference.detail}]"
        text += f" (source: {reference.source})"
        lines.append(_observed(text))
    return lines


__all__ = [
    "TOP_VALUES_SHOWN",
    "Emission",
    "PreparedBundle",
    "emit",
    "prepare",
]
