"""mutagent.runtime.api_recorder -- JSONL API call recorder."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, IO


class ApiRecorder:
    """Records LLM API calls to a JSONL file.

    Supports two modes:
    - ``"incremental"``: each call records only the new input message + response.
    - ``"full"``: each call records the complete messages array + response.

    File naming: ``api_{session_ts}.jsonl`` under ``log_dir``.
    """

    def __init__(
        self,
        log_dir: Path,
        mode: str = "incremental",
        session_ts: str = "",
    ) -> None:
        self._log_dir = Path(log_dir)
        self._mode = mode
        self._session_ts = session_ts or datetime.now().strftime("%Y%m%d_%H%M%S")
        self._file: IO[str] | None = None

    def _ensure_file(self) -> IO[str]:
        if self._file is None:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            path = self._log_dir / f"api_{self._session_ts}.jsonl"
            self._file = open(path, "a", encoding="utf-8")
        return self._file

    def start_session(
        self,
        model: str,
        system_prompt: str,
        tools: list[dict[str, Any]],
    ) -> None:
        """Write the session header record."""
        record = {
            "type": "session",
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "system_prompt": system_prompt,
            "tools": tools,
        }
        f = self._ensure_file()
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()

    def record_call(
        self,
        messages: list[dict[str, Any]],
        new_message: dict[str, Any],
        response: dict[str, Any],
        usage: dict[str, Any],
        duration_ms: int,
    ) -> None:
        """Record a single API call.

        Args:
            messages: The complete messages array sent to the API.
            new_message: Only the newly added user message (for incremental mode).
            response: The assembled (non-streaming) response.
            usage: Token usage dict.
            duration_ms: Wall-clock time in milliseconds.
        """
        record: dict[str, Any] = {
            "type": "call",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if self._mode == "full":
            record["messages"] = messages
        else:
            record["input"] = new_message
        record["response"] = response
        record["usage"] = usage
        record["duration_ms"] = duration_ms

        f = self._ensure_file()
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()

    def close(self) -> None:
        """Close the underlying file."""
        if self._file is not None:
            self._file.close()
            self._file = None
