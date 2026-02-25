"""Tests for mutagent.extras.rich -- Rich enhanced terminal module."""

from io import StringIO
from unittest.mock import patch

import pytest

rich = pytest.importorskip("rich")

import mutagent
import mutagent.builtins.block_handlers as builtin_bh  # register builtins first
import mutagent.extras.rich  # noqa: F401 -- register rich overrides
from mutagent.extras.rich.block_handlers import (
    RichCodeHandler,
    RichStatusHandler,
    RichTasksHandler,
    RichThinkingHandler,
    _parse_lang,
    _tasks_markup,
)
from mutagent.extras.rich import userio_impl as rich_userio
from mutagent.builtins.userio_impl import discover_block_handlers
from mutagent.messages import Content, StreamEvent, ToolCall, ToolResult
from mutagent.userio import BlockHandler, UserIO
from rich.console import Console


# ---------------------------------------------------------------------------
# Module lifecycle: restore basic impls after all rich tests complete
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _restore_basic_impls_after():
    """Rich extras import overrides basic @impl and adds classes to registry.

    After all tests in this module finish, restore the basic terminal impls
    and remove rich handler classes from the class registry so that subsequent
    test modules (e.g. test_userio.py) are not affected.
    """
    yield
    # Unregister rich @impls -- restores the basic impls as chain top
    mutagent.unregister_module_impls("mutagent.extras.rich.userio_impl")
    # Remove rich handler classes from _class_registry
    from mutobj.core import _class_registry
    rich_classes = {RichTasksHandler, RichStatusHandler,
                    RichCodeHandler, RichThinkingHandler}
    keys_to_remove = [k for k, v in _class_registry.items()
                      if v in rich_classes]
    for k in keys_to_remove:
        del _class_registry[k]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_console() -> tuple[Console, StringIO]:
    """Create a Console that writes to a StringIO for test capture."""
    buf = StringIO()
    console = Console(file=buf, highlight=False, force_terminal=True, width=120)
    return console, buf


def _send_text(userio, text):
    """Send a text_delta event."""
    userio.render_event(StreamEvent(type="text_delta", text=text))


def _send_turn_done(userio):
    """Send a turn_done event."""
    userio.render_event(StreamEvent(type="turn_done"))


def _make_userio_with_rich_handlers():
    """Create a UserIO with rich handlers from discovery."""
    handlers = discover_block_handlers()
    return UserIO(block_handlers=handlers)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestTasksMarkup:

    def test_checked(self):
        assert '\u2705' in _tasks_markup('- [x] done')

    def test_in_progress(self):
        assert '\u23f3' in _tasks_markup('- [~] working')

    def test_unchecked(self):
        assert '\u25fb' in _tasks_markup('- [ ] todo')

    def test_no_marker(self):
        assert _tasks_markup('- plain') == '- plain'


class TestParseLang:

    def test_lang_equals(self):
        assert _parse_lang('lang=python') == 'python'

    def test_bare_word(self):
        assert _parse_lang('python') == 'python'

    def test_lang_with_extra(self):
        assert _parse_lang('lang=python file=test.py') == 'python'

    def test_empty(self):
        assert _parse_lang('') == ''


# ---------------------------------------------------------------------------
# RichTasksHandler tests
# ---------------------------------------------------------------------------

class TestRichTasksHandler:

    def test_on_start_saves_console(self):
        console, _ = _make_console()
        handler = RichTasksHandler(block_type="tasks")
        handler.on_start({'console': console})
        assert handler._console is console

    def test_on_line_checked(self):
        console, buf = _make_console()
        handler = RichTasksHandler(block_type="tasks")
        handler.on_start({'console': console})
        handler.on_line('- [x] done')
        output = buf.getvalue()
        assert '\u2705' in output
        assert 'done' in output

    def test_on_line_pending(self):
        console, buf = _make_console()
        handler = RichTasksHandler(block_type="tasks")
        handler.on_start({'console': console})
        handler.on_line('- [ ] todo')
        output = buf.getvalue()
        assert '\u25fb' in output
        assert 'todo' in output

    def test_on_line_in_progress(self):
        console, buf = _make_console()
        handler = RichTasksHandler(block_type="tasks")
        handler.on_start({'console': console})
        handler.on_line('- [~] working')
        output = buf.getvalue()
        assert '\u23f3' in output
        assert 'working' in output

    def test_render(self):
        console, buf = _make_console()
        handler = RichTasksHandler(block_type="tasks")
        content = Content(
            type="tasks",
            body="- [x] done\n- [ ] todo",
            metadata={'console': console},
        )
        handler.render(content)
        output = buf.getvalue()
        assert '\u2705' in output
        assert '\u25fb' in output

    def test_on_end_noop(self):
        handler = RichTasksHandler(block_type="tasks")
        handler.on_end()  # should not raise


# ---------------------------------------------------------------------------
# RichStatusHandler tests
# ---------------------------------------------------------------------------

class TestRichStatusHandler:

    def test_buffers_during_streaming(self):
        console, buf = _make_console()
        handler = RichStatusHandler(block_type="status")
        handler.on_start({'console': console})
        handler.on_line('Line 1')
        handler.on_line('Line 2')
        # Nothing written during streaming
        assert buf.getvalue() == ''

    def test_on_end_renders_panel(self):
        console, buf = _make_console()
        handler = RichStatusHandler(block_type="status")
        handler.on_start({'console': console})
        handler.on_line('Running')
        handler.on_end()
        output = buf.getvalue()
        assert 'Running' in output
        assert 'Status' in output  # Panel title

    def test_render(self):
        console, buf = _make_console()
        handler = RichStatusHandler(block_type="status")
        content = Content(
            type="status",
            body="All systems go",
            metadata={'console': console},
        )
        handler.render(content)
        output = buf.getvalue()
        assert 'All systems go' in output
        assert 'Status' in output


# ---------------------------------------------------------------------------
# RichCodeHandler tests
# ---------------------------------------------------------------------------

class TestRichCodeHandler:

    def test_buffers_code(self):
        console, buf = _make_console()
        handler = RichCodeHandler(block_type="code")
        handler.on_start({'console': console, 'raw': 'python'})
        handler.on_line('x = 1')
        handler.on_line('print(x)')
        # Buffered, not yet rendered
        assert buf.getvalue() == ''

    def test_on_end_renders_syntax(self):
        console, buf = _make_console()
        handler = RichCodeHandler(block_type="code")
        handler.on_start({'console': console, 'raw': 'python'})
        handler.on_line('x = 1')
        handler.on_end()
        output = buf.getvalue()
        assert 'x' in output
        assert '1' in output

    def test_render(self):
        console, buf = _make_console()
        handler = RichCodeHandler(block_type="code")
        content = Content(
            type="code",
            body="def hello():\n    pass",
            metadata={'lang': 'python', 'console': console},
        )
        handler.render(content)
        output = buf.getvalue()
        assert 'hello' in output

    def test_lang_from_raw_metadata(self):
        console, _ = _make_console()
        handler = RichCodeHandler(block_type="code")
        handler.on_start({'console': console, 'raw': 'lang=javascript'})
        assert handler._lang == 'javascript'

    def test_lang_bare_word(self):
        console, _ = _make_console()
        handler = RichCodeHandler(block_type="code")
        handler.on_start({'console': console, 'raw': 'rust'})
        assert handler._lang == 'rust'


# ---------------------------------------------------------------------------
# RichThinkingHandler tests
# ---------------------------------------------------------------------------

class TestRichThinkingHandler:

    def test_on_line_dim_italic(self):
        console, buf = _make_console()
        handler = RichThinkingHandler(block_type="thinking")
        handler.on_start({'console': console})
        handler.on_line('Considering options...')
        output = buf.getvalue()
        assert 'Considering options...' in output

    def test_render(self):
        console, buf = _make_console()
        handler = RichThinkingHandler(block_type="thinking")
        content = Content(
            type="thinking",
            body="Step 1\nStep 2",
            metadata={'console': console},
        )
        handler.render(content)
        output = buf.getvalue()
        assert 'Step 1' in output
        assert 'Step 2' in output

    def test_on_end_noop(self):
        handler = RichThinkingHandler(block_type="thinking")
        handler.on_end()  # should not raise


# ---------------------------------------------------------------------------
# Console fallback tests
# ---------------------------------------------------------------------------

class TestConsoleFallback:

    def test_handler_without_injected_console(self):
        """Handlers should work even without Console injection (fallback)."""
        handler = RichTasksHandler(block_type="tasks")
        # No on_start called -> no _console set
        # on_line should create fallback Console and not crash
        handler.on_line('- [x] test')

    def test_render_without_metadata_console(self):
        handler = RichTasksHandler(block_type="tasks")
        content = Content(type="tasks", body="- [x] test")
        handler.render(content)  # should not crash


# ---------------------------------------------------------------------------
# render_event override tests
# ---------------------------------------------------------------------------

class TestRichRenderEvent:

    @pytest.fixture
    def userio(self):
        """Create a UserIO with rich handlers and a capturable Console."""
        handlers = discover_block_handlers()
        io = UserIO(block_handlers=handlers)
        console, buf = _make_console()
        object.__setattr__(io, '_console', console)
        object.__setattr__(io, '_capture_buf', buf)
        return io

    def _output(self, userio):
        return userio._capture_buf.getvalue()

    def test_text_delta_markdown(self, userio):
        _send_text(userio, "Hello **world**\n")
        _send_turn_done(userio)
        output = self._output(userio)
        assert 'world' in output

    def test_tool_exec_start(self, userio):
        tc = ToolCall(id="tc_1", name="Module-inspect", arguments={"module_path": "mutagent"})
        event = StreamEvent(type="tool_exec_start", tool_call=tc)
        userio.render_event(event)
        output = self._output(userio)
        assert 'Module-inspect' in output
        assert 'module_path=mutagent' in output

    def test_tool_exec_start_no_args(self, userio):
        tc = ToolCall(id="tc_1", name="Module-inspect", arguments={})
        event = StreamEvent(type="tool_exec_start", tool_call=tc)
        userio.render_event(event)
        output = self._output(userio)
        assert 'Module-inspect' in output

    def test_tool_exec_end_ok(self, userio):
        tr = ToolResult(tool_call_id="tc_1", content="Success result")
        event = StreamEvent(type="tool_exec_end", tool_result=tr)
        userio.render_event(event)
        output = self._output(userio)
        assert 'done' in output
        assert 'Success result' in output

    def test_tool_exec_end_error(self, userio):
        tr = ToolResult(tool_call_id="tc_1", content="Failed", is_error=True)
        event = StreamEvent(type="tool_exec_end", tool_result=tr)
        userio.render_event(event)
        output = self._output(userio)
        assert 'error' in output

    def test_error_event(self, userio):
        err_console, err_buf = _make_console()
        object.__setattr__(userio, '_err_console', err_console)
        event = StreamEvent(type="error", error="API failed")
        userio.render_event(event)
        output = err_buf.getvalue()
        assert 'API failed' in output

    def test_turn_done_flushes_text_buf(self, userio):
        _send_text(userio, "Some text")
        _send_turn_done(userio)
        output = self._output(userio)
        assert 'Some text' in output

    def test_text_flushed_before_tool_exec_start(self, userio):
        """text_buf must be flushed before tool info to maintain display order."""
        _send_text(userio, "Before tool\n")
        tc = ToolCall(id="tc_1", name="test_tool", arguments={})
        userio.render_event(StreamEvent(type="tool_exec_start", tool_call=tc))
        output = self._output(userio)
        # Text must appear before tool info
        text_pos = output.index('Before tool')
        tool_pos = output.index('test_tool')
        assert text_pos < tool_pos

    def test_text_flushed_before_tool_exec_end(self, userio):
        """text_buf must be flushed before tool result."""
        _send_text(userio, "Before result\n")
        tr = ToolResult(tool_call_id="tc_1", content="Result")
        userio.render_event(StreamEvent(type="tool_exec_end", tool_result=tr))
        output = self._output(userio)
        text_pos = output.index('Before result')
        result_pos = output.index('Result')
        assert text_pos < result_pos

    def test_text_flushed_before_error(self, userio):
        """text_buf must be flushed before error output."""
        _send_text(userio, "Before error\n")
        # Error goes to stderr console, but text flush goes to main console
        userio.render_event(StreamEvent(type="error", error="Oops"))
        output = self._output(userio)
        assert 'Before error' in output

    def test_unknown_event_noop(self, userio):
        event = StreamEvent(type="response_done")
        userio.render_event(event)
        output = self._output(userio)
        assert output == ''


# ---------------------------------------------------------------------------
# text_buf paragraph flush tests
# ---------------------------------------------------------------------------

class TestTextBufParagraphFlush:

    @pytest.fixture
    def userio(self):
        handlers = discover_block_handlers()
        io = UserIO(block_handlers=handlers)
        console, buf = _make_console()
        object.__setattr__(io, '_console', console)
        object.__setattr__(io, '_capture_buf', buf)
        return io

    def _output(self, userio):
        return userio._capture_buf.getvalue()

    def test_paragraph_boundary_flushes(self, userio):
        _send_text(userio, "First paragraph.\n\nSecond paragraph.\n")
        output = self._output(userio)
        assert 'First paragraph.' in output

    def test_no_flush_without_double_newline(self, userio):
        _send_text(userio, "Line one\nLine two\n")
        output = self._output(userio)
        # Should not flush yet (no paragraph boundary)
        assert output == ''

    def test_flush_before_block(self, userio):
        _send_text(userio, "Prefix text\n```mutagent:tasks\n- [x] done\n```\n")
        output = self._output(userio)
        # Prefix text should be rendered before the block
        assert 'Prefix text' in output
        assert '\u2705' in output  # Rich tasks handler active


# ---------------------------------------------------------------------------
# present override tests
# ---------------------------------------------------------------------------

class TestRichPresent:

    @pytest.fixture
    def userio(self):
        handlers = discover_block_handlers()
        io = UserIO(block_handlers=handlers)
        console, buf = _make_console()
        object.__setattr__(io, '_console', console)
        object.__setattr__(io, '_capture_buf', buf)
        return io

    def _output(self, userio):
        return userio._capture_buf.getvalue()

    def test_present_with_handler(self, userio):
        content = Content(type="tasks", body="- [x] done\n- [ ] todo")
        userio.present(content)
        output = self._output(userio)
        assert '\u2705' in output
        assert '\u25fb' in output

    def test_present_fallback(self, userio):
        content = Content(type="unknown", body="some text")
        userio.present(content)
        output = self._output(userio)
        assert 'some text' in output

    def test_present_with_source_prefix(self, userio):
        content = Content(type="unknown", body="message", source="agent-main")
        userio.present(content)
        output = self._output(userio)
        assert 'agent-main' in output
        assert 'message' in output

    def test_present_empty_body(self, userio):
        content = Content(type="unknown", body="")
        userio.present(content)
        output = self._output(userio)
        assert output == ''

    def test_present_injects_console_to_handler(self, userio):
        content = Content(type="status", body="All good")
        userio.present(content)
        # Console was injected into metadata
        assert 'console' in content.metadata
        output = self._output(userio)
        assert 'All good' in output


# ---------------------------------------------------------------------------
# Block detection integration tests (with rich handlers)
# ---------------------------------------------------------------------------

class TestRichBlockDetection:

    @pytest.fixture
    def userio(self):
        handlers = discover_block_handlers()
        io = UserIO(block_handlers=handlers)
        console, buf = _make_console()
        object.__setattr__(io, '_console', console)
        object.__setattr__(io, '_capture_buf', buf)
        return io

    def _output(self, userio):
        return userio._capture_buf.getvalue()

    def test_tasks_block_streaming(self, userio):
        _send_text(userio, "```mutagent:tasks\n- [x] a\n- [ ] b\n```\n")
        output = self._output(userio)
        assert '\u2705' in output
        assert '\u25fb' in output

    def test_code_block_streaming(self, userio):
        _send_text(userio, "```mutagent:code python\nx = 42\n```\n")
        output = self._output(userio)
        assert '42' in output

    def test_status_block_streaming(self, userio):
        _send_text(userio, "```mutagent:status\nAll good\n```\n")
        output = self._output(userio)
        assert 'All good' in output
        assert 'Status' in output

    def test_thinking_block_streaming(self, userio):
        _send_text(userio, "```mutagent:thinking\nHmm...\n```\n")
        output = self._output(userio)
        assert 'Hmm...' in output

    def test_multiple_blocks(self, userio):
        _send_text(userio, "```mutagent:tasks\n- [x] a\n```\n")
        _send_text(userio, "```mutagent:status\nDone\n```\n")
        output = self._output(userio)
        assert '\u2705' in output
        assert 'Done' in output

    def test_text_before_and_after_block(self, userio):
        _send_text(userio, "Before\n```mutagent:tasks\n- [x] a\n```\nAfter\n")
        _send_turn_done(userio)
        output = self._output(userio)
        assert 'Before' in output
        assert '\u2705' in output
        assert 'After' in output


# ---------------------------------------------------------------------------
# Module loading and handler discovery tests
# ---------------------------------------------------------------------------

class TestModuleLoadingOverride:

    def test_impl_overrides_basic(self):
        """After loading extras.rich, @impl points to rich implementations."""
        # The render_event and present should be the rich versions
        io = UserIO(block_handlers={})
        console, buf = _make_console()
        object.__setattr__(io, '_console', console)
        # Send a tool event -- rich version uses console.print with styles
        tc = ToolCall(id="tc_1", name="test_tool", arguments={})
        io.render_event(StreamEvent(type="tool_exec_start", tool_call=tc))
        output = buf.getvalue()
        # Rich version wraps in dim style; basic version uses print()
        assert 'test_tool' in output

    def test_discover_returns_rich_handlers(self):
        """discover_block_handlers returns Rich versions after extras loaded."""
        handlers = discover_block_handlers()
        assert isinstance(handlers['tasks'], RichTasksHandler)
        assert isinstance(handlers['status'], RichStatusHandler)
        assert isinstance(handlers['code'], RichCodeHandler)
        assert isinstance(handlers['thinking'], RichThinkingHandler)

    def test_builtin_default_handler_still_available(self):
        """DefaultHandler from builtins should still be discoverable."""
        handlers = discover_block_handlers()
        assert 'default' in handlers
        assert isinstance(handlers['default'], builtin_bh.DefaultHandler)


# ---------------------------------------------------------------------------
# State isolation test
# ---------------------------------------------------------------------------

class TestStateIsolation:

    def test_separate_userio_instances_independent(self):
        """Multiple UserIO instances should have independent parse state."""
        handlers = discover_block_handlers()
        io1 = UserIO(block_handlers=handlers)
        io2 = UserIO(block_handlers=handlers)
        c1, b1 = _make_console()
        c2, b2 = _make_console()
        object.__setattr__(io1, '_console', c1)
        object.__setattr__(io2, '_console', c2)

        _send_text(io1, "```mutagent:tasks\n- [x] a\n```\n")
        _send_text(io2, "Hello world\n")
        _send_turn_done(io2)

        out1 = b1.getvalue()
        out2 = b2.getvalue()
        assert '\u2705' in out1
        assert 'Hello world' in out2
        assert '\u2705' not in out2
