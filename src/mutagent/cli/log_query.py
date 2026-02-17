"""mutagent.cli.log_query -- CLI tool for querying log files and API recordings.

Usage::

    python -m mutagent.cli.log_query sessions
    python -m mutagent.cli.log_query logs [options]
    python -m mutagent.cli.log_query api [options]
    python -m mutagent.cli.log_query api-detail <session> <index>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mutagent.runtime.log_query import LogQueryEngine


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m mutagent.cli.log_query",
        description="Query mutagent log files and API recordings.",
    )
    parser.add_argument(
        "--dir", default=".mutagent/logs",
        help="Log directory (default: .mutagent/logs)",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- sessions ---
    subparsers.add_parser("sessions", help="List all sessions")

    # --- logs ---
    logs_parser = subparsers.add_parser("logs", help="Query log entries")
    logs_parser.add_argument("-s", "--session", default="", help="Session timestamp (default: latest)")
    logs_parser.add_argument("-p", "--pattern", default="", help="Regex pattern to match messages")
    logs_parser.add_argument("-l", "--level", default="DEBUG", help="Minimum log level")
    logs_parser.add_argument("-n", "--limit", type=int, default=50, help="Max entries to return")
    logs_parser.add_argument("--from", dest="time_from", default="", help="Time range start (HH:MM:SS)")
    logs_parser.add_argument("--to", dest="time_to", default="", help="Time range end (HH:MM:SS)")

    # --- api ---
    api_parser = subparsers.add_parser("api", help="Query API call records")
    api_parser.add_argument("-s", "--session", default="", help="Session timestamp (default: latest)")
    api_parser.add_argument("-t", "--tool", default="", help="Filter by tool name")
    api_parser.add_argument("-p", "--pattern", default="", help="Regex pattern to search content")
    api_parser.add_argument("-n", "--limit", type=int, default=10, help="Max entries to return")

    # --- api-detail ---
    detail_parser = subparsers.add_parser("api-detail", help="View API call details")
    detail_parser.add_argument("session", help="Session timestamp")
    detail_parser.add_argument("index", type=int, help="API call index (0-based)")
    detail_parser.add_argument("-f", "--field", default="", help="Field path (e.g., response.content)")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    engine = LogQueryEngine(Path(args.dir))

    if args.command == "sessions":
        _cmd_sessions(engine)
    elif args.command == "logs":
        _cmd_logs(engine, args)
    elif args.command == "api":
        _cmd_api(engine, args)
    elif args.command == "api-detail":
        _cmd_api_detail(engine, args)


def _cmd_sessions(engine: LogQueryEngine) -> None:
    sessions = engine.list_sessions()
    if not sessions:
        print("No sessions found.")
        return

    # Header
    print(f"{'Session':<21s} {'Log File':<35s} {'API File':<35s} {'Logs':>6s}  {'API Calls':>9s}")
    for s in sessions:
        log_name = s.log_file.name if s.log_file else "-"
        api_name = s.api_file.name if s.api_file else "-"
        log_count = str(s.log_lines) if s.log_lines >= 0 else "-"
        api_count = str(s.api_calls) if s.api_calls >= 0 else "-"
        print(f"{s.timestamp:<21s} {log_name:<35s} {api_name:<35s} {log_count:>6s}  {api_count:>9s}")


def _cmd_logs(engine: LogQueryEngine, args: argparse.Namespace) -> None:
    entries = engine.query_logs(
        session=args.session,
        pattern=args.pattern,
        level=args.level,
        limit=args.limit,
        time_from=args.time_from,
        time_to=args.time_to,
    )
    if not entries:
        print("No matching log entries.")
        return

    for entry in entries:
        # Extract time-only from timestamp for compact display
        time_part = _extract_time_display(entry.timestamp)
        # Shorten logger name (last component)
        short_name = entry.logger_name.rsplit(".", 1)[-1] if entry.logger_name else ""
        # Truncate message to single line for display
        msg = entry.message.split("\n")[0]
        if "\n" in entry.message:
            msg += f" (+{entry.message.count(chr(10))} lines)"
        print(f"{entry.line_no:>4d} | {time_part} {entry.level:<8s} {short_name:<20s} - {msg}")


def _cmd_api(engine: LogQueryEngine, args: argparse.Namespace) -> None:
    calls = engine.query_api(
        session=args.session,
        tool_name=args.tool,
        pattern=args.pattern,
        limit=args.limit,
    )
    if not calls:
        print("No matching API records.")
        return

    for call in calls:
        # Extract time from ISO timestamp
        time_part = _extract_iso_time(call.timestamp)
        print(f"#{call.index:02d} | {time_part} | {call.summary}")


def _cmd_api_detail(engine: LogQueryEngine, args: argparse.Namespace) -> None:
    result = engine.get_api_detail(
        session=args.session,
        call_index=args.index,
        field_path=args.field,
    )
    if isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


def _extract_time_display(timestamp: str) -> str:
    """Extract HH:MM:SS from log timestamp '2026-02-17 08:59:24,301'."""
    parts = timestamp.split(" ")
    if len(parts) >= 2:
        return parts[1].split(",")[0]
    return timestamp


def _extract_iso_time(ts: str) -> str:
    """Extract HH:MM:SS from ISO timestamp."""
    if "T" in ts:
        time_part = ts.split("T")[1]
        return time_part[:8]
    return ts[:8]


if __name__ == "__main__":
    main()
