"""The annotation pass — the one place step 1 asks a model anything.

Descriptions are REQUIRED at three levels (specs/01 step 6): the database
summary in index.md, a one-line table description plus a ``Purpose:`` body
line, and at least one description line per column. They are step 3's
semantic matching surface, not documentation, which is why "skip it" is not
among the outcomes: where no guess is supportable the pass writes the
explicit unknown — ``insufficient evidence to describe`` at ``low``, which
the conventions HITL-queue — and never a silent omission.

The seam is deliberately narrow. An :class:`Annotator` is handed *views*:
frozen snapshots holding names, types, profile numbers, top-N values and
constraint/index membership — the same derived artifacts the bundle itself
publishes, and nothing else. Raw rows never reach a view because raw rows
never reach the crawl result (sensitive columns arrive with their bounds and
values already withheld by the measuring pass, and the view builder scrubs
them again on principle). Whatever comes back is sanitized *in code*:
confidence vocabulary enforced, prose flattened to one line, stray provenance
tags stripped, hallucinated columns dropped, gaps filled with the explicit
unknown. The model is trusted to describe, never to format (CLAUDE.md: when
in doubt, move logic from prompt to code).

Two annotators ship. :class:`NullAnnotator` answers nothing and exists so a
run without a model still emits a complete, valid, honest bundle — every
description an explicit unknown, all of it queued for humans.
:class:`LLMAnnotator` wraps any ``complete(prompt) -> text`` callable and
speaks strict JSON; the callable is where a deployment plugs in its model
client, which keeps this package free of SDK and network dependencies.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Mapping

from okf.provenance import CONFIDENCES

from .errors import AnnotationError
from .results import (
    BUDGET_DENIED,
    GATE_CARDINALITY,
    UNPARSEABLE_TEMPORAL,
    CrawlResult,
)

#: The explicit unknown (specs/01 step 6). ``low`` HITL-queues it.
INSUFFICIENT = "insufficient evidence to describe"

#: The confidence every fallback carries.
LOW = "low"

_TAG_PREFIX_RE = re.compile(r"^(?:-\s+)?\[[a-z-]+(?::[a-z]+)?\]\s*")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s")
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n(.*)\n```\s*$", re.DOTALL)


# ---------------------------------------------------------------------------
# views — what an annotator is allowed to see
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnView:
    """One column's derived artifacts. No raw rows, ever; for a
    sensitive-listed column no values in any form, bounds included."""

    name: str
    type: str
    nullable: bool
    sensitive: bool = False
    distinct_count: int | None = None
    distinct_ratio: float | None = None
    null_rate: float | None = None
    min_value: str | None = None
    max_value: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    avg_length: float | None = None
    format: str | None = None
    dense_sequence: bool = False
    #: ``(value, percent)`` pairs — B3's literals, the one place the storage
    #: rules allow values, and specs/01 lists them as an annotator input.
    top_values: tuple[tuple[str, int], ...] = ()
    #: Rendered descriptors, e.g. ``PRIMARY KEY``,
    #: ``FOREIGN KEY -> CORE.artist.artist_id``.
    constraints: tuple[str, ...] = ()
    #: Rendered descriptors, e.g. ``non-unique``,
    #: ``PRIMARY INDEX (Teradata PI)``.
    indexes: tuple[str, ...] = ()
    #: Why a gated column carries no fingerprint — the specs/04 suppression
    #: vocabulary (``sensitive`` | ``budget`` | ``unparseable-temporal``), or
    #: None where a fingerprint exists or the column was never eligible.
    fingerprint_suppressed: str | None = None
    #: Where the numbers came from (``live`` | ``stats`` | ``stats-estimate``)
    #: and the dictionary's collection date, when they are not a measurement.
    source: str = "live"
    stats_date: date | None = None

    def to_obj(self) -> dict:
        """The evidence as a prompt payload: present facts only."""
        out: dict = {"name": self.name, "type": self.type,
                     "nullable": self.nullable}
        if self.sensitive:
            out["sensitive_listed"] = True
        for key in ("distinct_count", "distinct_ratio", "null_rate",
                    "min_value", "max_value", "min_length", "max_length",
                    "avg_length", "format"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.dense_sequence:
            out["dense_sequence"] = True
        if self.top_values:
            out["top_values"] = [
                {"value": value, "percent": percent}
                for value, percent in self.top_values
            ]
        if self.constraints:
            out["constraints"] = list(self.constraints)
        if self.indexes:
            out["indexes"] = list(self.indexes)
        if self.fingerprint_suppressed:
            out["fingerprint_suppressed"] = self.fingerprint_suppressed
        if self.source != "live":
            out["numbers_from"] = self.source
        return out


@dataclass(frozen=True)
class TableView:
    """One table's derived artifacts, plus the frontmatter facts the bundle
    will carry — emission renders from this same view, so what the annotator
    saw and what the bundle publishes cannot drift apart."""

    database: str
    engine: str
    schema: str
    table: str
    row_count: int | None = None
    row_count_source: str | None = None
    stats_date: date | None = None
    flags: tuple[str, ...] = ()
    columns: tuple[ColumnView, ...] = ()

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"

    def to_obj(self) -> dict:
        out: dict = {
            "database": self.database,
            "engine": self.engine,
            "table": self.qualified,
        }
        if self.row_count is not None:
            out["row_count"] = self.row_count
        if self.flags:
            out["flags"] = list(self.flags)
        out["columns"] = [column.to_obj() for column in self.columns]
        return out


@dataclass(frozen=True)
class DatabaseView:
    """What the database summary is synthesized from: the annotated tables
    (specs/01 — the summary comes *after* all tables are annotated)."""

    database: str
    engine: str
    completeness: str
    #: ``(qualified table, row_count, one-liner, purpose)``.
    tables: tuple[tuple[str, int | None, str, str], ...] = ()

    def to_obj(self) -> dict:
        return {
            "database": self.database,
            "engine": self.engine,
            "completeness": self.completeness,
            "tables": [
                {
                    "table": name,
                    "row_count": rows,
                    "description": description,
                    "purpose": purpose,
                }
                for name, rows, description, purpose in self.tables
            ],
        }


def table_views(result: CrawlResult) -> tuple[TableView, ...]:
    """Views for every base table, sorted the way the bundle will be."""
    table_profiles = {(p.schema, p.table): p for p in result.table_profiles}
    table_stats = {(s.schema, s.table): s for s in result.table_stats}
    column_profiles = {
        (p.schema, p.table, p.column): p for p in result.column_profiles
    }
    column_stats = {
        (s.schema, s.table, s.column): s for s in result.column_stats
    }
    columns_of: dict[tuple[str, str], list] = {}
    for column in result.columns:
        columns_of.setdefault((column.schema, column.table), []).append(column)

    views = []
    for table in sorted(result.base_tables, key=lambda t: (t.schema, t.name)):
        key = (table.schema, table.name)
        profile = table_profiles.get(key)
        stats = table_stats.get(key)
        row_count = source = stats_date = None
        if profile is not None:
            row_count, source, stats_date = (
                profile.row_count, profile.source, profile.stats_date,
            )
        elif stats is not None:
            row_count, source, stats_date = (
                stats.row_count, stats.source, stats.stats_date,
            )
        views.append(
            TableView(
                database=result.database,
                engine=result.engine,
                schema=table.schema,
                table=table.name,
                row_count=row_count,
                row_count_source=source,
                stats_date=stats_date,
                flags=_table_flags(result, table, profile),
                columns=tuple(
                    _column_view(result, column, column_profiles, column_stats)
                    for column in columns_of.get(key, [])
                ),
            )
        )
    return tuple(views)


def database_view(result: CrawlResult, annotated) -> DatabaseView:
    """``annotated`` is ``(TableView, TableAnnotation)`` pairs, in order."""
    return DatabaseView(
        database=result.database,
        engine=result.engine,
        completeness=result.completeness,
        tables=tuple(
            (view.qualified, view.row_count,
             annotation.description, annotation.purpose)
            for view, annotation in annotated
        ),
    )


def _table_flags(result, table, profile) -> tuple[str, ...]:
    """Frontmatter flags: the Teradata PI (specs/01: ``[pi:ACCT_ID]``) first —
    the join-intent signal that must survive every hop to step 2 — then the
    measuring pass's own (junk-suspect, empty)."""
    flags = []
    for index in result.indexes:
        if (index.schema, index.table) != (table.schema, table.name):
            continue
        if index.primary_index and index.columns:
            flags.append("pi:" + "+".join(index.columns))
    if profile is not None:
        flags.extend(profile.flags)
    return tuple(flags)


def _column_view(result, column, column_profiles, column_stats) -> ColumnView:
    key = (column.schema, column.table, column.name)
    profile = column_profiles.get(key)
    constraints = _constraint_descriptors(result, column)
    indexes = _index_descriptors(
        result, column, in_primary_key="PRIMARY KEY" in constraints
    )
    common = {
        "name": column.name,
        "type": column.type,
        "nullable": column.nullable,
        "constraints": constraints,
        "indexes": indexes,
    }
    if profile is not None:
        # The measuring pass already withholds a sensitive column's bounds and
        # values; scrubbing again here means a hand-built or future profile
        # cannot leak them into a prompt either.
        sensitive = profile.sensitive
        return ColumnView(
            sensitive=sensitive,
            distinct_count=profile.distinct_count,
            distinct_ratio=profile.distinct_ratio,
            null_rate=profile.null_rate,
            min_value=None if sensitive else profile.min_value,
            max_value=None if sensitive else profile.max_value,
            min_length=profile.min_length,
            max_length=profile.max_length,
            avg_length=profile.avg_length,
            format=profile.format,
            dense_sequence=profile.dense_sequence,
            top_values=()
            if sensitive
            else tuple((v.value, v.percent) for v in profile.top_values),
            fingerprint_suppressed=_fingerprint_suppression(profile),
            source=profile.source,
            stats_date=profile.stats_date,
            **common,
        )
    stats = column_stats.get(key)
    if stats is not None:
        return ColumnView(
            distinct_count=stats.distinct_count,
            null_rate=(
                None if stats.null_rate is None else round(stats.null_rate, 4)
            ),
            source="stats-estimate" if stats.approximate else "stats",
            stats_date=stats.stats_date,
            **common,
        )
    return ColumnView(**common)


def _fingerprint_suppression(profile) -> str | None:
    """The specs/04 suppression vocabulary for one profiled column.

    The measuring pass records every refusal, but in one flat list shared
    with top-N and format refusals; this reads the fingerprint's own story
    back out of it. A gated column that could have had a fingerprint names
    its reason; a column that failed the cardinality/index gate carries no
    line at all — absence of eligibility, not absence of measurement. A
    junk-suspect table's columns also carry none: the vocabulary has no
    token for that, and the table's own flags already say why nothing was
    measured (reported at adjudication).
    """
    if profile.fingerprint is not None:
        return None
    if profile.sensitive:
        return "sensitive"
    if UNPARSEABLE_TEMPORAL in profile.suppressed:
        return "unparseable-temporal"
    if GATE_CARDINALITY in profile.suppressed:
        return None
    if BUDGET_DENIED in profile.suppressed:
        return "budget"
    return None


def _constraint_descriptors(result, column) -> tuple[str, ...]:
    """A3 membership, in the fixtures' order: PK, then FKs, then the rest."""
    ranked = []
    for constraint in result.constraints:
        if (constraint.schema, constraint.table) != (column.schema, column.table):
            continue
        if column.name not in constraint.columns:
            continue
        if constraint.kind == "PRIMARY KEY":
            ranked.append((0, "PRIMARY KEY"))
        elif constraint.kind == "FOREIGN KEY":
            ranked.append((1, "FOREIGN KEY -> " + _fk_target(constraint, column)))
        else:
            ranked.append((2, constraint.kind))
    return tuple(text for _rank, text in sorted(ranked, key=lambda r: r[0]))


def _fk_target(constraint, column) -> str:
    """Where the FK points, column paired by position; an unresolved target
    (outside the account's grants) is reported as unresolved, never guessed."""
    if not constraint.referenced_table:
        return "(target unresolved)"
    position = constraint.columns.index(column.name)
    if position < len(constraint.referenced_columns):
        return f"{constraint.referenced_table}.{constraint.referenced_columns[position]}"
    return constraint.referenced_table


def _index_descriptors(result, column, *, in_primary_key: bool) -> tuple[str, ...]:
    """A4 membership. One line for plain indexes (the fixtures' rendering —
    the PK's own backing index is not repeated under a PK column), plus the
    Teradata PI, which is never elided: it is a first-class join signal."""
    plain_unique = plain = pi = False
    for index in result.indexes:
        if (index.schema, index.table) != (column.schema, column.table):
            continue
        if column.name not in index.columns:
            continue
        if index.primary_index:
            pi = True
        else:
            plain = True
            plain_unique = plain_unique or index.unique
    out = []
    if plain and not in_primary_key:
        out.append("unique" if plain_unique else "non-unique")
    if pi:
        out.append("PRIMARY INDEX (Teradata PI)")
    return tuple(out)


# ---------------------------------------------------------------------------
# annotations — what the pass produces
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnAnnotation:
    """One column's description line: text and its confidence."""

    text: str
    confidence: str


@dataclass(frozen=True)
class TableAnnotation:
    """One table's descriptions: the frontmatter one-liner, the ``Purpose:``
    body line, and one entry per column."""

    description: str
    purpose: str
    confidence: str
    columns: Mapping[str, ColumnAnnotation] = field(default_factory=dict)


@dataclass(frozen=True)
class DatabaseAnnotation:
    """The index.md descriptions: the one-liner and the 3-6 sentence summary."""

    description: str
    summary: str
    confidence: str


@dataclass
class Annotations:
    """Everything the annotation pass produced for one crawl result."""

    database: DatabaseAnnotation
    tables: dict[tuple[str, str], TableAnnotation] = field(default_factory=dict)
    #: Everything the sanitizer had to correct or refuse, in plain words.
    warnings: list[str] = field(default_factory=list)

    def for_table(self, schema: str, table: str) -> TableAnnotation:
        return self.tables[(schema, table)]


# ---------------------------------------------------------------------------
# annotators
# ---------------------------------------------------------------------------


class NullAnnotator:
    """No model attached. Every description becomes the explicit unknown,
    all of it ``[inferred:low]`` and therefore HITL-queued — a bundle that is
    structurally complete and honest about knowing nothing."""

    def annotate_table(self, view: TableView) -> TableAnnotation | None:
        return None

    def annotate_database(self, view: DatabaseView) -> DatabaseAnnotation | None:
        return None


#: What the model must return for one table / one database. Stated once,
#: shared by the prompt and (in spirit) the sanitizer that enforces it.
TABLE_RESPONSE_SHAPE = (
    '{"description": "<one line, under 120 characters: what the table is>",\n'
    ' "purpose": "<one to three sentences: what the table is for>",\n'
    ' "confidence": "high|medium|low",\n'
    ' "columns": {"<column>": {"text": "<one line: what the column holds>",\n'
    '                          "confidence": "high|medium|low"}}}'
)

DATABASE_RESPONSE_SHAPE = (
    '{"description": "<one line: what the database is>",\n'
    ' "summary": "<3-6 sentences: subject areas, entity families, apparent purpose>",\n'
    ' "confidence": "high|medium|low"}'
)

TABLE_INSTRUCTIONS = f"""\
You are annotating one database table for a data-discovery catalog. The
evidence below is everything the crawler measured: names, types, profile
numbers, top values, and constraint/index membership. There are no rows to
see and no further evidence to ask for. Describe only what the evidence
supports; the descriptions are used to match business fields to columns, so
say what a thing IS, plainly.

Return ONLY a JSON object of exactly this shape, no prose around it:
{TABLE_RESPONSE_SHAPE}

Every column in the evidence must appear under "columns". Where the evidence
genuinely supports no description, use the exact text
"{INSUFFICIENT}" with confidence "low".
"""

DATABASE_INSTRUCTIONS = f"""\
You are summarising one database for a data-discovery catalog, from its
annotated tables below. The summary routes searches when someone knows the
fields they want but not which database holds them: name the subject areas,
the entity families, and the apparent purpose. 3-6 sentences.

Return ONLY a JSON object of exactly this shape, no prose around it:
{DATABASE_RESPONSE_SHAPE}
"""


def table_prompt(view: TableView) -> str:
    return (
        TABLE_INSTRUCTIONS
        + "\nEvidence:\n"
        + json.dumps(view.to_obj(), indent=2, ensure_ascii=False)
    )


def database_prompt(view: DatabaseView) -> str:
    return (
        DATABASE_INSTRUCTIONS
        + "\nAnnotated tables:\n"
        + json.dumps(view.to_obj(), indent=2, ensure_ascii=False)
    )


@dataclass
class LLMAnnotator:
    """An annotator speaking strict JSON through any completion callable.

    ``complete`` is the whole integration surface: prompt in, model text out.
    A deployment wires its own client into it; this package deliberately has
    no SDK dependency and no network code. Malformed output raises
    :class:`~crawler.errors.AnnotationError`, which the pass converts into
    the explicit-unknown fallback plus a warning — a model that cannot answer
    is treated exactly like no model at all.
    """

    complete: Callable[[str], str]

    def annotate_table(self, view: TableView) -> TableAnnotation:
        obj = _parse_json(self.complete(table_prompt(view)), view.qualified)
        columns = obj.get("columns")
        if not isinstance(columns, dict):
            columns = {}
        return TableAnnotation(
            description=_text(obj.get("description")),
            purpose=_text(obj.get("purpose")),
            confidence=_text(obj.get("confidence")),
            columns={
                str(name): ColumnAnnotation(
                    text=_text(_pluck(entry, "text")),
                    confidence=_text(_pluck(entry, "confidence")),
                )
                for name, entry in columns.items()
            },
        )

    def annotate_database(self, view: DatabaseView) -> DatabaseAnnotation:
        obj = _parse_json(self.complete(database_prompt(view)), view.database)
        return DatabaseAnnotation(
            description=_text(obj.get("description")),
            summary=_text(obj.get("summary")),
            confidence=_text(obj.get("confidence")),
        )


def _pluck(entry, key):
    return entry.get(key) if isinstance(entry, dict) else None


def _text(value) -> str:
    return "" if value is None else str(value)


def _parse_json(response: str, subject: str) -> dict:
    text = str(response).strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AnnotationError(
            f"{subject}: the model did not return JSON ({exc}); "
            f"response began {text[:80]!r}"
        ) from None
    if not isinstance(obj, dict):
        raise AnnotationError(
            f"{subject}: the model returned JSON, but not an object"
        )
    return obj


# ---------------------------------------------------------------------------
# the pass
# ---------------------------------------------------------------------------


def annotate(result: CrawlResult, annotator) -> Annotations:
    """Annotate every base table, then the database, and sanitize all of it.

    The contract with specs/01 step 6 is enforced here, not hoped for: every
    table and every column ends up described, in one line, at a confidence
    the vocabulary knows — whatever the annotator did or failed to do.
    """
    annotations = Annotations(
        database=DatabaseAnnotation(INSUFFICIENT, INSUFFICIENT, LOW)
    )
    views = table_views(result)
    for view in views:
        raw = _ask(
            lambda: annotator.annotate_table(view),
            view.qualified,
            annotations.warnings,
        )
        annotations.tables[(view.schema, view.table)] = _sane_table(
            raw, view, annotations.warnings
        )

    db_view = database_view(
        result,
        [(view, annotations.tables[(view.schema, view.table)]) for view in views],
    )
    raw = _ask(
        lambda: annotator.annotate_database(db_view),
        result.database,
        annotations.warnings,
    )
    annotations.database = _sane_database(raw, annotations.warnings)
    return annotations


def _ask(call, subject: str, warnings: list[str]):
    try:
        return call()
    except AnnotationError as exc:
        warnings.append(
            f"{subject}: annotation failed and the explicit unknown stands in "
            f"({exc})"
        )
        return None


def _sane_table(raw, view: TableView, warnings: list[str]) -> TableAnnotation:
    where = view.qualified
    known = {column.name for column in view.columns}
    if raw is None:
        raw = TableAnnotation("", "", LOW)

    purpose = _line(raw.purpose)
    description = _line(raw.description)
    if not description and purpose:
        description = _first_sentence(purpose)
    if not purpose and description:
        purpose = description
    if not description:
        description = purpose = INSUFFICIENT
    confidence = _confidence(
        raw.confidence, warnings, where, described=description != INSUFFICIENT
    )

    columns = {}
    for name in sorted(set(raw.columns) - known):
        warnings.append(
            f"{where}: the annotator described a column that does not exist "
            f"({name!r}); dropped"
        )
    for column in view.columns:
        entry = raw.columns.get(column.name)
        text = _line(entry.text) if entry else ""
        if not text:
            columns[column.name] = ColumnAnnotation(INSUFFICIENT, LOW)
            continue
        columns[column.name] = ColumnAnnotation(
            text,
            _confidence(
                entry.confidence, warnings, f"{where}.{column.name}",
                described=text != INSUFFICIENT,
            ),
        )
    return TableAnnotation(
        description=description,
        purpose=purpose,
        confidence=confidence,
        columns=columns,
    )


def _sane_database(raw, warnings: list[str]) -> DatabaseAnnotation:
    if raw is None:
        return DatabaseAnnotation(INSUFFICIENT, INSUFFICIENT, LOW)
    summary = _flatten(raw.summary)
    description = _line(raw.description)
    if not summary and description:
        summary = description
    if not description and summary:
        description = _first_sentence(summary)
    if not summary:
        return DatabaseAnnotation(INSUFFICIENT, INSUFFICIENT, LOW)
    confidence = _confidence(
        raw.confidence, warnings, "database summary",
        described=summary != INSUFFICIENT,
    )
    return DatabaseAnnotation(description, summary, confidence)


def _flatten(text) -> str:
    """One line: whatever whitespace the model used, the OKF line is one."""
    return " ".join(str(text or "").split())


def _line(text) -> str:
    """One line, with any provenance tag the model helpfully added removed —
    the pass owns the tags, the model owns only the prose."""
    return _TAG_PREFIX_RE.sub("", _flatten(text))


def _first_sentence(text: str) -> str:
    return _SENTENCE_END_RE.split(text.strip(), maxsplit=1)[0]


def _confidence(value, warnings, where: str, *, described: bool) -> str:
    """The vocabulary, enforced. The explicit unknown is always ``low``; an
    out-of-vocabulary confidence demotes to ``low`` (queued, not trusted)."""
    if not described:
        return LOW
    folded = str(value or "").strip().lower()
    if folded in CONFIDENCES:
        return folded
    warnings.append(
        f"{where}: confidence {value!r} is not "
        f"{'|'.join(CONFIDENCES)}; demoted to low and queued for review"
    )
    return LOW


__all__ = [
    "INSUFFICIENT",
    "Annotations",
    "ColumnAnnotation",
    "ColumnView",
    "DatabaseAnnotation",
    "DatabaseView",
    "LLMAnnotator",
    "NullAnnotator",
    "TableAnnotation",
    "TableView",
    "annotate",
    "database_prompt",
    "database_view",
    "table_prompt",
    "table_views",
]
