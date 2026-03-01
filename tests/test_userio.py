"""Tests for Content, UserIO, and BlockHandler declarations and implementations."""

from io import StringIO
from unittest.mock import patch

import pytest

import mutagent
from mutagent.messages import Content, StreamEvent, ToolUseBlock, TextBlock
from mutagent.userio import BlockHandler, UserIO
from mutobj.core import DeclarationMeta, _DECLARED_METHODS

import mutagent.builtins.userio_impl  # noqa: F401  -- register @impl


# ---------------------------------------------------------------------------
# Content dataclass tests
# ---------------------------------------------------------------------------

class TestContent:

    def test_creation(self):
        c = Content(type="tasks", body="- [x] done\n- [ ] todo")
        assert c.type == "tasks"
        assert c.body == "- [x] done\n- [ ] todo"
        assert c.target == ""
        assert c.source == ""
        assert c.metadata == {}

    def test_full_creation(self):
        c = Content(
            type="code",
            body="print('hello')",
            target="main",
            source="agent-coder",
            metadata={"lang": "python", "file": "test.py"},
        )
        assert c.type == "code"
        assert c.target == "main"
        assert c.source == "agent-coder"
        assert c.metadata["lang"] == "python"

    def test_default_metadata_independent(self):
        c1 = Content(type="status")
        c2 = Content(type="status")
        c1.metadata["key"] = "value"
        assert "key" not in c2.metadata

    def test_equality(self):
        c1 = Content(type="tasks", body="hello")
        c2 = Content(type="tasks", body="hello")
        assert c1 == c2

    def test_inequality(self):
        c1 = Content(type="tasks", body="hello")
        c2 = Content(type="tasks", body="world")
        assert c1 != c2


# ---------------------------------------------------------------------------
# BlockHandler declaration tests
# ---------------------------------------------------------------------------

class TestBlockHandlerDeclaration:

    def test_inherits_from_declaration(self):
        assert issubclass(BlockHandler, mutagent.Declaration)

    def test_uses_declaration_meta(self):
        assert isinstance(BlockHandler, DeclarationMeta)

    def test_declared_methods(self):
        declared = getattr(BlockHandler, _DECLARED_METHODS, set())
        assert "on_start" in declared
        assert "on_line" in declared
        assert "on_end" in declared
        assert "render" in declared

    def test_has_block_type_attribute(self):
        handler = BlockHandler(block_type="test")
        assert handler.block_type == "test"


# ---------------------------------------------------------------------------
# UserIO declaration tests
# ---------------------------------------------------------------------------

class TestUserIODeclaration:

    def test_inherits_from_declaration(self):
        assert issubclass(UserIO, mutagent.Declaration)

    def test_uses_declaration_meta(self):
        assert isinstance(UserIO, DeclarationMeta)

    def test_declared_methods(self):
        declared = getattr(UserIO, _DECLARED_METHODS, set())
        assert "render_event" in declared
        assert "present" in declared
        assert "read_input" in declared
        assert "confirm_exit" in declared
        assert "input_stream" in declared

    def test_has_block_handlers_attribute(self):
        userio = UserIO(block_handlers={})
        assert userio.block_handlers == {}


# ---------------------------------------------------------------------------
# BlockHandler default implementation tests
# ---------------------------------------------------------------------------

class TestBlockHandlerDefaultImpl:

    def test_on_start_noop(self):
        handler = BlockHandler(block_type="test")
        handler.on_start({})  # should not raise

    def test_on_line_prints(self, capsys):
        handler = BlockHandler(block_type="test")
        handler.on_line("hello world")
        captured = capsys.readouterr()
        assert "hello world" in captured.out

    def test_on_end_noop(self):
        handler = BlockHandler(block_type="test")
        handler.on_end()  # should not raise

    def test_render_prints_body(self, capsys):
        handler = BlockHandler(block_type="test")
        content = Content(type="test", body="test body")
        handler.render(content)
        captured = capsys.readouterr()
        assert "test body" in captured.out

    def test_render_empty_body(self, capsys):
        handler = BlockHandler(block_type="test")
        content = Content(type="test", body="")
        handler.render(content)
        captured = capsys.readouterr()
        assert captured.out == ""


# ---------------------------------------------------------------------------
# UserIO render_event tests
# ---------------------------------------------------------------------------

class TestUserIORenderEvent:

    @pytest.fixture
    def userio(self):
        return UserIO(block_handlers={})

    def test_text_delta(self, userio, capsys):
        event = StreamEvent(type="text_delta", text="Hello world")
        userio.render_event(event)
        captured = capsys.readouterr()
        assert captured.out == "Hello world"

    def test_tool_exec_start_with_args(self, userio, capsys):
        tc = ToolUseBlock(id="tc_1", name="Module-inspect", input={"module_path": "mutagent"})
        event = StreamEvent(type="tool_exec_start", tool_call=tc)
        userio.render_event(event)
        captured = capsys.readouterr()
        assert "Module-inspect" in captured.out
        assert 'module_path="mutagent"' in captured.out

    def test_tool_exec_start_no_args(self, userio, capsys):
        tc = ToolUseBlock(id="tc_1", name="Module-inspect", input={})
        event = StreamEvent(type="tool_exec_start", tool_call=tc)
        userio.render_event(event)
        captured = capsys.readouterr()
        assert "Module-inspect()" in captured.out

    def test_tool_exec_end(self, userio, capsys):
        tc = ToolUseBlock(id="tc_1", name="Module-inspect", input={},
                          status="done", result="Success result")
        event = StreamEvent(type="tool_exec_end", tool_call=tc)
        userio.render_event(event)
        captured = capsys.readouterr()
        assert "\u2192" in captured.out
        assert "Success result" in captured.out

    def test_tool_exec_end_error(self, userio, capsys):
        tc = ToolUseBlock(id="tc_1", name="Module-inspect", input={},
                          status="done", result="Failed", is_error=True)
        event = StreamEvent(type="tool_exec_end", tool_call=tc)
        userio.render_event(event)
        captured = capsys.readouterr()
        assert "\u2192" in captured.out
        assert "Failed" in captured.out

    def test_tool_exec_end_long_content_truncated(self, userio, capsys):
        tc = ToolUseBlock(id="tc_1", name="Module-inspect", input={},
                          status="done", result="\n".join(f"line {i}" for i in range(20)))
        event = StreamEvent(type="tool_exec_end", tool_call=tc)
        userio.render_event(event)
        captured = capsys.readouterr()
        assert "..." in captured.out
        assert "+16 lines" in captured.out

    def test_error_event(self, userio, capsys):
        event = StreamEvent(type="error", error="API failed")
        userio.render_event(event)
        captured = capsys.readouterr()
        assert "API failed" in captured.err

    def test_turn_done(self, userio, capsys):
        event = StreamEvent(type="turn_done")
        userio.render_event(event)
        captured = capsys.readouterr()
        assert captured.out == "\n"

    def test_unknown_event_type_noop(self, userio, capsys):
        event = StreamEvent(type="response_done")
        userio.render_event(event)
        captured = capsys.readouterr()
        assert captured.out == ""


# ---------------------------------------------------------------------------
# UserIO present tests
# ---------------------------------------------------------------------------

class TestUserIOPresent:

    @pytest.fixture
    def userio(self):
        return UserIO(block_handlers={})

    def test_present_without_handler_prints_body(self, userio, capsys):
        content = Content(type="unknown", body="some text")
        userio.present(content)
        captured = capsys.readouterr()
        assert "some text" in captured.out

    def test_present_with_source_prefix(self, userio, capsys):
        content = Content(type="unknown", body="message", source="agent-main")
        userio.present(content)
        captured = capsys.readouterr()
        assert "[agent-main]" in captured.out
        assert "message" in captured.out

    def test_present_empty_body(self, userio, capsys):
        content = Content(type="status", body="")
        userio.present(content)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_present_with_handler(self, capsys):
        handler = BlockHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        content = Content(type="tasks", body="- [x] done")
        userio.present(content)
        captured = capsys.readouterr()
        assert "- [x] done" in captured.out


# ---------------------------------------------------------------------------
# UserIO read_input tests
# ---------------------------------------------------------------------------

class TestUserIOReadInput:

    def test_read_input(self):
        userio = UserIO(block_handlers={})
        with patch("builtins.input", return_value="  hello  "):
            result = userio.read_input()
        assert result == "hello"


# ---------------------------------------------------------------------------
# UserIO confirm_exit tests
# ---------------------------------------------------------------------------

class TestUserIOConfirmExit:

    def test_confirm_yes(self):
        userio = UserIO(block_handlers={})
        with patch("builtins.input", return_value="y"):
            assert userio.confirm_exit() is True

    def test_confirm_empty_is_yes(self):
        userio = UserIO(block_handlers={})
        with patch("builtins.input", return_value=""):
            assert userio.confirm_exit() is True

    def test_confirm_no(self):
        userio = UserIO(block_handlers={})
        with patch("builtins.input", return_value="n"):
            assert userio.confirm_exit() is False

    def test_confirm_exhaustion_returns_true(self):
        userio = UserIO(block_handlers={})
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            assert userio.confirm_exit() is True


# ---------------------------------------------------------------------------
# Block detection state machine tests
# ---------------------------------------------------------------------------

class _RecordingHandler(BlockHandler):
    """A BlockHandler that records all calls for testing."""

    def __init__(self, block_type="test"):
        super().__init__(block_type=block_type)
        object.__setattr__(self, '_calls', [])

    @property
    def calls(self):
        return self._calls

    def on_start(self, metadata):
        self._calls.append(('on_start', metadata))

    def on_line(self, text):
        self._calls.append(('on_line', text))

    def on_end(self):
        self._calls.append(('on_end',))

    def render(self, content):
        self._calls.append(('render', content))


def _send_text(userio, text):
    """Send a text_delta event to the userio."""
    userio.render_event(StreamEvent(type="text_delta", text=text))


def _send_turn_done(userio):
    """Send a turn_done event to the userio."""
    userio.render_event(StreamEvent(type="turn_done"))


class TestBlockDetectionBasic:

    def test_simple_block_detected(self, capsys):
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        _send_text(userio, "```mutagent:tasks\n- [x] done\n- [ ] todo\n```\n")
        assert ('on_start', {'type': 'tasks'}) in handler.calls
        assert ('on_line', '- [x] done') in handler.calls
        assert ('on_line', '- [ ] todo') in handler.calls
        assert ('on_end',) in handler.calls

    def test_text_before_block(self, capsys):
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        _send_text(userio, "Hello world\n```mutagent:tasks\n- item\n```\n")
        captured = capsys.readouterr()
        assert "Hello world" in captured.out
        assert ('on_line', '- item') in handler.calls

    def test_text_after_block(self, capsys):
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        _send_text(userio, "```mutagent:tasks\n- item\n```\nAfter block\n")
        captured = capsys.readouterr()
        assert "After block" in captured.out
        assert ('on_end',) in handler.calls

    def test_unknown_block_type_passes_through(self, capsys):
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        _send_text(userio, "```mutagent:unknown\nsome content\n```\n")
        captured = capsys.readouterr()
        # Unknown type should be printed as-is (no handler found)
        assert "```mutagent:unknown" in captured.out
        assert "some content" in captured.out
        assert len(handler.calls) == 0

    def test_regular_code_block_passes_through(self, capsys):
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        _send_text(userio, "```python\nprint('hello')\n```\n")
        captured = capsys.readouterr()
        assert "```python" in captured.out
        assert "print('hello')" in captured.out
        assert len(handler.calls) == 0

    def test_no_handlers_fast_path(self, capsys):
        userio = UserIO(block_handlers={})
        _send_text(userio, "```mutagent:tasks\n- item\n```\n")
        captured = capsys.readouterr()
        # Everything should be printed as-is
        assert "```mutagent:tasks" in captured.out
        assert "- item" in captured.out


class TestBlockDetectionStreaming:
    """Test block detection with fragmented text_delta events."""

    def test_split_across_events(self, capsys):
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        # Split the block opening across multiple events
        _send_text(userio, "```mut")
        _send_text(userio, "agent:tasks\n")
        _send_text(userio, "- [x] done\n")
        _send_text(userio, "```\n")
        assert ('on_start', {'type': 'tasks'}) in handler.calls
        assert ('on_line', '- [x] done') in handler.calls
        assert ('on_end',) in handler.calls

    def test_line_split_across_events(self, capsys):
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        _send_text(userio, "```mutagent:tasks\n- [x")
        _send_text(userio, "] done\n```\n")
        assert ('on_line', '- [x] done') in handler.calls

    def test_normal_text_buffers_until_newline(self, capsys):
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        # Partial lines are buffered until newline for correct highlighting
        _send_text(userio, "Hello")
        captured = capsys.readouterr()
        assert captured.out == ""
        # Newline triggers output
        _send_text(userio, " world\n")
        captured = capsys.readouterr()
        assert "Hello world" in captured.out

    def test_backtick_prefix_buffers(self, capsys):
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        # Single backtick buffers (all partial lines buffer now)
        _send_text(userio, "`")
        captured = capsys.readouterr()
        assert captured.out == ""
        # Complete the line (not a block)
        _send_text(userio, "code`\n")
        captured = capsys.readouterr()
        assert "`code`" in captured.out

    def test_multiple_blocks(self, capsys):
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        _send_text(userio, "```mutagent:tasks\n- a\n```\nMiddle\n```mutagent:tasks\n- b\n```\n")
        captured = capsys.readouterr()
        assert "Middle" in captured.out
        on_start_count = sum(1 for c in handler.calls if c[0] == 'on_start')
        on_end_count = sum(1 for c in handler.calls if c[0] == 'on_end')
        assert on_start_count == 2
        assert on_end_count == 2
        assert ('on_line', '- a') in handler.calls
        assert ('on_line', '- b') in handler.calls


class TestStreamingMarkdownHighlight:
    """Test that Markdown highlighting works correctly with fragmented streaming."""

    def test_heading_split_across_events(self, capsys):
        """Heading prefix arrives separately from content."""
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        _send_text(userio, "## ")
        # Should be buffered, not flushed
        captured = capsys.readouterr()
        assert captured.out == ""
        _send_text(userio, "Design\n")
        captured = capsys.readouterr()
        assert "## " in captured.out
        assert "Design" in captured.out

    def test_list_split_across_events(self, capsys):
        """List marker arrives separately from content."""
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        _send_text(userio, "- ")
        captured = capsys.readouterr()
        assert captured.out == ""
        _send_text(userio, "item\n")
        captured = capsys.readouterr()
        assert "- " in captured.out
        assert "item" in captured.out

    def test_turn_done_flushes_partial_line(self, capsys):
        """turn_done should flush any buffered partial line."""
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        _send_text(userio, "partial text")
        captured = capsys.readouterr()
        assert captured.out == ""
        _send_turn_done(userio)
        captured = capsys.readouterr()
        assert "partial text" in captured.out

    def test_complete_line_outputs_immediately(self, capsys):
        """A complete line (with newline) should output right away."""
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        _send_text(userio, "complete line\n")
        captured = capsys.readouterr()
        assert "complete line" in captured.out

    def test_no_premature_partial_flush(self, capsys):
        """Ensure partial lines don't leak out prematurely."""
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        # Send several fragments without newline
        _send_text(userio, "## ")
        _send_text(userio, "Head")
        _send_text(userio, "ing")
        captured = capsys.readouterr()
        assert captured.out == ""
        # Now complete the line
        _send_text(userio, "\n")
        captured = capsys.readouterr()
        assert "## Heading" in captured.out


class TestBlockDetectionEdgeCases:

    def test_empty_block(self, capsys):
        handler = _RecordingHandler(block_type="status")
        userio = UserIO(block_handlers={"status": handler})
        _send_text(userio, "```mutagent:status\n```\n")
        assert ('on_start', {'type': 'status'}) in handler.calls
        assert ('on_end',) in handler.calls
        on_line_calls = [c for c in handler.calls if c[0] == 'on_line']
        assert len(on_line_calls) == 0

    def test_block_with_metadata_in_opening(self, capsys):
        handler = _RecordingHandler(block_type="code")
        userio = UserIO(block_handlers={"code": handler})
        _send_text(userio, "```mutagent:code lang=python file=test.py\nprint('hi')\n```\n")
        start_call = [c for c in handler.calls if c[0] == 'on_start'][0]
        assert start_call[1]['type'] == 'code'
        assert 'raw' in start_call[1]
        assert 'lang=python' in start_call[1]['raw']

    def test_turn_done_resets_state(self, capsys):
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        # Start a block but don't close it
        _send_text(userio, "```mutagent:tasks\n- item\n")
        # turn_done should reset state and call on_end
        _send_turn_done(userio)
        assert ('on_end',) in handler.calls

    def test_closing_fence_with_trailing_whitespace(self, capsys):
        handler = _RecordingHandler(block_type="tasks")
        userio = UserIO(block_handlers={"tasks": handler})
        _send_text(userio, "```mutagent:tasks\n- item\n```   \n")
        assert ('on_end',) in handler.calls

    def test_multiple_handlers(self, capsys):
        tasks_handler = _RecordingHandler(block_type="tasks")
        status_handler = _RecordingHandler(block_type="status")
        userio = UserIO(block_handlers={
            "tasks": tasks_handler,
            "status": status_handler,
        })
        _send_text(userio, "```mutagent:tasks\n- item\n```\n```mutagent:status\nOK\n```\n")
        assert ('on_line', '- item') in tasks_handler.calls
        assert ('on_line', 'OK') in status_handler.calls
        assert len(status_handler.calls) == 0 or ('on_line', '- item') not in status_handler.calls


# ---------------------------------------------------------------------------
# Built-in BlockHandler tests
# ---------------------------------------------------------------------------

import mutagent.builtins.block_handlers as bh
from mutagent.builtins.userio_impl import discover_block_handlers


class TestBlockHandlerDiscovery:

    def test_discovers_all_builtin_handlers(self):
        handlers = discover_block_handlers()
        assert "tasks" in handlers
        assert "status" in handlers
        assert "code" in handlers
        assert "thinking" in handlers
        assert "default" in handlers

    def test_handler_instances_are_block_handlers(self):
        handlers = discover_block_handlers()
        for handler in handlers.values():
            assert isinstance(handler, BlockHandler)

    def test_handler_types(self):
        handlers = discover_block_handlers()
        assert isinstance(handlers["tasks"], bh.TasksHandler)
        assert isinstance(handlers["status"], bh.StatusHandler)
        assert isinstance(handlers["code"], bh.CodeHandler)
        assert isinstance(handlers["thinking"], bh.ThinkingHandler)
        assert isinstance(handlers["default"], bh.DefaultHandler)


class TestTasksHandler:

    def test_on_line_prints(self, capsys):
        handler = bh.TasksHandler(block_type="tasks")
        handler.on_line("- [x] done")
        captured = capsys.readouterr()
        assert "- [x] done" in captured.out

    def test_render(self, capsys):
        handler = bh.TasksHandler(block_type="tasks")
        content = Content(type="tasks", body="- [x] done\n- [ ] todo")
        handler.render(content)
        captured = capsys.readouterr()
        assert "- [x] done" in captured.out
        assert "- [ ] todo" in captured.out

    def test_streaming_integration(self, capsys):
        handlers = discover_block_handlers()
        userio = UserIO(block_handlers=handlers)
        _send_text(userio, "```mutagent:tasks\n- [x] done\n- [ ] todo\n```\n")
        captured = capsys.readouterr()
        assert "- [x] done" in captured.out
        assert "- [ ] todo" in captured.out


class TestStatusHandler:

    def test_buffers_then_flushes(self, capsys):
        handler = bh.StatusHandler(block_type="status")
        handler.on_start({})
        # Lines should be buffered, not printed yet
        handler.on_line("Line 1")
        handler.on_line("Line 2")
        captured = capsys.readouterr()
        assert captured.out == ""
        # on_end flushes
        handler.on_end()
        captured = capsys.readouterr()
        assert "Line 1" in captured.out
        assert "Line 2" in captured.out

    def test_render(self, capsys):
        handler = bh.StatusHandler(block_type="status")
        content = Content(type="status", body="All systems go")
        handler.render(content)
        captured = capsys.readouterr()
        assert "All systems go" in captured.out

    def test_streaming_integration(self, capsys):
        handlers = discover_block_handlers()
        userio = UserIO(block_handlers=handlers)
        _send_text(userio, "```mutagent:status\nRunning\n```\n")
        captured = capsys.readouterr()
        assert "Running" in captured.out


class TestCodeHandler:

    def test_wraps_in_code_block(self, capsys):
        handler = bh.CodeHandler(block_type="code")
        handler.on_start({'raw': 'lang=python'})
        handler.on_line("print('hello')")
        handler.on_end()
        captured = capsys.readouterr()
        assert "```lang=python" in captured.out
        assert "print('hello')" in captured.out
        assert captured.out.rstrip().endswith("```")

    def test_render(self, capsys):
        handler = bh.CodeHandler(block_type="code")
        content = Content(type="code", body="x = 1", metadata={"lang": "python"})
        handler.render(content)
        captured = capsys.readouterr()
        assert "```python" in captured.out
        assert "x = 1" in captured.out

    def test_streaming_integration(self, capsys):
        handlers = discover_block_handlers()
        userio = UserIO(block_handlers=handlers)
        _send_text(userio, "```mutagent:code lang=python\nx = 1\n```\n")
        captured = capsys.readouterr()
        assert "x = 1" in captured.out


class TestThinkingHandler:

    def test_on_line_prints(self, capsys):
        handler = bh.ThinkingHandler(block_type="thinking")
        handler.on_line("Considering options...")
        captured = capsys.readouterr()
        assert "Considering options..." in captured.out

    def test_render(self, capsys):
        handler = bh.ThinkingHandler(block_type="thinking")
        content = Content(type="thinking", body="Step 1\nStep 2")
        handler.render(content)
        captured = capsys.readouterr()
        assert "Step 1\nStep 2" in captured.out


class TestDefaultHandler:

    def test_wraps_in_code_block(self, capsys):
        handler = bh.DefaultHandler(block_type="default")
        handler.on_start({'type': 'custom'})
        handler.on_line("content")
        handler.on_end()
        captured = capsys.readouterr()
        assert "```custom" in captured.out
        assert "content" in captured.out
        assert captured.out.rstrip().endswith("```")

    def test_render(self, capsys):
        handler = bh.DefaultHandler(block_type="default")
        content = Content(type="custom", body="raw content")
        handler.render(content)
        captured = capsys.readouterr()
        assert "```custom" in captured.out
        assert "raw content" in captured.out


# ---------------------------------------------------------------------------
# InputEvent.data field tests (Task 4.1)
# ---------------------------------------------------------------------------

from mutagent.messages import InputEvent


class TestInputEventData:

    def test_default_data_is_empty_dict(self):
        event = InputEvent(type="user_message", text="hello")
        assert event.data == {}

    def test_data_with_interactions(self):
        interactions = [{'id': 0, 'type': 'ask', 'question': 'Q?', 'options': [], 'result': None}]
        event = InputEvent(type="user_message", text="hello", data={'interactions': interactions})
        assert event.data['interactions'] == interactions

    def test_data_default_independent(self):
        e1 = InputEvent(type="user_message")
        e2 = InputEvent(type="user_message")
        e1.data['key'] = 'value'
        assert 'key' not in e2.data

    def test_existing_construction_compatible(self):
        event = InputEvent(type="user_message", text="test")
        assert event.type == "user_message"
        assert event.text == "test"
        assert event.data == {}

    def test_equality_with_data(self):
        e1 = InputEvent(type="user_message", text="hi", data={'k': 1})
        e2 = InputEvent(type="user_message", text="hi", data={'k': 1})
        assert e1 == e2

    def test_inequality_with_different_data(self):
        e1 = InputEvent(type="user_message", text="hi", data={'k': 1})
        e2 = InputEvent(type="user_message", text="hi", data={'k': 2})
        assert e1 != e2


# ---------------------------------------------------------------------------
# _parse_ask_block tests (Task 4.2)
# ---------------------------------------------------------------------------

from mutagent.builtins.block_handlers import _parse_ask_block


class TestParseAskBlock:

    def test_standard_format(self):
        lines = ["Which color?", "", "- Red", "- Green", "- Blue"]
        question, options = _parse_ask_block(lines)
        assert question == "Which color?"
        assert options == ["Red", "Green", "Blue"]

    def test_no_options(self):
        lines = ["What is your name?"]
        question, options = _parse_ask_block(lines)
        assert question == "What is your name?"
        assert options == []

    def test_empty_block(self):
        question, options = _parse_ask_block([])
        assert question == ""
        assert options == []

    def test_multiline_question(self):
        lines = ["Line 1", "Line 2", "", "- A", "- B"]
        question, options = _parse_ask_block(lines)
        assert question == "Line 1\nLine 2"
        assert options == ["A", "B"]

    def test_options_only(self):
        lines = ["- A", "- B"]
        question, options = _parse_ask_block(lines)
        assert question == ""
        assert options == ["A", "B"]

    def test_blank_lines_ignored_in_question(self):
        lines = ["", "Question text", "", "- Option"]
        question, options = _parse_ask_block(lines)
        assert question == "Question text"
        assert options == ["Option"]


# ---------------------------------------------------------------------------
# AskHandler tests (Task 4.2)
# ---------------------------------------------------------------------------

class TestAskHandler:

    def test_on_line_prints_and_buffers(self, capsys):
        handler = bh.AskHandler(block_type="ask")
        handler.on_start({})
        handler.on_line("Which color?")
        handler.on_line("- Red")
        captured = capsys.readouterr()
        assert "Which color?" in captured.out
        assert "- Red" in captured.out

    def test_on_end_sets_pending_interaction(self):
        handler = bh.AskHandler(block_type="ask")
        handler.on_start({})
        handler.on_line("Pick one:")
        handler.on_line("- A")
        handler.on_line("- B")
        handler.on_end()
        pending = getattr(handler, '_pending_interaction', None)
        assert pending is not None
        assert pending['type'] == 'ask'
        assert pending['question'] == 'Pick one:'
        assert pending['options'] == ['A', 'B']
        assert pending['result'] is None

    def test_on_end_empty_block(self):
        handler = bh.AskHandler(block_type="ask")
        handler.on_start({})
        handler.on_end()
        pending = getattr(handler, '_pending_interaction', None)
        assert pending is not None
        assert pending['question'] == ''
        assert pending['options'] == []

    def test_render_sets_pending_interaction(self, capsys):
        handler = bh.AskHandler(block_type="ask")
        content = Content(type="ask", body="Question?\n\n- Yes\n- No")
        handler.render(content)
        captured = capsys.readouterr()
        assert "Question?" in captured.out
        pending = getattr(handler, '_pending_interaction', None)
        assert pending is not None
        assert pending['type'] == 'ask'
        assert pending['question'] == 'Question?'
        assert pending['options'] == ['Yes', 'No']

    def test_render_empty_body(self, capsys):
        handler = bh.AskHandler(block_type="ask")
        content = Content(type="ask", body="")
        handler.render(content)
        pending = getattr(handler, '_pending_interaction', None)
        assert pending is not None
        assert pending['question'] == ''

    def test_streaming_integration(self, capsys):
        handlers = discover_block_handlers()
        userio = UserIO(block_handlers=handlers)
        _send_text(userio, "```mutagent:ask\nWhich option?\n- A\n- B\n```\n")
        captured = capsys.readouterr()
        assert "Which option?" in captured.out
        assert "- A" in captured.out
        assert "- B" in captured.out


# ---------------------------------------------------------------------------
# ConfirmHandler tests (Task 4.3)
# ---------------------------------------------------------------------------

class TestConfirmHandler:

    def test_on_line_prints_and_buffers(self, capsys):
        handler = bh.ConfirmHandler(block_type="confirm")
        handler.on_start({})
        handler.on_line("Are you sure?")
        captured = capsys.readouterr()
        assert "Are you sure?" in captured.out

    def test_on_end_sets_pending_interaction(self):
        handler = bh.ConfirmHandler(block_type="confirm")
        handler.on_start({})
        handler.on_line("Proceed with deletion?")
        handler.on_end()
        pending = getattr(handler, '_pending_interaction', None)
        assert pending is not None
        assert pending['type'] == 'confirm'
        assert pending['question'] == 'Proceed with deletion?'
        assert pending['options'] == []
        assert pending['result'] is None

    def test_on_end_multiline(self):
        handler = bh.ConfirmHandler(block_type="confirm")
        handler.on_start({})
        handler.on_line("Line 1")
        handler.on_line("Line 2")
        handler.on_end()
        pending = getattr(handler, '_pending_interaction', None)
        assert pending['question'] == 'Line 1\nLine 2'

    def test_on_end_empty_block(self):
        handler = bh.ConfirmHandler(block_type="confirm")
        handler.on_start({})
        handler.on_end()
        pending = getattr(handler, '_pending_interaction', None)
        assert pending is not None
        assert pending['question'] == ''

    def test_render_sets_pending_interaction(self, capsys):
        handler = bh.ConfirmHandler(block_type="confirm")
        content = Content(type="confirm", body="Are you sure?")
        handler.render(content)
        captured = capsys.readouterr()
        assert "Are you sure?" in captured.out
        pending = getattr(handler, '_pending_interaction', None)
        assert pending is not None
        assert pending['type'] == 'confirm'
        assert pending['question'] == 'Are you sure?'
        assert pending['options'] == []

    def test_streaming_integration(self, capsys):
        handlers = discover_block_handlers()
        userio = UserIO(block_handlers=handlers)
        _send_text(userio, "```mutagent:confirm\nDelete all files?\n```\n")
        captured = capsys.readouterr()
        assert "Delete all files?" in captured.out


# ---------------------------------------------------------------------------
# _transfer_pending_interaction tests (Task 4.4)
# ---------------------------------------------------------------------------

from mutagent.builtins.userio_impl import _transfer_pending_interaction


class TestTransferPendingInteraction:

    def test_transfer_with_pending(self):
        userio = UserIO(block_handlers={})
        handler = bh.AskHandler(block_type="ask")
        object.__setattr__(handler, '_pending_interaction', {
            'type': 'ask', 'question': 'Q?', 'options': [], 'result': None,
        })
        _transfer_pending_interaction(userio, handler)
        interactions = getattr(userio, '_pending_interactions', [])
        assert len(interactions) == 1
        assert interactions[0]['question'] == 'Q?'
        assert getattr(handler, '_pending_interaction', 'NOT_NONE') is None

    def test_transfer_without_pending(self):
        userio = UserIO(block_handlers={})
        handler = BlockHandler(block_type="test")
        _transfer_pending_interaction(userio, handler)
        interactions = getattr(userio, '_pending_interactions', None)
        assert interactions is None or len(interactions) == 0

    def test_multiple_transfers(self):
        userio = UserIO(block_handlers={})
        h1 = bh.AskHandler(block_type="ask")
        h2 = bh.ConfirmHandler(block_type="confirm")
        object.__setattr__(h1, '_pending_interaction', {
            'type': 'ask', 'question': 'Q1?', 'options': ['A'], 'result': None,
        })
        object.__setattr__(h2, '_pending_interaction', {
            'type': 'confirm', 'question': 'Sure?', 'options': [], 'result': None,
        })
        _transfer_pending_interaction(userio, h1)
        _transfer_pending_interaction(userio, h2)
        interactions = getattr(userio, '_pending_interactions', [])
        assert len(interactions) == 2
        assert interactions[0]['type'] == 'ask'
        assert interactions[1]['type'] == 'confirm'

    def test_process_complete_line_transfers(self, capsys):
        """Integration: closing fence triggers transfer via _process_complete_line."""
        handlers = discover_block_handlers()
        userio = UserIO(block_handlers=handlers)
        _send_text(userio, "```mutagent:ask\nQuestion?\n- A\n```\n")
        interactions = getattr(userio, '_pending_interactions', [])
        assert len(interactions) == 1
        assert interactions[0]['type'] == 'ask'

    def test_reset_parse_state_transfers(self, capsys):
        """Integration: turn_done on unclosed block triggers transfer via _reset_parse_state."""
        handlers = discover_block_handlers()
        userio = UserIO(block_handlers=handlers)
        _send_text(userio, "```mutagent:confirm\nReady?\n")
        # Block not closed; turn_done should close and transfer
        _send_turn_done(userio)
        interactions = getattr(userio, '_pending_interactions', [])
        assert len(interactions) == 1
        assert interactions[0]['type'] == 'confirm'

    def test_present_transfers(self, capsys):
        """Integration: present() path triggers transfer."""
        handlers = discover_block_handlers()
        userio = UserIO(block_handlers=handlers)
        content = Content(type="ask", body="Pick?\n- X\n- Y")
        userio.present(content)
        interactions = getattr(userio, '_pending_interactions', [])
        assert len(interactions) == 1
        assert interactions[0]['type'] == 'ask'
        assert interactions[0]['options'] == ['X', 'Y']


# ---------------------------------------------------------------------------
# input_stream integration tests (Task 4.5)
# ---------------------------------------------------------------------------

class TestInputStreamInteractions:

    def test_with_pending_interactions(self):
        handlers = discover_block_handlers()
        userio = UserIO(block_handlers=handlers)
        # Send an ask block to create pending interactions
        _send_text(userio, "```mutagent:ask\nQuestion?\n- A\n- B\n```\n")
        _send_turn_done(userio)
        # Mock read_input to return user text
        with patch.object(type(userio), 'read_input', return_value="my answer"):
            stream = userio.input_stream()
            event = next(stream)
        assert event.text == "my answer"
        assert 'interactions' in event.data
        interactions = event.data['interactions']
        assert len(interactions) == 1
        assert interactions[0]['id'] == 0
        assert interactions[0]['type'] == 'ask'
        assert interactions[0]['question'] == 'Question?'
        assert interactions[0]['options'] == ['A', 'B']
        assert interactions[0]['result'] is None

    def test_without_pending_interactions(self):
        userio = UserIO(block_handlers={})
        with patch.object(type(userio), 'read_input', return_value="hello"):
            stream = userio.input_stream()
            event = next(stream)
        assert event.text == "hello"
        assert event.data == {}

    def test_multiple_pending_collected(self):
        handlers = discover_block_handlers()
        userio = UserIO(block_handlers=handlers)
        # Two blocks
        _send_text(userio, "```mutagent:ask\nQ1?\n- A\n```\n")
        _send_text(userio, "```mutagent:confirm\nSure?\n```\n")
        _send_turn_done(userio)
        with patch.object(type(userio), 'read_input', return_value="yes"):
            stream = userio.input_stream()
            event = next(stream)
        interactions = event.data['interactions']
        assert len(interactions) == 2
        assert interactions[0]['id'] == 0
        assert interactions[0]['type'] == 'ask'
        assert interactions[1]['id'] == 1
        assert interactions[1]['type'] == 'confirm'

    def test_pending_cleared_after_collection(self):
        handlers = discover_block_handlers()
        userio = UserIO(block_handlers=handlers)
        _send_text(userio, "```mutagent:ask\nQ?\n```\n")
        _send_turn_done(userio)
        with patch.object(type(userio), 'read_input', return_value="answer"):
            stream = userio.input_stream()
            next(stream)
        pending = getattr(userio, '_pending_interactions', [])
        assert len(pending) == 0


# ---------------------------------------------------------------------------
# End-to-end integration tests (Task 4.6)
# ---------------------------------------------------------------------------

class TestEndToEnd:

    def test_ask_full_flow(self, capsys):
        """Full flow: text_delta(ask block) -> handler -> pending -> input_stream -> data."""
        handlers = discover_block_handlers()
        userio = UserIO(block_handlers=handlers)
        # Simulate LLM streaming an ask block
        _send_text(userio, "```mutagent:ask\n")
        _send_text(userio, "Which approach?\n")
        _send_text(userio, "- Approach A\n")
        _send_text(userio, "- Approach B\n")
        _send_text(userio, "```\n")
        _send_turn_done(userio)
        captured = capsys.readouterr()
        assert "Which approach?" in captured.out
        assert "- Approach A" in captured.out
        # Simulate user input
        with patch.object(type(userio), 'read_input', return_value="A"):
            stream = userio.input_stream()
            event = next(stream)
        assert event.text == "A"
        assert event.data['interactions'][0]['question'] == 'Which approach?'
        assert event.data['interactions'][0]['options'] == ['Approach A', 'Approach B']

    def test_existing_handlers_unaffected(self, capsys):
        """Existing 5 handlers (tasks/status/code/thinking/default) don't produce pending."""
        handlers = discover_block_handlers()
        userio = UserIO(block_handlers=handlers)
        _send_text(userio, "```mutagent:tasks\n- item\n```\n")
        _send_text(userio, "```mutagent:status\nOK\n```\n")
        _send_text(userio, "```mutagent:code\nx = 1\n```\n")
        _send_text(userio, "```mutagent:thinking\nHmm\n```\n")
        _send_turn_done(userio)
        pending = getattr(userio, '_pending_interactions', None)
        assert pending is None or len(pending) == 0

    def test_handler_discovery_includes_new_handlers(self):
        handlers = discover_block_handlers()
        assert "ask" in handlers
        assert "confirm" in handlers
        assert isinstance(handlers["ask"], bh.AskHandler)
        assert isinstance(handlers["confirm"], bh.ConfirmHandler)
