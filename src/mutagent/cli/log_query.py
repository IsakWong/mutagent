"""mutagent.cli.log_query -- CLI tool for querying log files and API recordings.

Usage::

    python -m mutagent.cli.log_query sessions
    python -m mutagent.cli.log_query logs [options]
    python -m mutagent.cli.log_query api [options]
    python -m mutagent.cli.log_query api-detail <session> <index>
    python -m mutagent.cli.log_query tools [options]
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
    api_parser.add_argument("-v", "--verbose", action="store_true", help="Show tool call details")

    # --- api-detail ---
    detail_parser = subparsers.add_parser("api-detail", help="View API call details")
    detail_parser.add_argument("session", help="Session timestamp")
    detail_parser.add_argument("index", type=int, help="API call index (0-based)")
    detail_parser.add_argument("-f", "--field", default="", help="Field path (e.g., response.content)")

    # --- tools ---
    tools_parser = subparsers.add_parser("tools", help="List tool calls from a session")
    tools_parser.add_argument("-s", "--session", default="", help="Session timestamp (default: latest)")
    tools_parser.add_argument("-t", "--tool", default="", help="Filter by tool name")
    tools_parser.add_argument("--errors", action="store_true", help="Show only failed tool calls")
    tools_parser.add_argument("-n", "--limit", type=int, default=0, help="Max entries (0=unlimited)")

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
    elif args.command == "tools":
        _cmd_tools(engine, args)


def _cmd_sessions(engine: LogQueryEngine) -> None:
    sessions = engine.list_sessions()
    if not sessions:
        print("No sessions found.")
        return

    # Header
    print(
        f"{'Session':<18s} {'Logs':>6s}  {'API':>4s}"
        f"  {'Tools(ok/err)':>13s}  {'Duration':>8s}"
    )
    for s in sessions:
        log_count = str(s.log_lines) if s.log_lines >= 0 else "-"
        api_count = str(s.api_calls) if s.api_calls >= 0 else "-"

        if s.tool_ok_count >= 0 or s.tool_err_count >= 0:
            ok = max(0, s.tool_ok_count)
            err = max(0, s.tool_err_count)
            tools_str = f"{ok}/{err}"
        else:
            tools_str = "-"

        if s.duration_seconds >= 0:
            duration_str = _format_duration(s.duration_seconds)
        else:
            duration_str = "-"

        print(
            f"{s.timestamp:<18s} {log_count:>6s}  {api_count:>4s}"
            f"  {tools_str:>13s}  {duration_str:>8s}"
        )


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
        verbose=args.verbose,
    )
    if not calls:
        print("No matching API records.")
        return

    for call in calls:
        # Extract time from ISO timestamp
        time_part = _extract_iso_time(call.timestamp)
        print(f"#{call.index:02d} | {time_part} | {call.summary}")
        # P1: verbose lines
        for vline in call.verbose_lines:
            print(vline)


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


def _cmd_tools(engine: LogQueryEngine, args: argparse.Namespace) -> None:
    tool_calls = engine.query_tools(
        session=args.session,
        tool_name=args.tool,
        errors_only=args.errors,
        limit=args.limit,
    )
    if not tool_calls:
        print("No tool calls found.")
        return

    for tc in tool_calls:
        summary = tc.input_summary
        if summary:
            line = f" #{tc.index:02d} {tc.tool_name}({summary})"
        else:
            line = f" #{tc.index:02d} {tc.tool_name}()"
        result = tc.result_summary if tc.result_summary else "?"
        print(f"{line} \u2192 {result}")


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


def _format_duration(seconds: float) -> str:
    """Format duration in seconds as human-readable string."""
    if seconds < 0:
        return "-"
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    minutes = total // 60
    secs = total % 60
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h{mins:02d}m"


if __name__ == "__main__":
    main()
