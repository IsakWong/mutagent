"""Tests for the logging system: LogStore, ApiRecorder, query_logs tool, tool capture."""

import json
import logging
import time
from pathlib import Path

import pytest

from mutagent.runtime.log_store import (
    LogEntry,
    LogStore,
    LogStoreHandler,
    ToolLogCaptureHandler,
    _tool_log_buffer,
)
from mutagent.runtime.api_recorder import ApiRecorder
from mutagent.essential_tools import EssentialTools
from mutagent.runtime.module_manager import ModuleManager

import mutagent.builtins  # noqa: F401  -- register all @impl


# ---------------------------------------------------------------------------
# LogStore
# ---------------------------------------------------------------------------

class TestLogStore:

    def test_append_and_count(self):
        store = LogStore()
        assert store.count() == 0
        store.append(LogEntry(time.time(), "INFO", "test", "hello"))
        assert store.count() == 1

    def test_query_returns_newest_first(self):
        store = LogStore()
        store.append(LogEntry(1.0, "INFO", "test", "first"))
        store.append(LogEntry(2.0, "INFO", "test", "second"))
        store.append(LogEntry(3.0, "INFO", "test", "third"))
        results = store.query(limit=10)
        assert len(results) == 3
        assert results[0].message == "third"
        assert results[2].message == "first"

    def test_query_limit(self):
        store = LogStore()
        for i in range(100):
            store.append(LogEntry(float(i), "INFO", "test", f"msg {i}"))
        results = store.query(limit=5)
        assert len(results) == 5
        assert results[0].message == "msg 99"

    def test_query_level_filter(self):
        store = LogStore()
        store.append(LogEntry(1.0, "DEBUG", "test", "debug msg"))
        store.append(LogEntry(2.0, "INFO", "test", "info msg"))
        store.append(LogEntry(3.0, "WARNING", "test", "warn msg"))
        store.append(LogEntry(4.0, "ERROR", "test", "error msg"))

        results = store.query(level="WARNING", limit=10)
        assert len(results) == 2
        assert results[0].message == "error msg"
        assert results[1].message == "warn msg"

    def test_query_pattern_filter(self):
        store = LogStore()
        store.append(LogEntry(1.0, "INFO", "test", "module foo defined"))
        store.append(LogEntry(2.0, "INFO", "test", "module bar defined"))
        store.append(LogEntry(3.0, "INFO", "test", "something else"))
        results = store.query(pattern="module.*defined", limit=10)
        assert len(results) == 2

    def test_query_pattern_and_level(self):
        store = LogStore()
        store.append(LogEntry(1.0, "DEBUG", "test", "error occurred"))
        store.append(LogEntry(2.0, "ERROR", "test", "error occurred"))
        results = store.query(pattern="error", level="ERROR", limit=10)
        assert len(results) == 1
        assert results[0].level == "ERROR"

    def test_query_empty_pattern_matches_all(self):
        store = LogStore()
        store.append(LogEntry(1.0, "INFO", "test", "hello"))
        results = store.query(pattern="", limit=10)
        assert len(results) == 1

    def test_no_capacity_limit(self):
        store = LogStore()
        for i in range(5000):
            store.append(LogEntry(float(i), "DEBUG", "test", f"msg {i}"))
        assert store.count() == 5000

    def test_tool_capture_default_off(self):
        store = LogStore()
        assert store.tool_capture_enabled is False


# ---------------------------------------------------------------------------
# LogStoreHandler
# ---------------------------------------------------------------------------

class TestLogStoreHandler:

    def test_handler_writes_to_store(self):
        store = LogStore()
        handler = LogStoreHandler(store)
        handler.setFormatter(logging.Formatter("%(message)s"))

        logger = logging.getLogger("test.log_store_handler")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("test message")
        finally:
            logger.removeHandler(handler)

        assert store.count() == 1
        entry = store.query(limit=1)[0]
        assert entry.level == "INFO"
        assert entry.message == "test message"
        assert entry.logger_name == "test.log_store_handler"

    def test_handler_captures_all_levels(self):
        store = LogStore()
        handler = LogStoreHandler(store)
        handler.setFormatter(logging.Formatter("%(message)s"))

        logger = logging.getLogger("test.log_store_all_levels")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.debug("d")
            logger.info("i")
            logger.warning("w")
            logger.error("e")
        finally:
            logger.removeHandler(handler)

        assert store.count() == 4


# ---------------------------------------------------------------------------
# ToolLogCaptureHandler
# ---------------------------------------------------------------------------

class TestToolLogCaptureHandler:

    def test_capture_when_buffer_active(self):
        handler = ToolLogCaptureHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))

        logger = logging.getLogger("test.tool_capture_active")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            buf: list[str] = []
            token = _tool_log_buffer.set(buf)
            try:
                logger.info("captured message")
            finally:
                _tool_log_buffer.reset(token)
        finally:
            logger.removeHandler(handler)

        assert len(buf) == 1
        assert buf[0] == "captured message"

    def test_no_capture_when_buffer_inactive(self):
        handler = ToolLogCaptureHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))

        logger = logging.getLogger("test.tool_capture_inactive")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            # No _tool_log_buffer set — should be a no-op
            logger.info("not captured")
        finally:
            logger.removeHandler(handler)
        # No assertion needed — just verifying no crash


# ---------------------------------------------------------------------------
# ApiRecorder
# ---------------------------------------------------------------------------

class TestApiRecorder:

    def test_start_session_creates_file(self, tmp_path):
        rec = ApiRecorder(tmp_path, mode="incremental", session_ts="20260217_103000")
        rec.start_session(model="test-model", system_prompt="You are test", tools=[])
        rec.close()

        path = tmp_path / "20260217_103000-api.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["type"] == "session"
        assert data["model"] == "test-model"
        assert data["system_prompt"] == "You are test"

    def test_record_call_incremental(self, tmp_path):
        rec = ApiRecorder(tmp_path, mode="incremental", session_ts="20260217_110000")
        rec.start_session(model="m", system_prompt="s", tools=[])
        rec.record_call(
            messages=[{"role": "user", "content": "hello"}],
            new_message={"role": "user", "content": "hello"},
            response={"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn"},
            usage={"input_tokens": 10, "output_tokens": 5},
            duration_ms=100,
        )
        rec.close()

        lines = (tmp_path / "20260217_110000-api.jsonl").read_text(encoding="utf-8").strip().split("\n")
        call_data = json.loads(lines[1])
        assert call_data["type"] == "call"
        assert "input" in call_data
        assert "messages" not in call_data
        assert call_data["input"]["content"] == "hello"
        assert call_data["duration_ms"] == 100

    def test_record_call_full(self, tmp_path):
        rec = ApiRecorder(tmp_path, mode="full", session_ts="20260217_120000")
        rec.start_session(model="m", system_prompt="s", tools=[])
        rec.record_call(
            messages=[
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "reply1"},
                {"role": "user", "content": "msg2"},
            ],
            new_message={"role": "user", "content": "msg2"},
            response={"content": [{"type": "text", "text": "reply2"}], "stop_reason": "end_turn"},
            usage={"input_tokens": 50, "output_tokens": 20},
            duration_ms=200,
        )
        rec.close()

        lines = (tmp_path / "20260217_120000-api.jsonl").read_text(encoding="utf-8").strip().split("\n")
        call_data = json.loads(lines[1])
        assert "messages" in call_data
        assert "input" not in call_data
        assert len(call_data["messages"]) == 3

    def test_auto_creates_directory(self, tmp_path):
        log_dir = tmp_path / "nested" / "logs"
        assert not log_dir.exists()
        rec = ApiRecorder(log_dir, session_ts="20260217_130000")
        rec.start_session(model="m", system_prompt="s", tools=[])
        rec.close()
        assert (log_dir / "20260217_130000-api.jsonl").exists()

    def test_close_is_idempotent(self, tmp_path):
        rec = ApiRecorder(tmp_path, session_ts="20260217_140000")
        rec.start_session(model="m", system_prompt="s", tools=[])
        rec.close()
        rec.close()  # should not raise


# ---------------------------------------------------------------------------
# query_logs tool
# ---------------------------------------------------------------------------

class TestQueryLogsTool:

    @pytest.fixture
    def tools(self):
        mgr = ModuleManager()
        log_store = LogStore()
        t = EssentialTools(module_manager=mgr, log_store=log_store)
        yield t
        mgr.cleanup()

    def test_query_no_logs(self, tools):
        result = tools.query_logs()
        assert "Total entries: 0" in result
        assert "no matching entries" in result

    def test_query_with_entries(self, tools):
        tools.log_store.append(LogEntry(time.time(), "INFO", "test", "hello world"))
        result = tools.query_logs()
        assert "hello world" in result
        assert "Total entries: 1" in result

    def test_query_pattern_filter(self, tools):
        tools.log_store.append(LogEntry(time.time(), "INFO", "a", "foo bar"))
        tools.log_store.append(LogEntry(time.time(), "INFO", "b", "baz qux"))
        result = tools.query_logs(pattern="foo")
        assert "foo bar" in result
        assert "baz qux" not in result

    def test_query_level_filter(self, tools):
        tools.log_store.append(LogEntry(time.time(), "DEBUG", "a", "debug msg"))
        tools.log_store.append(LogEntry(time.time(), "ERROR", "b", "error msg"))
        result = tools.query_logs(level="ERROR")
        assert "error msg" in result
        assert "debug msg" not in result

    def test_tool_capture_on_off(self, tools):
        assert tools.log_store.tool_capture_enabled is False

        result = tools.query_logs(tool_capture="on")
        assert "Tool capture: on" in result
        assert tools.log_store.tool_capture_enabled is True

        result = tools.query_logs(tool_capture="off")
        assert "Tool capture: off" in result
        assert tools.log_store.tool_capture_enabled is False

    def test_query_limit(self, tools):
        for i in range(20):
            tools.log_store.append(LogEntry(float(i), "INFO", "test", f"msg {i}"))
        result = tools.query_logs(limit=3)
        assert "showing 3 of 20" in result

    def test_query_shows_version_in_output(self, tools):
        tools.log_store.append(
            LogEntry(time.time(), "INFO", "mutagent.tools", "Module x defined (v2)")
        )
        result = tools.query_logs(pattern="Module x")
        assert "Module x defined (v2)" in result


# ---------------------------------------------------------------------------
# Integration: tool log capture in agent loop
# ---------------------------------------------------------------------------

class TestToolLogCaptureIntegration:

    def test_capture_appends_to_tool_result(self):
        """Simulate what agent_impl does with tool capture."""
        handler = ToolLogCaptureHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        test_logger = logging.getLogger("test.capture_integration")
        test_logger.addHandler(handler)
        test_logger.setLevel(logging.DEBUG)

        try:
            buf: list[str] = []
            token = _tool_log_buffer.set(buf)
            try:
                # Simulate tool execution that emits logs
                test_logger.info("inside tool execution")
                test_logger.debug("debug detail")
            finally:
                _tool_log_buffer.reset(token)

            assert len(buf) == 2
            assert "inside tool execution" in buf[0]
            assert "debug detail" in buf[1]

            # Simulate appending to tool result
            tool_output = "OK: module defined"
            if buf:
                tool_output += "\n\n[Tool Logs]\n" + "\n".join(buf)
            assert "[Tool Logs]" in tool_output
            assert "inside tool execution" in tool_output
        finally:
            test_logger.removeHandler(handler)

    def test_no_capture_without_buffer(self):
        """Without setting the buffer, no logs are captured."""
        handler = ToolLogCaptureHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        test_logger = logging.getLogger("test.no_capture")
        test_logger.addHandler(handler)
        test_logger.setLevel(logging.DEBUG)

        try:
            test_logger.info("this should not be captured anywhere")
        finally:
            test_logger.removeHandler(handler)
        # If we get here without error, it works


# ---------------------------------------------------------------------------
# Integration: LogStore + FileHandler sharing session timestamp
# ---------------------------------------------------------------------------

class TestLogFileIntegration:

    def test_file_handler_writes_logs(self, tmp_path):
        """Verify FileHandler produces a log file alongside LogStore."""
        log_file = tmp_path / "20260217_100000-log.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-5s %(name)s — %(message)s")
        )

        store = LogStore()
        mem_handler = LogStoreHandler(store)
        mem_handler.setFormatter(logging.Formatter("%(message)s"))

        test_logger = logging.getLogger("test.file_integration")
        test_logger.addHandler(file_handler)
        test_logger.addHandler(mem_handler)
        test_logger.setLevel(logging.DEBUG)

        try:
            test_logger.info("file and memory")
        finally:
            test_logger.removeHandler(file_handler)
            test_logger.removeHandler(mem_handler)
            file_handler.close()

        # Memory
        assert store.count() == 1
        assert store.query(limit=1)[0].message == "file and memory"

        # File
        content = log_file.read_text(encoding="utf-8")
        assert "file and memory" in content
