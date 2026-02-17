"""Tests for log query: format fixes, LogQueryEngine, and CLI."""

import json
import logging
import time
from pathlib import Path

import pytest

from mutagent.runtime.log_store import (
    LogEntry,
    LogStore,
    LogStoreHandler,
    SingleLineFormatter,
)
from mutagent.runtime.log_query import (
    LogQueryEngine,
    SessionInfo,
    LogLine,
    ApiCall,
    _iter_log_entries,
    _make_api_summary,
    _extract_field,
)
from mutagent.cli.log_query import main as cli_main


# ---------------------------------------------------------------------------
# SingleLineFormatter
# ---------------------------------------------------------------------------

class TestSingleLineFormatter:

    def test_single_line_message_unchanged(self):
        formatter = SingleLineFormatter("%(levelname)s - %(message)s")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="simple message", args=(), exc_info=None,
        )
        result = formatter.format(record)
        assert result == "INFO - simple message"

    def test_multiline_message_gets_tab_prefix(self):
        formatter = SingleLineFormatter("%(levelname)s - %(message)s")
        record = logging.LogRecord(
            name="test", level=logging.DEBUG, pathname="", lineno=0,
            msg="line1\nline2\nline3", args=(), exc_info=None,
        )
        result = formatter.format(record)
        assert result == "DEBUG - line1\n\tline2\n\tline3"

    def test_traceback_gets_tab_prefix(self):
        formatter = SingleLineFormatter("%(levelname)s - %(message)s")
        msg = "Traceback (most recent call last):\n  File \"test.py\", line 1\nError: bad"
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="", lineno=0,
            msg=msg, args=(), exc_info=None,
        )
        result = formatter.format(record)
        lines = result.split("\n")
        assert lines[0] == "ERROR - Traceback (most recent call last):"
        assert lines[1].startswith("\t")
        assert lines[2].startswith("\t")


# ---------------------------------------------------------------------------
# LogStoreHandler format fix (message only, no timestamp)
# ---------------------------------------------------------------------------

class TestLogStoreHandlerFormat:

    def test_message_only_formatter(self):
        """LogStoreHandler with %(message)s should store only the message."""
        store = LogStore()
        handler = LogStoreHandler(store)
        handler.setFormatter(logging.Formatter("%(message)s"))

        test_logger = logging.getLogger("test.format_fix")
        test_logger.addHandler(handler)
        test_logger.setLevel(logging.DEBUG)
        try:
            test_logger.info("hello world")
        finally:
            test_logger.removeHandler(handler)

        entry = store.query(limit=1)[0]
        # Should NOT contain timestamp prefix
        assert entry.message == "hello world"
        assert "202" not in entry.message  # no year prefix


# ---------------------------------------------------------------------------
# LogQueryEngine - list_sessions
# ---------------------------------------------------------------------------

class TestListSessions:

    def test_empty_directory(self, tmp_path):
        engine = LogQueryEngine(tmp_path)
        assert engine.list_sessions() == []

    def test_nonexistent_directory(self, tmp_path):
        engine = LogQueryEngine(tmp_path / "nonexistent")
        assert engine.list_sessions() == []

    def test_single_session_with_both_files(self, tmp_path):
        (tmp_path / "20260217_085924-log.log").write_text("line1\nline2\n", encoding="utf-8")
        (tmp_path / "20260217_085924-api.jsonl").write_text('{"type":"session"}\n{"type":"call"}\n', encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        sessions = engine.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].timestamp == "20260217_085924"
        assert sessions[0].log_file is not None
        assert sessions[0].api_file is not None
        assert sessions[0].log_lines == 2
        assert sessions[0].api_calls == 1  # 2 lines - 1 session header

    def test_session_with_log_only(self, tmp_path):
        (tmp_path / "20260217_100000-log.log").write_text("line1\n", encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        sessions = engine.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].log_file is not None
        assert sessions[0].api_file is None

    def test_multiple_sessions_sorted(self, tmp_path):
        (tmp_path / "20260217_100000-log.log").write_text("a\n", encoding="utf-8")
        (tmp_path / "20260218_090000-log.log").write_text("b\n", encoding="utf-8")
        (tmp_path / "20260216_120000-log.log").write_text("c\n", encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        sessions = engine.list_sessions()
        assert len(sessions) == 3
        assert sessions[0].timestamp == "20260216_120000"
        assert sessions[2].timestamp == "20260218_090000"


# ---------------------------------------------------------------------------
# LogQueryEngine - query_logs
# ---------------------------------------------------------------------------

LOG_CONTENT = """\
2026-02-17 08:59:24,301 INFO     mutagent.builtins.main_impl - Logging initialized (session=20260217_085924)
2026-02-17 08:59:30,863 DEBUG    mutagent.builtins.claude_impl - Payload size: 5002 bytes
2026-02-17 08:59:32,352 WARNING  mutagent.builtins.agent_impl - Tool call failed: timeout
2026-02-17 09:00:01,100 ERROR    mutagent.builtins.agent_impl - Critical failure
2026-02-17 09:05:18,134 DEBUG    mutagent.builtins.define_module_impl - Traceback (most recent call last):
\t  File "module.py", line 10, in func
\t    return bad_call()
\tTypeError: missing argument
"""


class TestQueryLogs:

    @pytest.fixture
    def engine(self, tmp_path):
        (tmp_path / "20260217_085924-log.log").write_text(LOG_CONTENT, encoding="utf-8")
        return LogQueryEngine(tmp_path)

    def test_query_all(self, engine):
        results = engine.query_logs(session="20260217_085924", limit=100)
        assert len(results) == 5

    def test_query_level_filter(self, engine):
        results = engine.query_logs(session="20260217_085924", level="WARNING", limit=100)
        assert len(results) == 2
        assert results[0].level == "WARNING"
        assert results[1].level == "ERROR"

    def test_query_pattern_filter(self, engine):
        results = engine.query_logs(session="20260217_085924", pattern="Payload", limit=100)
        assert len(results) == 1
        assert "Payload size" in results[0].message

    def test_query_limit(self, engine):
        results = engine.query_logs(session="20260217_085924", limit=2)
        assert len(results) == 2

    def test_query_time_range(self, engine):
        results = engine.query_logs(
            session="20260217_085924",
            time_from="09:00:00",
            time_to="09:05:00",
            limit=100,
        )
        assert len(results) == 1
        assert "Critical failure" in results[0].message

    def test_query_latest_session(self, tmp_path):
        (tmp_path / "20260217_085924-log.log").write_text(LOG_CONTENT, encoding="utf-8")
        (tmp_path / "20260218_100000-log.log").write_text(
            "2026-02-18 10:00:00,000 INFO     test - newer session\n",
            encoding="utf-8",
        )
        engine = LogQueryEngine(tmp_path)
        results = engine.query_logs(limit=100)
        assert len(results) == 1
        assert "newer session" in results[0].message

    def test_continuation_lines_merged(self, engine):
        results = engine.query_logs(
            session="20260217_085924", pattern="Traceback", limit=100,
        )
        assert len(results) == 1
        entry = results[0]
        assert "Traceback" in entry.message
        assert "TypeError: missing argument" in entry.message
        # Continuation tab should be stripped in message
        assert "\t" not in entry.message.split("\n", 1)[0]

    def test_line_numbers(self, engine):
        results = engine.query_logs(session="20260217_085924", limit=100)
        assert results[0].line_no == 1
        assert results[1].line_no == 2

    def test_nonexistent_session(self, engine):
        results = engine.query_logs(session="99999999_999999", limit=100)
        assert results == []


# ---------------------------------------------------------------------------
# LogQueryEngine - load_to_store
# ---------------------------------------------------------------------------

class TestLoadToStore:

    def test_load_basic(self, tmp_path):
        content = "2026-02-17 08:59:24,301 INFO     mutagent.test - hello world\n"
        (tmp_path / "20260217_085924-log.log").write_text(content, encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        store = engine.load_to_store("20260217_085924")
        assert store.count() == 1
        entry = store.query(limit=1)[0]
        assert entry.level == "INFO"
        assert entry.logger_name == "mutagent.test"
        assert entry.message == "hello world"

    def test_load_preserves_all_entries(self, tmp_path):
        (tmp_path / "20260217_085924-log.log").write_text(LOG_CONTENT, encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        store = engine.load_to_store("20260217_085924")
        assert store.count() == 5

    def test_load_nonexistent(self, tmp_path):
        engine = LogQueryEngine(tmp_path)
        store = engine.load_to_store("nonexistent")
        assert store.count() == 0


# ---------------------------------------------------------------------------
# LogQueryEngine - query_api
# ---------------------------------------------------------------------------

API_CONTENT = """\
{"type":"session","ts":"2026-02-17T00:59:24Z","model":"test-model","system_prompt":"You are test","tools":[{"name":"define_module"}]}
{"type":"call","ts":"2026-02-17T00:59:32Z","input":{"role":"user","content":"hello"},"response":{"content":[{"type":"text","text":"hi"}],"stop_reason":"end_turn"},"usage":{"input_tokens":10,"output_tokens":5},"duration_ms":100}
{"type":"call","ts":"2026-02-17T00:59:35Z","input":{"role":"user","content":"define it"},"response":{"content":[{"type":"tool_use","name":"define_module","input":{"source":"x=1"}}],"stop_reason":"tool_use"},"usage":{"input_tokens":20,"output_tokens":10},"duration_ms":200}
"""


class TestQueryApi:

    @pytest.fixture
    def engine(self, tmp_path):
        (tmp_path / "20260217_085924-api.jsonl").write_text(API_CONTENT, encoding="utf-8")
        return LogQueryEngine(tmp_path)

    def test_query_all(self, engine):
        results = engine.query_api(session="20260217_085924", limit=100)
        assert len(results) == 3
        assert results[0].type == "session"
        assert results[1].type == "call"

    def test_query_by_index(self, engine):
        results = engine.query_api(session="20260217_085924", call_index=1)
        assert len(results) == 1
        assert results[0].index == 1
        assert results[0].type == "call"

    def test_query_by_tool_name(self, engine):
        results = engine.query_api(session="20260217_085924", tool_name="define_module", limit=100)
        # Should match session (has define_module in tools) and call #2 (uses define_module)
        assert len(results) >= 1
        # At least the tool_use call should match
        tool_use_calls = [r for r in results if r.type == "call" and "define_module" in json.dumps(r.data)]
        assert len(tool_use_calls) >= 1

    def test_query_by_pattern(self, engine):
        results = engine.query_api(session="20260217_085924", pattern="hello", limit=100)
        assert len(results) == 1
        assert results[0].index == 1

    def test_query_limit(self, engine):
        results = engine.query_api(session="20260217_085924", limit=1)
        assert len(results) == 1

    def test_api_summary_session(self, engine):
        results = engine.query_api(session="20260217_085924", call_index=0)
        assert "session" in results[0].summary

    def test_api_summary_call(self, engine):
        results = engine.query_api(session="20260217_085924", call_index=2)
        assert "tool_use" in results[0].summary


# ---------------------------------------------------------------------------
# LogQueryEngine - get_api_detail
# ---------------------------------------------------------------------------

class TestGetApiDetail:

    @pytest.fixture
    def engine(self, tmp_path):
        (tmp_path / "20260217_085924-api.jsonl").write_text(API_CONTENT, encoding="utf-8")
        return LogQueryEngine(tmp_path)

    def test_full_record(self, engine):
        result = engine.get_api_detail("20260217_085924", 1)
        assert isinstance(result, dict)
        assert result["type"] == "call"
        assert result["input"]["content"] == "hello"

    def test_field_extraction(self, engine):
        result = engine.get_api_detail("20260217_085924", 1, field_path="input.content")
        assert result == "hello"

    def test_nested_field_with_index(self, engine):
        result = engine.get_api_detail("20260217_085924", 1, field_path="response.content[0].type")
        assert result == "text"

    def test_nonexistent_call(self, engine):
        result = engine.get_api_detail("20260217_085924", 99)
        assert isinstance(result, dict)
        assert "error" in result

    def test_nonexistent_field(self, engine):
        result = engine.get_api_detail("20260217_085924", 1, field_path="nonexistent")
        assert isinstance(result, str)
        assert "not found" in result


# ---------------------------------------------------------------------------
# _extract_field
# ---------------------------------------------------------------------------

class TestExtractField:

    def test_simple_path(self):
        data = {"a": {"b": "value"}}
        assert _extract_field(data, "a.b") == "value"

    def test_array_index(self):
        data = {"items": [{"name": "first"}, {"name": "second"}]}
        assert _extract_field(data, "items[1].name") == "second"

    def test_missing_field(self):
        data = {"a": 1}
        result = _extract_field(data, "b")
        assert "not found" in str(result)

    def test_index_out_of_range(self):
        data = {"items": [1]}
        result = _extract_field(data, "items[5]")
        assert "out of range" in str(result)


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestCLI:

    def test_sessions_command(self, tmp_path, capsys):
        (tmp_path / "20260217_085924-log.log").write_text("line1\n", encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "sessions"])
        output = capsys.readouterr().out
        assert "20260217_085924" in output

    def test_sessions_empty(self, tmp_path, capsys):
        cli_main(["--dir", str(tmp_path), "sessions"])
        output = capsys.readouterr().out
        assert "No sessions found" in output

    def test_logs_command(self, tmp_path, capsys):
        content = "2026-02-17 08:59:24,301 INFO     mutagent.test - hello world\n"
        (tmp_path / "20260217_085924-log.log").write_text(content, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "logs", "-s", "20260217_085924"])
        output = capsys.readouterr().out
        assert "hello world" in output

    def test_logs_with_pattern(self, tmp_path, capsys):
        content = (
            "2026-02-17 08:59:24,301 INFO     mutagent.test - hello\n"
            "2026-02-17 08:59:25,000 INFO     mutagent.test - goodbye\n"
        )
        (tmp_path / "20260217_085924-log.log").write_text(content, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "logs", "-s", "20260217_085924", "-p", "hello"])
        output = capsys.readouterr().out
        assert "hello" in output
        assert "goodbye" not in output

    def test_api_command(self, tmp_path, capsys):
        (tmp_path / "20260217_085924-api.jsonl").write_text(API_CONTENT, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "api", "-s", "20260217_085924"])
        output = capsys.readouterr().out
        assert "#00" in output
        assert "#01" in output

    def test_api_detail_command(self, tmp_path, capsys):
        (tmp_path / "20260217_085924-api.jsonl").write_text(API_CONTENT, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "api-detail", "20260217_085924", "1"])
        output = capsys.readouterr().out
        assert "hello" in output

    def test_api_detail_with_field(self, tmp_path, capsys):
        (tmp_path / "20260217_085924-api.jsonl").write_text(API_CONTENT, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "api-detail", "20260217_085924", "1", "-f", "input.content"])
        output = capsys.readouterr().out
        assert "hello" in output

    def test_logs_empty_result(self, tmp_path, capsys):
        content = "2026-02-17 08:59:24,301 INFO     mutagent.test - hello\n"
        (tmp_path / "20260217_085924-log.log").write_text(content, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "logs", "-s", "20260217_085924", "-p", "nonexistent"])
        output = capsys.readouterr().out
        assert "No matching" in output

    def test_no_command_shows_help(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            cli_main(["--dir", str(tmp_path)])
