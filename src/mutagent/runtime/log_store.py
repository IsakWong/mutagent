"""mutagent.runtime.log_store -- In-memory log storage with query support."""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass

# Level name → numeric value for comparison
_LEVEL_VALUES = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# ContextVar for tool log capture (activated per-tool-call in agent_impl)
_tool_log_buffer: ContextVar[list[str] | None] = ContextVar(
    "_tool_log_buffer", default=None
)


@dataclass
class LogEntry:
    """A single log entry."""

    timestamp: float  # time.time()
    level: str  # "DEBUG", "INFO", "WARNING", "ERROR"
    logger_name: str  # "mutagent.agent"
    message: str  # formatted message


class LogStore:
    """In-memory log storage with query support.

    Stores all log entries without capacity limit.
    Use ``query(limit=...)`` to control how many entries are returned.
    """

    def __init__(self) -> None:
        self._entries: list[LogEntry] = []
        self.tool_capture_enabled: bool = False

    def append(self, entry: LogEntry) -> None:
        """Append a log entry."""
        self._entries.append(entry)

    def query(
        self,
        pattern: str = "",
        level: str = "DEBUG",
        limit: int = 50,
    ) -> list[LogEntry]:
        """Query log entries, newest first.

        Args:
            pattern: Regex pattern to match against message. Empty matches all.
            level: Minimum log level filter.
            limit: Maximum number of entries to return.

        Returns:
            Matching entries in reverse chronological order (newest first).
        """
        min_level = _LEVEL_VALUES.get(level.upper(), logging.DEBUG)
        compiled = re.compile(pattern) if pattern else None

        results: list[LogEntry] = []
        for entry in reversed(self._entries):
            if len(results) >= limit:
                break
            entry_level = _LEVEL_VALUES.get(entry.level, logging.DEBUG)
            if entry_level < min_level:
                continue
            if compiled and not compiled.search(entry.message):
                continue
            results.append(entry)
        return results

    def count(self) -> int:
        """Return total number of stored entries."""
        return len(self._entries)


class LogStoreHandler(logging.Handler):
    """Logging handler that writes records to a LogStore."""

    def __init__(self, store: LogStore) -> None:
        super().__init__(level=logging.DEBUG)
        self.store = store

    def emit(self, record: logging.LogRecord) -> None:
        entry = LogEntry(
            timestamp=record.created,
            level=record.levelname,
            logger_name=record.name,
            message=self.format(record),
        )
        self.store.append(entry)


class ToolLogCaptureHandler(logging.Handler):
    """Logging handler that captures records into a ContextVar buffer.

    When ``_tool_log_buffer`` is set to a list (during tool execution),
    formatted log messages are appended to it.  When ``None`` (default),
    this handler is a no-op.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)

    def emit(self, record: logging.LogRecord) -> None:
        buf = _tool_log_buffer.get()
        if buf is not None:
            buf.append(self.format(record))
