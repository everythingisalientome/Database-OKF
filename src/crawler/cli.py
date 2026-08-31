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
every issue. ``--diff`` makes the emit a *refresh*: the previous bundle at
that path is snapshotted before being overwritten, the fresh bundle is
diffed against it (:mod:`crawler.diff`), the change report and stale
manifest land under ``<root>/refresh/<database>/``, and fingerprint
payloads orphaned by the overwrite are removed. The first refresh of an
empty path is a recorded baseline, not an error. Two annotators: ``none`` (no model attached — every description
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
from .diff import DiffTolerance, diff_snapshots, snapshot
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
        "--diff",
        action="store_true",
        help="with --emit: diff the fresh bundle against the previous one at "
        "the same path, write the change report and stale manifest under "
        "OKF_ROOT/refresh/<database>/, and remove fingerprint payloads the "
        "overwrite orphaned",
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
    if args.diff and args.emit is None:
        parser.error("--diff needs a bundle to refresh (--emit)")

    try:
        config, result = _load_or_crawl(args)
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
    tolerance = config.refresh if config is not None else DiffTolerance()
    return _emit_bundle(
        result, args.emit, args.annotator, diff=args.diff, tolerance=tolerance
    )


def _load_or_crawl(args) -> tuple[CrawlConfig | None, CrawlResult]:
    if args.from_crawl is not None:
        return None, CrawlResult.from_obj(
            json.loads(args.from_crawl.read_text(encoding="utf-8"))
        )
    config = CrawlConfig.load(args.config)
    connection = connect(config)
    try:
        return config, crawl(
            connection, config, today=args.date, measure=args.measure
        )
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


def _emit_bundle(
    result: CrawlResult,
    okf_root: Path,
    annotator_name: str,
    *,
    diff: bool = False,
    tolerance: DiffTolerance | None = None,
) -> int:
    try:
        annotator = ANNOTATORS[annotator_name]()
    except CrawlerError as exc:
        print(f"emit failed: {exc}", file=sys.stderr)
        return 1

    # The refresh overwrites the previous bundle in place, so anything the
    # diff needs from it has to be read now or never.
    previous = None
    bundle_root = Path(okf_root) / "db" / result.database
    if diff and (bundle_root / "index.md").is_file():
        previous = snapshot(read_database_bundle(bundle_root))

    annotations = annotate(result, annotator)
    emission = emit(result, annotations, okf_root)
    for warning in [*annotations.warnings, *emission.warnings]:
        print(f"  warning: {warning}", file=sys.stderr)

    bundle = read_database_bundle(emission.root)
    orphans_removed = []
    if diff:
        # A dropped or renamed column leaves its previous payload behind;
        # the refresh is the one place that may clean up, because it is the
        # one place that knows the file belonged to the previous bundle.
        for orphan in bundle.orphan_fingerprints():
            orphans_removed.append(orphan.relative_to(bundle.root).as_posix())
            orphan.unlink()
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

    if diff:
        _write_refresh(result, okf_root, bundle, previous, tolerance,
                       orphans_removed)
    return 1 if errors else 0


def _write_refresh(
    result, okf_root, bundle, previous, tolerance, orphans_removed
) -> None:
    refresh = diff_snapshots(
        previous,
        snapshot(bundle),
        tolerance,
        orphans_removed=tuple(orphans_removed),
    )
    refresh_dir = Path(okf_root) / "refresh" / result.database
    refresh_dir.mkdir(parents=True, exist_ok=True)
    report_path = refresh_dir / "report.md"
    manifest_path = refresh_dir / "stale.json"
    report_path.write_text(refresh.render_report(), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(refresh.to_manifest(), indent=2) + "\n", encoding="utf-8"
    )

    if refresh.baseline:
        print(
            "refresh diff: baseline — no previous bundle, nothing invalidated",
            file=sys.stderr,
        )
    else:
        print(
            f"refresh diff vs {refresh.previous_date} "
            f"({refresh.previous_completeness}): "
            f"{len(refresh.added)} added, {len(refresh.dropped)} dropped, "
            f"{len(refresh.changed)} changed, "
            f"{len(refresh.annotation_only)} annotation-only; "
            f"{len(refresh.invalidated)} artifacts invalidated",
            file=sys.stderr,
        )
        for change in refresh.invalidated:
            reasons = "; ".join(change.invalidating_reasons) or change.change
            print(f"  stale: {change.table} — {reasons}", file=sys.stderr)
    for orphan in orphans_removed:
        print(f"  removed orphaned payload: {orphan}", file=sys.stderr)
    print(f"  report:   {report_path.as_posix()}", file=sys.stderr)
    print(f"  manifest: {manifest_path.as_posix()}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
