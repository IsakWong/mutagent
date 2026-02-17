"""mutagent.runtime.log_query -- Parse and query log files and API recordings."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mutagent.runtime.log_store import LogEntry, LogStore

# Regex to parse a standard log line:
# 2026-02-17 08:59:24,301 INFO     mutagent.builtins.main_impl - message
_LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+"  # timestamp
    r"(\w+)\s+"                                             # level
    r"(\S+)\s+-\s+"                                         # logger_name -
    r"(.*)$"                                                # message
)

# Regex to detect the start of a new log entry (timestamp at line start)
_TIMESTAMP_START_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

# Session timestamp pattern in filenames
_SESSION_TS_RE = re.compile(r"(\d{8}_\d{6})")

# Level name → numeric value for comparison
_LEVEL_VALUES = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


@dataclass
class SessionInfo:
    """Information about a single session."""

    timestamp: str  # "20260217_085924"
    log_file: Path | None = None
    api_file: Path | None = None
    log_lines: int = -1  # -1 = not counted
    api_calls: int = -1  # -1 = not counted


@dataclass
class LogLine:
    """A parsed log entry from a file."""

    line_no: int  # file line number (1-based)
    timestamp: str  # "2026-02-17 08:59:24,301"
    level: str  # "INFO"
    logger_name: str  # "mutagent.builtins.agent_impl"
    message: str  # message content (continuation lines merged, \t prefix removed)
    raw: str = ""  # original text (including continuation lines)


@dataclass
class ApiCall:
    """A parsed API call record."""

    index: int  # call sequence number (0-based, 0=session)
    type: str  # "session" | "call"
    timestamp: str  # ISO format
    summary: str  # e.g., "user → tool_use (3 tools)"
    data: dict = field(default_factory=dict)


class LogQueryEngine:
    """Parse and query log files and API recording files on disk."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = Path(log_dir)

    def list_sessions(self) -> list[SessionInfo]:
        """List all sessions found in the log directory.

        Scans for ``TIMESTAMP-log.log`` and ``TIMESTAMP-api.jsonl`` files,
        extracts session timestamps, and pairs them together.
        """
        if not self._log_dir.is_dir():
            return []

        sessions: dict[str, SessionInfo] = {}

        for path in self._log_dir.iterdir():
            m = _SESSION_TS_RE.search(path.name)
            if m is None:
                continue
            ts = m.group(1)
            if ts not in sessions:
                sessions[ts] = SessionInfo(timestamp=ts)

            if path.name.endswith("-log.log"):
                sessions[ts].log_file = path
                sessions[ts].log_lines = _count_lines(path)
            elif path.name.endswith("-api.jsonl"):
                sessions[ts].api_file = path
                n = _count_lines(path)
                sessions[ts].api_calls = max(0, n - 1) if n >= 0 else -1

        return sorted(sessions.values(), key=lambda s: s.timestamp)

    def query_logs(
        self,
        session: str = "",
        pattern: str = "",
        level: str = "DEBUG",
        limit: int = 50,
        time_from: str = "",
        time_to: str = "",
    ) -> list[LogLine]:
        """Query log entries from a session's log file.

        Args:
            session: Session timestamp. Empty string = latest session.
            pattern: Regex to match against message content.
            level: Minimum log level (DEBUG/INFO/WARNING/ERROR).
            limit: Maximum number of entries to return.
            time_from: Time range start (HH:MM:SS).
            time_to: Time range end (HH:MM:SS).

        Returns:
            Matching log entries (in file order).
        """
        log_file = self._resolve_log_file(session)
        if log_file is None:
            return []

        min_level = _LEVEL_VALUES.get(level.upper(), logging.DEBUG)
        compiled = re.compile(pattern) if pattern else None

        results: list[LogLine] = []
        for entry in _iter_log_entries(log_file):
            # Level filter
            entry_level = _LEVEL_VALUES.get(entry.level, logging.DEBUG)
            if entry_level < min_level:
                continue

            # Time filter
            if time_from or time_to:
                entry_time = _extract_time(entry.timestamp)
                if entry_time:
                    if time_from and entry_time < time_from:
                        continue
                    if time_to and entry_time > time_to:
                        continue

            # Pattern filter
            if compiled and not compiled.search(entry.message):
                continue

            results.append(entry)
            if len(results) >= limit:
                break

        return results

    def load_to_store(self, session: str) -> LogStore:
        """Parse a log file and load it into a LogStore object.

        Args:
            session: Session timestamp.

        Returns:
            A LogStore populated with entries from the log file.
        """
        from datetime import datetime

        store = LogStore()
        log_file = self._resolve_log_file(session)
        if log_file is None:
            return store

        for entry in _iter_log_entries(log_file):
            # Parse timestamp string to float
            try:
                dt = datetime.strptime(entry.timestamp, "%Y-%m-%d %H:%M:%S,%f")
                ts = dt.timestamp()
            except ValueError:
                ts = 0.0

            store.append(LogEntry(
                timestamp=ts,
                level=entry.level,
                logger_name=entry.logger_name,
                message=entry.message,
            ))

        return store

    def query_api(
        self,
        session: str = "",
        call_index: int | None = None,
        tool_name: str = "",
        pattern: str = "",
        limit: int = 10,
    ) -> list[ApiCall]:
        """Query API call records from a session's JSONL file.

        Args:
            session: Session timestamp. Empty string = latest session.
            call_index: If specified, return only this specific call.
            tool_name: Filter by tool name in response content.
            pattern: Regex to search in response/input content.
            limit: Maximum number of entries to return.

        Returns:
            Matching API call records.
        """
        api_file = self._resolve_api_file(session)
        if api_file is None:
            return []

        compiled = re.compile(pattern) if pattern else None
        results: list[ApiCall] = []

        for idx, line in enumerate(_iter_file_lines(api_file)):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            call = ApiCall(
                index=idx,
                type=data.get("type", "unknown"),
                timestamp=data.get("ts", ""),
                summary=_make_api_summary(data),
                data=data,
            )

            # Index filter
            if call_index is not None and idx != call_index:
                continue

            # Tool name filter
            if tool_name and not _api_has_tool(data, tool_name):
                continue

            # Pattern filter
            if compiled:
                text = json.dumps(data, ensure_ascii=False)
                if not compiled.search(text):
                    continue

            results.append(call)
            if len(results) >= limit:
                break

        return results

    def get_api_detail(
        self,
        session: str,
        call_index: int,
        field_path: str = "",
    ) -> dict | str:
        """Get detailed content of a specific API call.

        Args:
            session: Session timestamp.
            call_index: The call sequence number (0-based).
            field_path: Dot-separated field path (e.g., "response.content").
                Empty string returns the full record.

        Returns:
            The full record dict or the extracted field value.
        """
        results = self.query_api(session=session, call_index=call_index, limit=1)
        if not results:
            return {"error": f"API call #{call_index} not found"}

        data = results[0].data
        if not field_path:
            return data

        return _extract_field(data, field_path)

    # --- Private helpers ---

    def _resolve_log_file(self, session: str) -> Path | None:
        """Find the log file for a session."""
        if session:
            path = self._log_dir / f"{session}-log.log"
            return path if path.is_file() else None

        return self._find_latest_file("-log.log")

    def _resolve_api_file(self, session: str) -> Path | None:
        """Find the API file for a session."""
        if session:
            path = self._log_dir / f"{session}-api.jsonl"
            return path if path.is_file() else None

        return self._find_latest_file("-api.jsonl")

    def _find_latest_file(self, suffix: str) -> Path | None:
        """Find the latest file matching suffix by filename sort."""
        if not self._log_dir.is_dir():
            return None

        candidates = sorted(
            (p for p in self._log_dir.iterdir() if p.name.endswith(suffix)),
            key=lambda p: p.name,
            reverse=True,
        )
        return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# File parsing helpers
# ---------------------------------------------------------------------------


def _iter_file_lines(path: Path):
    """Yield non-empty lines from a file."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip("\n\r")
            if stripped:
                yield stripped


def _iter_log_entries(path: Path):
    """Parse a log file and yield LogLine objects.

    Handles continuation lines (lines starting with ``\\t``).
    """
    current_line_no = 0
    current_entry: LogLine | None = None
    raw_lines: list[str] = []

    with open(path, encoding="utf-8") as f:
        for file_line_no, raw_line in enumerate(f, start=1):
            raw_line = raw_line.rstrip("\n\r")

            if _TIMESTAMP_START_RE.match(raw_line):
                # This is a new log entry — yield previous if exists
                if current_entry is not None:
                    current_entry.raw = "\n".join(raw_lines)
                    yield current_entry

                m = _LOG_LINE_RE.match(raw_line)
                if m:
                    current_entry = LogLine(
                        line_no=file_line_no,
                        timestamp=m.group(1),
                        level=m.group(2),
                        logger_name=m.group(3),
                        message=m.group(4),
                    )
                else:
                    # Line looks like a timestamp start but doesn't match full pattern
                    current_entry = LogLine(
                        line_no=file_line_no,
                        timestamp="",
                        level="",
                        logger_name="",
                        message=raw_line,
                    )
                current_line_no = file_line_no
                raw_lines = [raw_line]
            else:
                # Continuation line (starts with \t or other non-timestamp text)
                if current_entry is not None:
                    # Remove leading tab if present
                    cont = raw_line.lstrip("\t") if raw_line.startswith("\t") else raw_line
                    current_entry.message += "\n" + cont
                    raw_lines.append(raw_line)

    # Yield the last entry
    if current_entry is not None:
        current_entry.raw = "\n".join(raw_lines)
        yield current_entry


def _count_lines(path: Path) -> int:
    """Count lines in a file."""
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except OSError:
        return -1


def _extract_time(timestamp: str) -> str:
    """Extract HH:MM:SS from a log timestamp string."""
    # "2026-02-17 08:59:24,301" → "08:59:24"
    parts = timestamp.split(" ")
    if len(parts) >= 2:
        time_part = parts[1].split(",")[0]
        return time_part
    return ""


def _make_api_summary(data: dict) -> str:
    """Generate a human-readable summary of an API call record."""
    record_type = data.get("type", "unknown")

    if record_type == "session":
        model = data.get("model", "?")
        tools = data.get("tools", [])
        return f"session (model={model}, tools={len(tools)})"

    if record_type != "call":
        return record_type

    # Determine input summary
    input_data = data.get("input", data.get("messages", []))
    if isinstance(input_data, dict):
        input_summary = _summarize_message(input_data)
    elif isinstance(input_data, list) and input_data:
        last = input_data[-1] if input_data else {}
        input_summary = _summarize_message(last)
    else:
        input_summary = "?"

    # Determine response summary
    response = data.get("response", {})
    stop_reason = response.get("stop_reason", "?")
    content_blocks = response.get("content", [])
    tool_uses = [b for b in content_blocks if isinstance(b, dict) and b.get("type") == "tool_use"]

    if tool_uses:
        response_summary = f"tool_use ({len(tool_uses)} tools)"
    else:
        response_summary = stop_reason

    # Duration and tokens
    duration = data.get("duration_ms", 0)
    usage = data.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)

    parts = [f"{input_summary} → {response_summary}"]
    if duration:
        parts.append(f"{duration}ms")
    if input_tokens or output_tokens:
        parts.append(f"{input_tokens}→{output_tokens} tokens")

    return " | ".join(parts)


def _summarize_message(msg: dict) -> str:
    """Create a short summary of a message."""
    content = msg.get("content", "")
    if isinstance(content, str):
        preview = content[:30]
        if len(content) > 30:
            preview += "..."
        return f'{msg.get("role", "?")}: "{preview}"'
    elif isinstance(content, list):
        # tool_result or multi-block content
        types = [b.get("type", "?") for b in content if isinstance(b, dict)]
        return f'{msg.get("role", "?")}: [{", ".join(types)}]'
    return msg.get("role", "?")


def _api_has_tool(data: dict, tool_name: str) -> bool:
    """Check if an API record references a specific tool name."""
    text = json.dumps(data, ensure_ascii=False)
    return tool_name in text


def _extract_field(data: Any, field_path: str) -> Any:
    """Extract a nested field from a dict using dot-separated path.

    Supports array indexing with brackets: ``response.content[0].type``.
    """
    parts = re.split(r"\.(?![^\[]*\])", field_path)
    current = data

    for part in parts:
        # Check for array indexing: "content[0]"
        bracket_match = re.match(r"^(\w+)\[(\d+)\]$", part)
        if bracket_match:
            key = bracket_match.group(1)
            idx = int(bracket_match.group(2))
            if isinstance(current, dict) and key in current:
                current = current[key]
                if isinstance(current, list) and 0 <= idx < len(current):
                    current = current[idx]
                else:
                    return f"Index {idx} out of range"
            else:
                return f"Field '{key}' not found"
        else:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return f"Field '{part}' not found"

    return current
