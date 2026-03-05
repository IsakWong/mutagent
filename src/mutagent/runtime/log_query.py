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

# File naming patterns:
# - server-YYYYMMDD_HHMMSS.log          (server log)
# - session-YYYYMMDD_HHMMSS-HEXID.log   (session log)
# - session-YYYYMMDD_HHMMSS-HEXID-api.jsonl (session API)
# - YYYYMMDD_HHMMSS.log                 (mutagent standalone log)
# - YYYYMMDD_HHMMSS-api.jsonl           (mutagent standalone API)
_SESSION_FILE_RE = re.compile(
    r"^((?:server-|session-)?\d{8}_\d{6}(?:-[0-9a-f]+)?)"  # prefix
    r"(?:\.log|-api\.jsonl)$"                                # suffix
)

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
    tool_ok_count: int = -1  # -1 = not counted
    tool_err_count: int = -1  # -1 = not counted
    duration_seconds: float = -1  # -1 = not computed


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
    verbose_lines: list[str] = field(default_factory=list)  # extra detail lines for -v


@dataclass
class ToolCallInfo:
    """Information about a single tool call extracted from API records."""

    index: int  # tool call sequence number (1-based, across all API calls)
    api_index: int  # which API call record this belongs to
    tool_name: str
    input_summary: str  # e.g., 'module_path="foo", source="...300 lines..."'
    is_error: bool = False
    result_summary: str = ""  # e.g., "ok (1308 chars)" or "error: SyntaxError line 152"
    result_length: int = 0


class LogQueryEngine:
    """Parse and query log files and API recording files on disk."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = Path(log_dir)

    def list_sessions(self) -> list[SessionInfo]:
        """List all sessions found in the log directory.

        Scans for ``.log``, ``-log.log``, and ``-api.jsonl`` files,
        extracts session identifiers from filename prefixes, and pairs them.
        Also computes tool call statistics and session duration from API JSONL files.
        """
        if not self._log_dir.is_dir():
            return []

        sessions: dict[str, SessionInfo] = {}

        for path in self._log_dir.iterdir():
            session_id = _extract_session_prefix(path.name)
            if session_id is None:
                continue
            if session_id not in sessions:
                sessions[session_id] = SessionInfo(timestamp=session_id)

            if path.name.endswith("-api.jsonl"):
                sessions[session_id].api_file = path
                n = _count_lines(path)
                sessions[session_id].api_calls = max(0, n - 1) if n >= 0 else -1
            elif path.name.endswith(".log"):
                sessions[session_id].log_file = path
                sessions[session_id].log_lines = _count_lines(path)

        # Compute tool statistics and duration for each session with an API file
        for info in sessions.values():
            if info.api_file is not None:
                _compute_session_stats(info)

        return sorted(sessions.values(), key=lambda s: s.timestamp)

    def query_tools(
        self,
        session: str = "",
        tool_name: str = "",
        errors_only: bool = False,
        limit: int = 0,
    ) -> list[ToolCallInfo]:
        """Extract tool calls from a session's API JSONL file.

        Args:
            session: Session timestamp. Empty string = latest session.
            tool_name: Filter by tool name (exact match).
            errors_only: If True, only return failed tool calls.
            limit: Maximum number of entries (0 = unlimited).

        Returns:
            List of ToolCallInfo objects in call order.
        """
        api_file = self._resolve_api_file(session)
        if api_file is None:
            return []

        records = _load_api_records(api_file)
        tool_calls = _extract_tool_calls(records)

        # Apply filters
        results: list[ToolCallInfo] = []
        for tc in tool_calls:
            if tool_name and tc.tool_name != tool_name:
                continue
            if errors_only and not tc.is_error:
                continue
            results.append(tc)
            if limit > 0 and len(results) >= limit:
                break

        return results

    def query_logs(
        self,
        session: str = "",
        pattern: str = "",
        level: str = "DEBUG",
        limit: int = 50,
        time_from: str = "",
        time_to: str = "",
        logger_name: str = "",
    ) -> list[LogLine]:
        """Query log entries from a session's log file.

        Args:
            session: Session timestamp. Empty string = latest session.
            pattern: Regex to match against message content.
            level: Minimum log level (DEBUG/INFO/WARNING/ERROR).
            limit: Maximum number of entries to return.
            time_from: Time range start (HH:MM:SS).
            time_to: Time range end (HH:MM:SS).
            logger_name: Filter by logger name (prefix match).

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

            # Logger name filter (prefix match)
            if logger_name:
                if entry.logger_name != logger_name and not entry.logger_name.startswith(logger_name + "."):
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
        verbose: bool = False,
    ) -> list[ApiCall]:
        """Query API call records from a session's JSONL file.

        Args:
            session: Session timestamp. Empty string = latest session.
            call_index: If specified, return only this specific call.
            tool_name: Filter by tool name in response content.
            pattern: Regex to search in response/input content.
            limit: Maximum number of entries to return.
            verbose: If True, include tool call detail lines.

        Returns:
            Matching API call records.
        """
        api_file = self._resolve_api_file(session)
        if api_file is None:
            return []

        compiled = re.compile(pattern) if pattern else None
        results: list[ApiCall] = []

        # For P3 (tool_result association), track previous record's tool_use id→name
        prev_tool_map: dict[str, str] = {}  # tool_use_id → tool_name

        for idx, line in enumerate(_iter_file_lines(api_file)):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            call = ApiCall(
                index=idx,
                type=data.get("type", "unknown"),
                timestamp=data.get("ts", ""),
                summary=_make_api_summary(data, prev_tool_map),
                data=data,
            )

            # Generate verbose lines (P1)
            if verbose:
                call.verbose_lines = _make_verbose_lines(data)

            # Update prev_tool_map for next iteration (P3)
            prev_tool_map = _build_tool_use_map(data)

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

    def _resolve_session_file(self, session: str, suffix: str) -> Path | None:
        """Find a session file by exact or partial session ID match.

        Tries exact match first (``{session}{suffix}``), then falls back to
        substring match against all files ending with *suffix*.  This allows
        users to pass a short hex fragment (e.g. ``b007bbe9``) instead of the
        full ``session-20260305_194024-b007bbe9f853`` prefix.

        Args:
            session: Session ID (full or partial).  Empty → latest file.
            suffix: File suffix, e.g. ``".log"`` or ``"-api.jsonl"``.
        """
        if not session:
            return self._find_latest_file(suffix)

        # 1. Exact match
        exact = self._log_dir / f"{session}{suffix}"
        if exact.is_file():
            return exact

        # 2. Substring / prefix match against all candidate files
        if not self._log_dir.is_dir():
            return None

        matches: list[Path] = []
        for p in self._log_dir.iterdir():
            if not p.name.endswith(suffix):
                continue
            # Strip suffix to get the session prefix portion
            prefix = p.name[: -len(suffix)]
            if session in prefix:
                matches.append(p)

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Multiple matches — pick the latest by filename sort
            matches.sort(key=lambda p: p.name, reverse=True)
            return matches[0]

        return None

    def _resolve_log_file(self, session: str) -> Path | None:
        """Find the log file for a session."""
        return self._resolve_session_file(session, ".log")

    def _resolve_api_file(self, session: str) -> Path | None:
        """Find the API file for a session."""
        return self._resolve_session_file(session, "-api.jsonl")

    def _find_latest_file(self, suffix: str) -> Path | None:
        """Find the latest file matching suffix by filename sort.

        Only considers files that match known session file patterns.
        """
        if not self._log_dir.is_dir():
            return None

        candidates = sorted(
            (p for p in self._log_dir.iterdir()
             if p.name.endswith(suffix) and _extract_session_prefix(p.name) is not None),
            key=lambda p: p.name,
            reverse=True,
        )
        return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# File parsing helpers
# ---------------------------------------------------------------------------


def _extract_session_prefix(filename: str) -> str | None:
    """Extract session identifier from a log filename.

    Recognizes files like:
    - ``server-20260217_085924.log`` (server log)
    - ``session-20260217_085924-a1b2c3d4e5f6.log`` (session log)
    - ``session-20260217_085924-a1b2c3d4e5f6-api.jsonl`` (session API)
    - ``20260217_085924.log`` (mutagent standalone log)
    - ``20260217_085924-api.jsonl`` (mutagent standalone API)
    """
    m = _SESSION_FILE_RE.match(filename)
    if m:
        return m.group(1)
    return None


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


def _make_api_summary(data: dict, prev_tool_map: dict[str, str] | None = None) -> str:
    """Generate a human-readable summary of an API call record.

    Args:
        data: The API record dict.
        prev_tool_map: Mapping of tool_use_id → tool_name from the previous
            record's response, used to associate tool_result with tool names (P3).
    """
    record_type = data.get("type", "unknown")

    if record_type == "session":
        model = data.get("model", "?")
        tools = data.get("tools", [])
        return f"session (model={model}, tools={len(tools)})"

    if record_type != "call":
        return record_type

    # Determine input summary (with P3 tool_result association)
    input_data = data.get("input", data.get("messages", []))
    if isinstance(input_data, dict):
        input_summary = _summarize_message(input_data, prev_tool_map)
    elif isinstance(input_data, list) and input_data:
        last = input_data[-1] if input_data else {}
        input_summary = _summarize_message(last, prev_tool_map)
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


def _summarize_message(msg: dict, prev_tool_map: dict[str, str] | None = None) -> str:
    """Create a short summary of a message.

    When prev_tool_map is provided, tool_result blocks are annotated with the
    corresponding tool name and error status (P3).
    """
    content = msg.get("content", "")
    if isinstance(content, str):
        preview = content[:30]
        if len(content) > 30:
            preview += "..."
        return f'{msg.get("role", "?")}: "{preview}"'
    elif isinstance(content, list):
        tool_results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
        if tool_results and prev_tool_map:
            # P3: annotate with tool names and error status
            labels: list[str] = []
            for tr in tool_results:
                use_id = tr.get("tool_use_id", "")
                name = prev_tool_map.get(use_id, "")
                is_err = tr.get("is_error", False)
                if name and is_err:
                    labels.append(f"{name}:error")
                elif name:
                    labels.append(name)
                elif is_err:
                    labels.append("error")
            if labels:
                return f'{msg.get("role", "?")}: [tool_result:{",".join(labels)}]'
        # Fallback: original behavior
        types = [b.get("type", "?") for b in content if isinstance(b, dict)]
        return f'{msg.get("role", "?")}: [{", ".join(types)}]'
    return msg.get("role", "?")


def _api_has_tool(data: dict, tool_name: str) -> bool:
    """Check if an API record references a specific tool name."""
    text = json.dumps(data, ensure_ascii=False)
    return tool_name in text


def _build_tool_use_map(data: dict) -> dict[str, str]:
    """Build a mapping of tool_use_id → tool_name from a record's response."""
    result: dict[str, str] = {}
    response = data.get("response", {})
    for block in response.get("content", []):
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tid = block.get("id", "")
            name = block.get("name", "")
            if tid and name:
                result[tid] = name
    return result


def _make_verbose_lines(data: dict) -> list[str]:
    """Generate verbose detail lines for tool_use calls in a record (P1)."""
    response = data.get("response", {})
    if response.get("stop_reason") != "tool_use":
        return []

    lines: list[str] = []
    for block in response.get("content", []):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name", "?")
        tool_input = block.get("input", {})
        params = _summarize_tool_input(tool_input)
        lines.append(f"     {name}({params})")
    return lines


def _summarize_tool_input(tool_input: dict, max_value_len: int = 40) -> str:
    """Summarize tool input parameters for verbose display.

    Takes the first 2 keys, truncates long string values.
    """
    if not isinstance(tool_input, dict):
        return ""
    parts: list[str] = []
    for key in list(tool_input.keys())[:2]:
        value = tool_input[key]
        if isinstance(value, str):
            if "\n" in value:
                line_count = value.count("\n") + 1
                display = f'"...{line_count} lines..."'
            elif len(value) > max_value_len:
                display = f'"{value[:max_value_len]}..."'
            else:
                display = f'"{value}"'
        else:
            display = str(value)
            if len(display) > max_value_len:
                display = display[:max_value_len] + "..."
        parts.append(f'{key}={display}')
    return ", ".join(parts)


def _load_api_records(api_file: Path) -> list[dict]:
    """Load all records from an API JSONL file."""
    records: list[dict] = []
    for line in _iter_file_lines(api_file):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _extract_tool_calls(records: list[dict]) -> list[ToolCallInfo]:
    """Extract all tool calls from a sequence of API records.

    Correlates tool_use blocks in responses with tool_result blocks in the
    next record's input, using tool_use_id for matching.
    """
    # First pass: collect all tool_use blocks with their API index
    tool_uses: list[tuple[int, dict]] = []  # (api_index, tool_use_block)
    for idx, rec in enumerate(records):
        if rec.get("type") != "call":
            continue
        response = rec.get("response", {})
        for block in response.get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_uses.append((idx, block))

    # Second pass: collect all tool_result blocks keyed by tool_use_id
    result_map: dict[str, dict] = {}  # tool_use_id → tool_result block
    for rec in records:
        if rec.get("type") != "call":
            continue
        input_data = rec.get("input", {})
        content = input_data.get("content", []) if isinstance(input_data, dict) else []
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                use_id = block.get("tool_use_id", "")
                if use_id:
                    result_map[use_id] = block

    # Build ToolCallInfo list
    tool_call_infos: list[ToolCallInfo] = []
    for seq, (api_idx, tu_block) in enumerate(tool_uses, start=1):
        use_id = tu_block.get("id", "")
        name = tu_block.get("name", "?")
        tool_input = tu_block.get("input", {})
        input_summary = _summarize_tool_input(tool_input, max_value_len=30)

        # Look up result
        is_error = False
        result_summary = ""
        result_length = 0
        tr_block = result_map.get(use_id)
        if tr_block is not None:
            is_error = bool(tr_block.get("is_error", False))
            tr_content = tr_block.get("content", "")
            if isinstance(tr_content, str):
                result_length = len(tr_content)
            elif isinstance(tr_content, list):
                # Concatenate text blocks for length
                result_length = sum(
                    len(b.get("text", "")) for b in tr_content if isinstance(b, dict)
                )

            if is_error:
                # Show first line of error message, truncated to 60 chars
                err_text = tr_content if isinstance(tr_content, str) else ""
                if isinstance(tr_content, list):
                    for b in tr_content:
                        if isinstance(b, dict) and b.get("type") == "text":
                            err_text = b.get("text", "")
                            break
                first_line = err_text.split("\n")[0][:60]
                result_summary = f"error: {first_line}"
            else:
                result_summary = f"ok ({result_length} chars)"

        tool_call_infos.append(ToolCallInfo(
            index=seq,
            api_index=api_idx,
            tool_name=name,
            input_summary=input_summary,
            is_error=is_error,
            result_summary=result_summary,
            result_length=result_length,
        ))

    return tool_call_infos


def _compute_session_stats(info: SessionInfo) -> None:
    """Compute tool statistics and duration for a session, updating in place."""
    assert info.api_file is not None
    records = _load_api_records(info.api_file)
    if not records:
        return

    # Tool statistics
    tool_calls = _extract_tool_calls(records)
    info.tool_ok_count = sum(1 for tc in tool_calls if not tc.is_error)
    info.tool_err_count = sum(1 for tc in tool_calls if tc.is_error)

    # Duration: first and last record timestamps
    timestamps: list[str] = []
    for rec in records:
        ts = rec.get("ts", "")
        if ts:
            timestamps.append(ts)
    if len(timestamps) >= 2:
        info.duration_seconds = _iso_diff_seconds(timestamps[0], timestamps[-1])


def _iso_diff_seconds(ts1: str, ts2: str) -> float:
    """Compute the difference in seconds between two ISO timestamps."""
    from datetime import datetime, timezone

    def _parse(ts: str) -> datetime | None:
        # Handle various ISO formats
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
        ):
            try:
                return datetime.strptime(ts, fmt)
            except ValueError:
                continue
        return None

    dt1 = _parse(ts1)
    dt2 = _parse(ts2)
    if dt1 is None or dt2 is None:
        return -1
    return abs((dt2 - dt1).total_seconds())


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
