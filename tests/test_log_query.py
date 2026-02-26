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
    ToolCallInfo,
    _iter_log_entries,
    _make_api_summary,
    _extract_field,
    _extract_tool_calls,
    _summarize_tool_input,
    _build_tool_use_map,
    _make_verbose_lines,
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
        (tmp_path / "20260217_085924.log").write_text("line1\nline2\n", encoding="utf-8")
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
        (tmp_path / "20260217_100000.log").write_text("line1\n", encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        sessions = engine.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].log_file is not None
        assert sessions[0].api_file is None

    def test_multiple_sessions_sorted(self, tmp_path):
        (tmp_path / "20260217_100000.log").write_text("a\n", encoding="utf-8")
        (tmp_path / "20260218_090000.log").write_text("b\n", encoding="utf-8")
        (tmp_path / "20260216_120000.log").write_text("c\n", encoding="utf-8")
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
        (tmp_path / "20260217_085924.log").write_text(LOG_CONTENT, encoding="utf-8")
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
        (tmp_path / "20260217_085924.log").write_text(LOG_CONTENT, encoding="utf-8")
        (tmp_path / "20260218_100000.log").write_text(
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
        (tmp_path / "20260217_085924.log").write_text(content, encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        store = engine.load_to_store("20260217_085924")
        assert store.count() == 1
        entry = store.query(limit=1)[0]
        assert entry.level == "INFO"
        assert entry.logger_name == "mutagent.test"
        assert entry.message == "hello world"

    def test_load_preserves_all_entries(self, tmp_path):
        (tmp_path / "20260217_085924.log").write_text(LOG_CONTENT, encoding="utf-8")
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
{"type":"session","ts":"2026-02-17T00:59:24Z","model":"test-model","system_prompt":"You are test","tools":[{"name":"Module-define"}]}
{"type":"call","ts":"2026-02-17T00:59:32Z","input":{"role":"user","content":"hello"},"response":{"content":[{"type":"text","text":"hi"}],"stop_reason":"end_turn"},"usage":{"input_tokens":10,"output_tokens":5},"duration_ms":100}
{"type":"call","ts":"2026-02-17T00:59:35Z","input":{"role":"user","content":"define it"},"response":{"content":[{"type":"tool_use","name":"Module-define","input":{"source":"x=1"}}],"stop_reason":"tool_use"},"usage":{"input_tokens":20,"output_tokens":10},"duration_ms":200}
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
        results = engine.query_api(session="20260217_085924", tool_name="Module-define", limit=100)
        # Should match session (has Module-define in tools) and call #2 (uses Module-define)
        assert len(results) >= 1
        # At least the tool_use call should match
        tool_use_calls = [r for r in results if r.type == "call" and "Module-define" in json.dumps(r.data)]
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
        (tmp_path / "20260217_085924.log").write_text("line1\n", encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "sessions"])
        output = capsys.readouterr().out
        assert "20260217_085924" in output

    def test_sessions_empty(self, tmp_path, capsys):
        cli_main(["--dir", str(tmp_path), "sessions"])
        output = capsys.readouterr().out
        assert "No sessions found" in output

    def test_logs_command(self, tmp_path, capsys):
        content = "2026-02-17 08:59:24,301 INFO     mutagent.test - hello world\n"
        (tmp_path / "20260217_085924.log").write_text(content, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "logs", "-s", "20260217_085924"])
        output = capsys.readouterr().out
        assert "hello world" in output

    def test_logs_with_pattern(self, tmp_path, capsys):
        content = (
            "2026-02-17 08:59:24,301 INFO     mutagent.test - hello\n"
            "2026-02-17 08:59:25,000 INFO     mutagent.test - goodbye\n"
        )
        (tmp_path / "20260217_085924.log").write_text(content, encoding="utf-8")
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
        (tmp_path / "20260217_085924.log").write_text(content, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "logs", "-s", "20260217_085924", "-p", "nonexistent"])
        output = capsys.readouterr().out
        assert "No matching" in output

    def test_no_command_shows_help(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            cli_main(["--dir", str(tmp_path)])


# ---------------------------------------------------------------------------
# Test data with tool_use / tool_result for P1-P4 tests
# ---------------------------------------------------------------------------

# A richer API JSONL with tool_use → tool_result across records
API_TOOLS_CONTENT = """\
{"type":"session","ts":"2026-02-17T00:59:24Z","model":"test-model","tools":[{"name":"Module-inspect"},{"name":"Module-define"}]}
{"type":"call","ts":"2026-02-17T00:59:32Z","input":{"role":"user","content":"check modules"},"response":{"content":[{"type":"tool_use","id":"tu_1","name":"Module-inspect","input":{"module_path":"","depth":2}}],"stop_reason":"tool_use"},"usage":{"input_tokens":100,"output_tokens":20},"duration_ms":1000}
{"type":"call","ts":"2026-02-17T00:59:34Z","input":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu_1","content":"mutagent/\\n  agent/\\n  tools/"}]},"response":{"content":[{"type":"tool_use","id":"tu_2","name":"Module-define","input":{"module_path":"my_mod","source":"import os\\ndef hello():\\n    return 'hi'\\n"}}],"stop_reason":"tool_use"},"usage":{"input_tokens":200,"output_tokens":30},"duration_ms":1500}
{"type":"call","ts":"2026-02-17T00:59:37Z","input":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu_2","is_error":true,"content":"SyntaxError: invalid syntax line 3"}]},"response":{"content":[{"type":"tool_use","id":"tu_3","name":"Module-define","input":{"module_path":"my_mod","source":"import os\\ndef hello():\\n    return 'hi'"}}],"stop_reason":"tool_use"},"usage":{"input_tokens":300,"output_tokens":30},"duration_ms":2000}
{"type":"call","ts":"2026-02-17T00:59:40Z","input":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu_3","content":"Module defined: my_mod"}]},"response":{"content":[{"type":"text","text":"Done!"}],"stop_reason":"end_turn"},"usage":{"input_tokens":400,"output_tokens":10},"duration_ms":500}
"""


# ---------------------------------------------------------------------------
# query_tools
# ---------------------------------------------------------------------------

class TestQueryTools:

    @pytest.fixture
    def engine(self, tmp_path):
        (tmp_path / "20260217_085924-api.jsonl").write_text(API_TOOLS_CONTENT, encoding="utf-8")
        return LogQueryEngine(tmp_path)

    def test_basic_extraction(self, engine):
        results = engine.query_tools(session="20260217_085924")
        assert len(results) == 3
        assert results[0].tool_name == "Module-inspect"
        assert results[1].tool_name == "Module-define"
        assert results[2].tool_name == "Module-define"

    def test_sequential_numbering(self, engine):
        results = engine.query_tools(session="20260217_085924")
        assert results[0].index == 1
        assert results[1].index == 2
        assert results[2].index == 3

    def test_error_detection(self, engine):
        results = engine.query_tools(session="20260217_085924")
        assert results[0].is_error is False
        assert results[1].is_error is True  # tu_2 got is_error=true
        assert results[2].is_error is False

    def test_result_summary_ok(self, engine):
        results = engine.query_tools(session="20260217_085924")
        # tu_1: ok with content length
        assert results[0].result_summary.startswith("ok (")
        assert "chars)" in results[0].result_summary

    def test_result_summary_error(self, engine):
        results = engine.query_tools(session="20260217_085924")
        # tu_2: error
        assert results[1].result_summary.startswith("error: ")
        assert "SyntaxError" in results[1].result_summary

    def test_filter_by_tool_name(self, engine):
        results = engine.query_tools(session="20260217_085924", tool_name="Module-define")
        assert len(results) == 2
        assert all(tc.tool_name == "Module-define" for tc in results)

    def test_filter_errors_only(self, engine):
        results = engine.query_tools(session="20260217_085924", errors_only=True)
        assert len(results) == 1
        assert results[0].is_error is True
        assert results[0].tool_name == "Module-define"

    def test_limit(self, engine):
        results = engine.query_tools(session="20260217_085924", limit=2)
        assert len(results) == 2

    def test_input_summary(self, engine):
        results = engine.query_tools(session="20260217_085924")
        # First tool: inspect with module_path and depth
        assert 'module_path=""' in results[0].input_summary
        assert "depth=2" in results[0].input_summary

    def test_nonexistent_session(self, engine):
        results = engine.query_tools(session="99999999_999999")
        assert results == []

    def test_api_index_tracked(self, engine):
        results = engine.query_tools(session="20260217_085924")
        assert results[0].api_index == 1  # call at index 1
        assert results[1].api_index == 2  # call at index 2
        assert results[2].api_index == 3  # call at index 3


# ---------------------------------------------------------------------------
# _extract_tool_calls
# ---------------------------------------------------------------------------

class TestExtractToolCalls:

    def test_empty_records(self):
        assert _extract_tool_calls([]) == []

    def test_no_tool_use(self):
        records = [
            {"type": "session"},
            {"type": "call", "response": {"content": [{"type": "text"}]}},
        ]
        assert _extract_tool_calls(records) == []

    def test_tool_use_without_result(self):
        records = [
            {"type": "call", "response": {"content": [
                {"type": "tool_use", "id": "x", "name": "foo", "input": {"a": "b"}}
            ]}},
        ]
        result = _extract_tool_calls(records)
        assert len(result) == 1
        assert result[0].tool_name == "foo"
        assert result[0].result_summary == ""  # no result found


# ---------------------------------------------------------------------------
# _summarize_tool_input
# ---------------------------------------------------------------------------

class TestSummarizeToolInput:

    def test_short_values(self):
        result = _summarize_tool_input({"name": "foo", "count": 3})
        assert result == 'name="foo", count=3'

    def test_long_string_truncated(self):
        result = _summarize_tool_input({"code": "x" * 50}, max_value_len=30)
        assert '...' in result
        assert len(result) < 80

    def test_multiline_string(self):
        result = _summarize_tool_input({"source": "line1\nline2\nline3"})
        assert "3 lines" in result

    def test_only_first_two_keys(self):
        result = _summarize_tool_input({"a": 1, "b": 2, "c": 3})
        assert "a=1" in result
        assert "b=2" in result
        assert "c=3" not in result

    def test_empty_input(self):
        assert _summarize_tool_input({}) == ""


# ---------------------------------------------------------------------------
# _build_tool_use_map & _make_verbose_lines
# ---------------------------------------------------------------------------

class TestBuildToolUseMap:

    def test_extracts_tool_uses(self):
        data = {"response": {"content": [
            {"type": "tool_use", "id": "abc", "name": "my_tool"},
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "id": "def", "name": "other_tool"},
        ]}}
        result = _build_tool_use_map(data)
        assert result == {"abc": "my_tool", "def": "other_tool"}

    def test_no_tool_uses(self):
        data = {"response": {"content": [{"type": "text"}]}}
        assert _build_tool_use_map(data) == {}


class TestMakeVerboseLines:

    def test_tool_use_response(self):
        data = {"response": {
            "stop_reason": "tool_use",
            "content": [
                {"type": "tool_use", "id": "x", "name": "Module-inspect", "input": {"module_path": "foo"}},
            ],
        }}
        lines = _make_verbose_lines(data)
        assert len(lines) == 1
        assert "Module-inspect" in lines[0]
        assert 'module_path="foo"' in lines[0]

    def test_non_tool_use_response(self):
        data = {"response": {"stop_reason": "end_turn", "content": []}}
        assert _make_verbose_lines(data) == []


# ---------------------------------------------------------------------------
# _make_api_summary with tool_result association (P3)
# ---------------------------------------------------------------------------

class TestApiSummaryToolResult:

    def test_tool_result_annotated_with_name(self):
        prev_map = {"tu_1": "Module-inspect"}
        data = {
            "type": "call",
            "input": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"},
            ]},
            "response": {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"},
        }
        summary = _make_api_summary(data, prev_map)
        assert "tool_result:Module-inspect" in summary

    def test_tool_result_error_annotated(self):
        prev_map = {"tu_2": "Module-define"}
        data = {
            "type": "call",
            "input": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_2", "is_error": True, "content": "fail"},
            ]},
            "response": {"content": [{"type": "text", "text": "retrying"}], "stop_reason": "end_turn"},
        }
        summary = _make_api_summary(data, prev_map)
        assert "Module-define:error" in summary

    def test_multiple_tool_results(self):
        prev_map = {"tu_a": "foo", "tu_b": "bar"}
        data = {
            "type": "call",
            "input": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_a", "content": "ok"},
                {"type": "tool_result", "tool_use_id": "tu_b", "content": "ok"},
            ]},
            "response": {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"},
        }
        summary = _make_api_summary(data, prev_map)
        assert "foo" in summary
        assert "bar" in summary

    def test_no_prev_map_falls_back(self):
        data = {
            "type": "call",
            "input": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"},
            ]},
            "response": {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"},
        }
        # Without prev_tool_map, should fall back to original format
        summary = _make_api_summary(data)
        assert "tool_result" in summary


# ---------------------------------------------------------------------------
# Sessions statistics (P4)
# ---------------------------------------------------------------------------

class TestSessionStats:

    def test_session_has_tool_counts(self, tmp_path):
        (tmp_path / "20260217_085924-api.jsonl").write_text(API_TOOLS_CONTENT, encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        sessions = engine.list_sessions()
        assert len(sessions) == 1
        s = sessions[0]
        assert s.tool_ok_count == 2  # tu_1 and tu_3 succeeded
        assert s.tool_err_count == 1  # tu_2 failed

    def test_session_has_duration(self, tmp_path):
        (tmp_path / "20260217_085924-api.jsonl").write_text(API_TOOLS_CONTENT, encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        sessions = engine.list_sessions()
        s = sessions[0]
        # From 00:59:24 to 00:59:40 = 16 seconds
        assert 15 <= s.duration_seconds <= 17

    def test_session_no_api_file(self, tmp_path):
        (tmp_path / "20260217_085924.log").write_text("line\n", encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        sessions = engine.list_sessions()
        s = sessions[0]
        assert s.tool_ok_count == -1
        assert s.tool_err_count == -1
        assert s.duration_seconds == -1


# ---------------------------------------------------------------------------
# Verbose API output (P1) - integration via query_api
# ---------------------------------------------------------------------------

class TestApiVerbose:

    @pytest.fixture
    def engine(self, tmp_path):
        (tmp_path / "20260217_085924-api.jsonl").write_text(API_TOOLS_CONTENT, encoding="utf-8")
        return LogQueryEngine(tmp_path)

    def test_verbose_includes_tool_lines(self, engine):
        results = engine.query_api(session="20260217_085924", verbose=True, limit=100)
        # Call #1 (index 1) has tool_use → should have verbose lines
        call_1 = results[1]
        assert len(call_1.verbose_lines) == 1
        assert "Module-inspect" in call_1.verbose_lines[0]

    def test_non_verbose_has_no_lines(self, engine):
        results = engine.query_api(session="20260217_085924", verbose=False, limit=100)
        for call in results:
            assert call.verbose_lines == []

    def test_end_turn_no_verbose(self, engine):
        results = engine.query_api(session="20260217_085924", verbose=True, limit=100)
        # Last call (index 4) has stop_reason=end_turn → no verbose lines
        last = results[-1]
        assert last.verbose_lines == []


# ---------------------------------------------------------------------------
# CLI tests for new features
# ---------------------------------------------------------------------------

class TestCLITools:

    def test_tools_command(self, tmp_path, capsys):
        (tmp_path / "20260217_085924-api.jsonl").write_text(API_TOOLS_CONTENT, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "tools", "-s", "20260217_085924"])
        output = capsys.readouterr().out
        assert "Module-inspect" in output
        assert "Module-define" in output
        assert "#01" in output

    def test_tools_errors_only(self, tmp_path, capsys):
        (tmp_path / "20260217_085924-api.jsonl").write_text(API_TOOLS_CONTENT, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "tools", "-s", "20260217_085924", "--errors"])
        output = capsys.readouterr().out
        assert "error" in output.lower()
        # Only the error call should show
        lines = [l for l in output.strip().split("\n") if l.strip()]
        assert len(lines) == 1

    def test_tools_filter_by_name(self, tmp_path, capsys):
        (tmp_path / "20260217_085924-api.jsonl").write_text(API_TOOLS_CONTENT, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "tools", "-s", "20260217_085924", "-t", "Module-inspect"])
        output = capsys.readouterr().out
        assert "Module-inspect" in output
        assert "Module-define" not in output

    def test_tools_empty(self, tmp_path, capsys):
        content = '{"type":"session","ts":"2026-02-17T00:00:00Z","model":"m","tools":[]}\n'
        (tmp_path / "20260217_085924-api.jsonl").write_text(content, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "tools", "-s", "20260217_085924"])
        output = capsys.readouterr().out
        assert "No tool calls found" in output

    def test_api_verbose_flag(self, tmp_path, capsys):
        (tmp_path / "20260217_085924-api.jsonl").write_text(API_TOOLS_CONTENT, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "api", "-s", "20260217_085924", "-v", "-n", "100"])
        output = capsys.readouterr().out
        # Should have indented tool call lines
        assert "Module-inspect(" in output
        assert "Module-define(" in output

    def test_sessions_shows_stats(self, tmp_path, capsys):
        (tmp_path / "20260217_085924-api.jsonl").write_text(API_TOOLS_CONTENT, encoding="utf-8")
        (tmp_path / "20260217_085924.log").write_text("line\n", encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "sessions"])
        output = capsys.readouterr().out
        assert "Tools(ok/err)" in output
        assert "Duration" in output
        assert "2/1" in output  # 2 ok, 1 error


# ---------------------------------------------------------------------------
# 多行日志预览 / 展开
# ---------------------------------------------------------------------------

MULTILINE_LOG = """\
2026-02-17 08:59:24,301 INFO     mutagent.test - single line message
2026-02-17 08:59:25,000 ERROR    mutagent.agent - Traceback (most recent call last):
\t  File "agent.py", line 10, in run
\t    result = self._call()
\t  File "agent.py", line 20, in _call
\t    return provider.send()
\tConnectionError: connection refused
"""


class TestMultilinePreview:

    @pytest.fixture
    def engine(self, tmp_path):
        (tmp_path / "20260217_085924.log").write_text(MULTILINE_LOG, encoding="utf-8")
        return LogQueryEngine(tmp_path)

    def test_default_preview_shows_3_lines(self, tmp_path, capsys):
        (tmp_path / "20260217_085924.log").write_text(MULTILINE_LOG, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "logs", "-s", "20260217_085924", "-l", "ERROR"])
        output = capsys.readouterr().out
        lines = output.strip().split("\n")
        # 首行 + 2 续行 + 提示行 = 4 行
        assert "Traceback" in lines[0]
        assert "File" in lines[1]
        assert "+3 lines" in lines[-1] or "use -e to expand" in lines[-1]

    def test_expand_shows_all_lines(self, tmp_path, capsys):
        (tmp_path / "20260217_085924.log").write_text(MULTILINE_LOG, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "logs", "-s", "20260217_085924", "-l", "ERROR", "-e"])
        output = capsys.readouterr().out
        assert "ConnectionError" in output
        assert "+3 lines" not in output
        assert "use -e to expand" not in output

    def test_single_line_message_no_preview_hint(self, tmp_path, capsys):
        (tmp_path / "20260217_085924.log").write_text(MULTILINE_LOG, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "logs", "-s", "20260217_085924", "-p", "single line"])
        output = capsys.readouterr().out
        assert "single line message" in output
        assert "use -e to expand" not in output

    def test_continuation_lines_aligned(self, tmp_path, capsys):
        (tmp_path / "20260217_085924.log").write_text(MULTILINE_LOG, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "logs", "-s", "20260217_085924", "-l", "ERROR", "-e"])
        output = capsys.readouterr().out
        lines = output.strip().split("\n")
        # 续行应以 "     |" 开头
        for line in lines[1:]:
            assert line.startswith("     |")


# ---------------------------------------------------------------------------
# --logger 过滤
# ---------------------------------------------------------------------------

LOGGER_FILTER_LOG = """\
2026-02-17 08:59:24,301 INFO     mutbot.web.server - server started
2026-02-17 08:59:25,000 DEBUG    mutbot.web.server.auth - checking token
2026-02-17 08:59:26,000 INFO     mutagent.builtins.agent_impl - agent ready
2026-02-17 08:59:27,000 WARNING  mutbot.proxy.routes - timeout
"""


class TestLoggerFilter:

    def test_engine_logger_name_filter(self, tmp_path):
        (tmp_path / "20260217_085924.log").write_text(LOGGER_FILTER_LOG, encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        results = engine.query_logs(session="20260217_085924", logger_name="mutbot.web.server", limit=100)
        assert len(results) == 2
        assert results[0].logger_name == "mutbot.web.server"
        assert results[1].logger_name == "mutbot.web.server.auth"

    def test_engine_logger_exact_match(self, tmp_path):
        (tmp_path / "20260217_085924.log").write_text(LOGGER_FILTER_LOG, encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        results = engine.query_logs(session="20260217_085924", logger_name="mutbot.proxy.routes", limit=100)
        assert len(results) == 1
        assert results[0].logger_name == "mutbot.proxy.routes"

    def test_engine_no_match(self, tmp_path):
        (tmp_path / "20260217_085924.log").write_text(LOGGER_FILTER_LOG, encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        results = engine.query_logs(session="20260217_085924", logger_name="nonexistent", limit=100)
        assert len(results) == 0

    def test_cli_logger_flag(self, tmp_path, capsys):
        (tmp_path / "20260217_085924.log").write_text(LOGGER_FILTER_LOG, encoding="utf-8")
        cli_main(["--dir", str(tmp_path), "logs", "-s", "20260217_085924", "--logger", "mutbot.web.server"])
        output = capsys.readouterr().out
        assert "server started" in output
        assert "checking token" in output
        assert "agent ready" not in output
        assert "timeout" not in output

    def test_logstore_logger_name_filter(self):
        store = LogStore()
        store.append(LogEntry(timestamp=1.0, level="INFO", logger_name="mutbot.web.server", message="a"))
        store.append(LogEntry(timestamp=2.0, level="INFO", logger_name="mutbot.web.server.auth", message="b"))
        store.append(LogEntry(timestamp=3.0, level="INFO", logger_name="mutagent.agent", message="c"))
        results = store.query(logger_name="mutbot.web.server")
        assert len(results) == 2
        assert results[0].message == "b"  # newest first
        assert results[1].message == "a"


# ---------------------------------------------------------------------------
# --tail 模式基础测试
# ---------------------------------------------------------------------------

class TestTailMode:

    def test_tail_no_log_file(self, tmp_path, capsys):
        """Tail mode should print error if no log file found."""
        cli_main(["--dir", str(tmp_path), "logs", "-f", "-s", "nonexistent"])
        output = capsys.readouterr().out
        assert "No log file found" in output


# ---------------------------------------------------------------------------
# Session 发现兼容性（支持 session_id 格式文件名）
# ---------------------------------------------------------------------------

class TestSessionDiscovery:

    def test_timestamp_sessions(self, tmp_path):
        """Traditional YYYYMMDD_HHMMSS format."""
        (tmp_path / "20260217_085924.log").write_text("line\n", encoding="utf-8")
        (tmp_path / "20260217_085924-api.jsonl").write_text('{"type":"session"}\n', encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        sessions = engine.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].timestamp == "20260217_085924"

    def test_session_id_format(self, tmp_path):
        """Session files with session- prefix and hex id (mutbot per-session)."""
        (tmp_path / "session-20260217_085924-a1b2c3d4e5f6-api.jsonl").write_text('{"type":"session"}\n{"type":"call"}\n', encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        sessions = engine.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].timestamp == "session-20260217_085924-a1b2c3d4e5f6"
        assert sessions[0].api_file is not None
        assert sessions[0].api_calls == 1

    def test_server_log_with_prefix(self, tmp_path):
        """Server log files with server- prefix."""
        (tmp_path / "server-20260217_085924.log").write_text("line\n", encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        sessions = engine.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].timestamp == "server-20260217_085924"

    def test_mixed_formats(self, tmp_path):
        """Server log and session files in same directory."""
        (tmp_path / "server-20260217_085924.log").write_text("line\n", encoding="utf-8")
        (tmp_path / "session-20260217_100000-abc123def456-api.jsonl").write_text('{"type":"session"}\n', encoding="utf-8")
        (tmp_path / "session-20260217_110000-def789abc012-api.jsonl").write_text('{"type":"session"}\n', encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        sessions = engine.list_sessions()
        assert len(sessions) == 3

    def test_non_log_files_ignored(self, tmp_path):
        """Non-log files should be ignored."""
        (tmp_path / "readme.txt").write_text("hello\n", encoding="utf-8")
        (tmp_path / "config.json").write_text("{}\n", encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        sessions = engine.list_sessions()
        assert len(sessions) == 0

    def test_resolve_by_session_id(self, tmp_path):
        """Can query logs by session prefix."""
        content = "2026-02-17 08:59:24,301 INFO     test - message from session\n"
        (tmp_path / "session-20260217_085924-abc123.log").write_text(content, encoding="utf-8")
        engine = LogQueryEngine(tmp_path)
        results = engine.query_logs(session="session-20260217_085924-abc123", limit=10)
        assert len(results) == 1
        assert "message from session" in results[0].message
