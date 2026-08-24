"""``python -m crawler`` — run one Tier A crawl, write one JSON result.

    python -m crawler --config rig/config/chinook-postgres.json --out crawl.json
    python -m crawler --config ... --measure --out crawl.json

The summary goes to stderr and the JSON to stdout (or ``--out``), so the
command pipes cleanly inside a job. ``--date`` pins the crawl date, which is
what makes two runs of an unchanged database produce byte-identical output —
the property the refresh diff depends on.

Tier A alone is the default. ``--measure`` adds the Tier B/C pass, which is
the half that reads data: it is opt-in because it costs table scans, and
because a crawl that only wants the inventory should not pay for them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .config import CrawlConfig
from .connect import connect
from .crawl import crawl
from .errors import CrawlerError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m crawler",
        description="Run the Tier A schema crawl for one database.",
    )
    parser.add_argument(
        "--config", required=True, type=Path, help="crawl config JSON"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write the crawl result here (default: stdout)",
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
        "--indent", type=int, default=2, help="JSON indent (0 for compact)"
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = CrawlConfig.load(args.config)
        connection = connect(config)
        try:
            result = crawl(
                connection, config, today=args.date, measure=args.measure
            )
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
    except CrawlerError as exc:
        print(f"crawl failed: {exc}", file=sys.stderr)
        return 1

    text = json.dumps(result.to_obj(), indent=args.indent or None, sort_keys=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)

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
    # An INCOMPLETE or UNVERIFIED crawl still succeeded: the flag on the
    # result is the signal, and a job that treats it as a failure would
    # retry a grant gap forever. Only an error exits non-zero.
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
