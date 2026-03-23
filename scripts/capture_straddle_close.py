#!/usr/bin/env python3
"""
Persist the SPX straddle monitor daily close snapshot.

The script is intended to be run by a scheduler every few minutes. It exits
cleanly outside the 4:00 PM ET close-capture window unless --force is used.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from fastapi import HTTPException

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from web.server.main import _capture_straddle_daily_close_snapshot


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Persist the SPX straddle daily close snapshot.",
        epilog=(
            "Examples:\n"
            "  %(prog)s\n"
            "  %(prog)s --force\n"
            "  %(prog)s --rows 6 --force"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=None,
        help="Number of near-term expiries to capture (defaults to the monitor route default).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Capture immediately even outside the 4:00 PM ET close window.",
    )
    args = parser.parse_args(argv)
    if args.rows is not None and args.rows < 1:
        parser.error("--rows must be >= 1")
    return args


def main(argv=None):
    args = parse_args(argv)
    now_utc = datetime.now(timezone.utc)
    try:
        result = _capture_straddle_daily_close_snapshot(
            row_limit=args.rows,
            now_utc=now_utc,
            force=args.force,
        )
    except HTTPException as exc:
        message = exc.detail if hasattr(exc, "detail") else str(exc)
        print(f"Close capture failed: {message}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Close capture failed: {exc}", file=sys.stderr)
        return 1

    status = result.get("status")
    if status == "skipped":
        print(
            f"Skipped close capture for {result.get('session_date')}: "
            f"{result.get('reason')} at {result.get('captured_at')}"
        )
        return 0

    expirations = ", ".join(result.get("expirations", [])) or "none"
    print(
        f"Captured {result.get('rows_persisted', 0)} close rows for {result.get('session_date')} "
        f"at {result.get('captured_at')} ({expirations})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
