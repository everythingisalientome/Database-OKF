"""``python -m crawler`` — crawl a database, and/or emit its OKF bundle.

    # Tier A crawl, JSON result
    python -m crawler --config rig/config/chinook-postgres.json --out crawl.json
    # ... with the measuring pass
    python -m crawler --config ... --measure --out crawl.json
    # crawl, annotate, emit and validate the bundle in one run
    python -m crawler --config ... --measure --emit okf
    # annotate and emit from a saved crawl result — no database in the room
    python -m crawler --from-crawl crawl.json --emit okf

The summary goes to stderr and the JSON to stdout (or ``--out``), so the
command pipes cleanly inside a job. ``--date`` pins the crawl date, which is
what makes two runs of an unchanged database produce byte-identical output —
the property the refresh diff depends on.

Tier A alone is the default. ``--measure`` adds the Tier B/C pass, which is
the half that reads data: it is opt-in because it costs table scans, and
because a crawl that only wants the inventory should not pay for them.

``--emit`` runs the annotation pass and writes the bundle under
``<root>/db/<database>``, then validates it with the okf package and prints
every issue. Two annotators: ``none`` (no model attached — every description
the explicit unknown, all of it HITL-queued) and ``llm`` (an
OpenAI-compatible endpoint via :mod:`crawler.llm`, configured entirely from
``$CRAWLER_LLM_MODEL`` / ``$CRAWLER_LLM_BASE_URL`` / ``$CRAWLER_LLM_API_KEY``
and needing the ``okf[llm]`` extra installed). Anything else — another wire
protocol, another auth scheme — wires its own callable through
:class:`crawler.annotate.LLMAnnotator` in code.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from okf import read_database_bundle, validate_bundle

from . import llm
from .annotate import NullAnnotator, annotate
from .config import CrawlConfig
from .connect import connect
from .crawl import crawl
from .emit import emit
from .errors import CrawlerError
from .results import CrawlResult


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m crawler",
        description="Run the step 1 schema crawl for one database, and/or "
        "emit its OKF bundle.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--config", type=Path, help="crawl config JSON: connect and crawl"
    )
    source.add_argument(
        "--from-crawl",
        type=Path,
        metavar="CRAWL_JSON",
        help="a saved crawl result to annotate and emit; no connection made",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write the crawl result here (default: stdout; --config only)",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="crawl date to record, YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--measure",
        action="store_true",
        help="also run the Tier B/C measuring pass (profiles, top-N, "
        "fingerprints); costs table scans",
    )
    parser.add_argument(
        "--emit",
        type=Path,
        default=None,
        metavar="OKF_ROOT",
        help="annotate and write the OKF bundle under OKF_ROOT/db/<database>, "
        "then validate it",
    )
    parser.add_argument(
        "--annotator",
        choices=["none", "llm"],
        default="none",
        help="how --emit gets its descriptions. 'none' emits the explicit "
        "unknown everywhere (HITL-queued); 'llm' calls the OpenAI-compatible "
        "endpoint named by $CRAWLER_LLM_MODEL / $CRAWLER_LLM_BASE_URL / "
        "$CRAWLER_LLM_API_KEY (needs the okf[llm] extra)",
    )
    parser.add_argument(
        "--indent", type=int, default=2, help="JSON indent (0 for compact)"
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.from_crawl is not None:
        for flag, name in ((args.measure, "--measure"), (args.date, "--date"),
                           (args.out, "--out")):
            if flag:
                parser.error(f"{name} needs a live crawl (--config)")
        if args.emit is None:
            parser.error("--from-crawl does nothing without --emit")

    try:
        result = _load_or_crawl(args)
    except CrawlerError as exc:
        print(f"crawl failed: {exc}", file=sys.stderr)
        return 1

    if args.config is not None:
        _write_result(result, args)
    _print_crawl_summary(result)

    if args.emit is None:
        # An INCOMPLETE or UNVERIFIED crawl still succeeded: the flag on the
        # result is the signal, and a job that treats it as a failure would
        # retry a grant gap forever. Only an error exits non-zero.
        return 0
    return _emit_bundle(result, args.emit, args.annotator)


def _load_or_crawl(args) -> CrawlResult:
    if args.from_crawl is not None:
        return CrawlResult.from_obj(
            json.loads(args.from_crawl.read_text(encoding="utf-8"))
        )
    config = CrawlConfig.load(args.config)
    connection = connect(config)
    try:
        return crawl(connection, config, today=args.date, measure=args.measure)
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()


def _write_result(result: CrawlResult, args) -> None:
    text = json.dumps(result.to_obj(), indent=args.indent or None, sort_keys=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    elif args.emit is None:
        # With --emit and no --out, the bundle is the output; the JSON on
        # stdout would just be noise between the two summaries.
        print(text)


def _print_crawl_summary(result: CrawlResult) -> None:
    summary = (
        f"{result.database} ({result.engine}): "
        f"{len(result.base_tables)} base tables, {len(result.columns)} columns, "
        f"{len(result.constraints)} constraints, {len(result.indexes)} indexes"
    )
    if result.measured:
        fingerprints = sum(
            1 for p in result.column_profiles if p.fingerprint is not None
        )
        top_n = sum(1 for p in result.column_profiles if p.top_values)
        summary += (
            f", {len(result.column_profiles)} profiles, "
            f"{top_n} top-N, {fingerprints} fingerprints"
        )
    print(f"{summary}, {result.completeness}", file=sys.stderr)
    for warning in result.warnings:
        print(f"  warning: {warning}", file=sys.stderr)


#: The annotators the CLI can construct by name.
ANNOTATORS = {"none": NullAnnotator, "llm": llm.annotator_from_env}


def _emit_bundle(result: CrawlResult, okf_root: Path, annotator_name: str) -> int:
    try:
        annotator = ANNOTATORS[annotator_name]()
    except CrawlerError as exc:
        print(f"emit failed: {exc}", file=sys.stderr)
        return 1
    annotations = annotate(result, annotator)
    emission = emit(result, annotations, okf_root)
    for warning in [*annotations.warnings, *emission.warnings]:
        print(f"  warning: {warning}", file=sys.stderr)

    bundle = read_database_bundle(emission.root)
    issues = validate_bundle(bundle)
    errors = [issue for issue in issues if issue.is_error]
    queued = len(bundle.needs_review)
    print(
        f"emitted {emission.root.as_posix()}: "
        f"{len(emission.documents)} documents, "
        f"{len(emission.fingerprints)} fingerprint payloads, "
        f"{queued} lines HITL-queued",
        file=sys.stderr,
    )
    for issue in issues:
        print(f"  {issue}", file=sys.stderr)
    verdict = (
        "valid"
        if not errors
        else f"INVALID ({len(errors)} errors) — this is an emitter bug"
    )
    print(f"okf validator: {verdict}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
