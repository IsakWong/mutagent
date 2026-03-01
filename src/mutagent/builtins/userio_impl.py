"""Default implementation for mutagent.userio -- basic terminal UserIO."""

from __future__ import annotations

import re
import sys

import mutagent
from mutagent.messages import Content, InputEvent
from mutagent.runtime.ansi import (
    bold_cyan, bold_red, dim, green, highlight_markdown_line,
)
from mutagent.userio import BlockHandler, UserIO


# Block opening pattern: ```mutagent:type (optional trailing text ignored)
_BLOCK_OPEN_RE = re.compile(r'^```mutagent:(\w+)(.*)$')
# Block closing pattern: ``` (possibly with trailing whitespace)
_BLOCK_CLOSE_RE = re.compile(r'^```\s*$')


# ---------------------------------------------------------------------------
# BlockHandler default implementations
# ---------------------------------------------------------------------------

@mutagent.impl(BlockHandler.on_start)
def block_handler_on_start(self, metadata: dict) -> None:
    """Default: no-op on block start."""
    pass


@mutagent.impl(BlockHandler.on_line)
def block_handler_on_line(self, text: str) -> None:
    """Default: print each line."""
    print(text, flush=True)


@mutagent.impl(BlockHandler.on_end)
def block_handler_on_end(self) -> None:
    """Default: no-op on block end."""
    pass


@mutagent.impl(BlockHandler.render)
def block_handler_render(self, content) -> None:
    """Default: print content body."""
    if content.body:
        print(content.body, flush=True)


# ---------------------------------------------------------------------------
# Pending interaction transfer
# ---------------------------------------------------------------------------

def _transfer_pending_interaction(userio, handler):
    """Transfer a pending interaction from handler to UserIO's list.

    Checks handler for a ``_pending_interaction`` attribute. If present,
    appends it to ``userio._pending_interactions`` and clears it from handler.
    """
    pending = getattr(handler, '_pending_interaction', None)
    if pending is not None:
        interactions = getattr(userio, '_pending_interactions', None)
        if interactions is None:
            interactions = []
            object.__setattr__(userio, '_pending_interactions', interactions)
        interactions.append(pending)
        object.__setattr__(handler, '_pending_interaction', None)


# ---------------------------------------------------------------------------
# Streaming block detection state machine
# ---------------------------------------------------------------------------

def _get_parse_state(userio):
    """Get or lazily initialize the streaming parse state."""
    ps = getattr(userio, '_parse_state', None)
    if ps is None:
        ps = {
            'state': 'NORMAL',    # NORMAL or IN_BLOCK
            'line_buf': '',       # incomplete line buffer
            'handler': None,      # current BlockHandler instance
            'block_type': '',     # current block type string
        }
        object.__setattr__(userio, '_parse_state', ps)
    return ps


def _reset_parse_state(userio):
    """Reset parse state (e.g. at turn boundaries)."""
    ps = getattr(userio, '_parse_state', None)
    if ps is not None:
        # Flush any buffered text
        if ps['state'] == 'IN_BLOCK' and ps['handler'] is not None:
            ps['handler'].on_end()
            _transfer_pending_interaction(userio, ps['handler'])
        if ps['line_buf']:
            print(highlight_markdown_line(ps['line_buf']), end="", flush=True)
        ps['state'] = 'NORMAL'
        ps['line_buf'] = ''
        ps['handler'] = None
        ps['block_type'] = ''


def _could_be_block_start(text):
    """Check if partial text could still become a ```mutagent: opening line."""
    prefix = "```mutagent:"
    # text is a prefix of the pattern, so it could still match
    return prefix.startswith(text)


def _process_text(userio, text):
    """Process a text fragment through the block detection state machine."""
    ps = _get_parse_state(userio)
    ps['line_buf'] += text

    # Process all complete lines (separated by newline)
    while '\n' in ps['line_buf']:
        line, ps['line_buf'] = ps['line_buf'].split('\n', 1)
        _process_complete_line(userio, ps, line)

    # Partial line remains in line_buf until the next \n arrives.
    # This ensures line-start Markdown patterns (headings, lists, etc.)
    # are detected correctly even when tokens arrive in small fragments.


def _process_complete_line(userio, ps, line):
    """Process one complete line through the state machine."""
    if ps['state'] == 'NORMAL':
        m = _BLOCK_OPEN_RE.match(line) if userio.block_handlers else None
        if m:
            block_type = m.group(1)
            handler = userio.block_handlers.get(block_type)
            if handler is not None:
                # Transition: NORMAL → IN_BLOCK
                ps['state'] = 'IN_BLOCK'
                ps['handler'] = handler
                ps['block_type'] = block_type
                metadata = {'type': block_type}
                # Parse any key=value pairs from the rest of the opening line
                rest = m.group(2).strip()
                if rest:
                    metadata['raw'] = rest
                handler.on_start(metadata)
                return
        # Not a block start — output as normal text with newline
        print(highlight_markdown_line(line), flush=True)
    elif ps['state'] == 'IN_BLOCK':
        if _BLOCK_CLOSE_RE.match(line):
            # Transition: IN_BLOCK → NORMAL (FLUSH)
            ps['handler'].on_end()
            _transfer_pending_interaction(userio, ps['handler'])
            ps['state'] = 'NORMAL'
            ps['handler'] = None
            ps['block_type'] = ''
        else:
            ps['handler'].on_line(line)


# ---------------------------------------------------------------------------
# UserIO basic terminal implementation
# ---------------------------------------------------------------------------

@mutagent.impl(UserIO.render_event)
def render_event(self, event) -> None:
    """Basic terminal: render events with streaming block detection."""
    if event.type == "text_delta":
        if not self.block_handlers:
            # Fast path: no handlers registered, skip block detection
            print(event.text, end="", flush=True)
        else:
            _process_text(self, event.text)
    elif event.type == "tool_exec_start":
        name = event.tool_call.name if event.tool_call else "?"
        args = event.tool_call.input if event.tool_call else {}
        call_str = _format_tool_call(name, args)
        print(f"\n{dim(call_str)}", flush=True)
    elif event.type == "tool_exec_end":
        if event.tool_call:
            is_error = event.tool_call.is_error
            result_str = _format_tool_result(
                event.tool_call.result, is_error,
            )
            print(result_str, flush=True)
    elif event.type == "error":
        print(f"\n{bold_red('[Error: ' + event.error + ']')}",
              file=sys.stderr, flush=True)
    elif event.type == "turn_done":
        _reset_parse_state(self)
        print()


@mutagent.impl(UserIO.present)
def present(self, content) -> None:
    """Basic terminal: render Content by delegating to BlockHandler."""
    handler = self.block_handlers.get(content.type) if self.block_handlers else None
    if handler is None:
        # Fallback: print body with source prefix
        prefix = f"[{content.source}] " if content.source else ""
        if content.body:
            print(f"{prefix}{content.body}", flush=True)
    else:
        handler.render(content)
        _transfer_pending_interaction(self, handler)


@mutagent.impl(UserIO.read_input)
def read_input(self) -> str:
    """Basic terminal: read a line from stdin."""
    return input(bold_cyan("> ")).strip()


@mutagent.impl(UserIO.confirm_exit)
def confirm_exit(self) -> bool:
    """Basic terminal: ask y/n to confirm exit."""
    for _ in range(3):
        try:
            choice = input("\nDo you want to exit? (Y/n) ").strip().lower()
        except KeyboardInterrupt:
            continue
        if choice in ("y", "yes", ""):
            return True
        elif choice in ("n", "no"):
            return False
    print("")
    return True


@mutagent.impl(UserIO.input_stream)
def input_stream(self):
    """Basic terminal: generator yielding InputEvents from stdin."""
    while True:
        try:
            while True:
                user_input = self.read_input()
                if user_input:
                    break
            # Collect pending interactions
            data = {}
            pending = getattr(self, '_pending_interactions', None)
            if pending:
                for i, interaction in enumerate(pending):
                    interaction['id'] = i
                data['interactions'] = list(pending)
                pending.clear()
            yield InputEvent(type="user_message", text=user_input, data=data)
        except KeyboardInterrupt:
            if self.confirm_exit():
                print("Bye.")
                return
        except EOFError:
            return


# ---------------------------------------------------------------------------
# Tool call / result formatting
# ---------------------------------------------------------------------------

_MAX_VALUE_LEN = 60       # max display length for a single parameter value
_MAX_SINGLE_LINE = 80     # max total length before switching to multi-line
_INDENT = "  "            # base indentation
_PARAM_INDENT = "      "  # parameter indentation in multi-line mode (6 spaces)
_PREVIEW_LINES = 4        # default number of result preview lines
_RESULT_INDENT = "    "   # result continuation indent (4 spaces)


def _format_value(value) -> str:
    """Format a single argument value in Python style."""
    if isinstance(value, str):
        display = value
        if len(display) > _MAX_VALUE_LEN:
            display = display[:_MAX_VALUE_LEN - 3] + "..."
        return f'"{display}"'
    r = repr(value)
    if len(r) > _MAX_VALUE_LEN:
        r = r[:_MAX_VALUE_LEN - 3] + "..."
    return r


def _format_tool_call(name: str, args: dict) -> str:
    """Format a tool call as a Python-style function call string."""
    if not args:
        return f"{_INDENT}{name}()"

    # Build parameter strings
    params = [f"{k}={_format_value(v)}" for k, v in args.items()]

    # Try single-line first
    single = f"{_INDENT}{name}({', '.join(params)})"
    if len(single) <= _MAX_SINGLE_LINE:
        return single

    # Multi-line form
    lines = [f"{_INDENT}{name}("]
    for p in params:
        lines.append(f"{_PARAM_INDENT}{p},")
    lines.append(f"{_INDENT})")
    return "\n".join(lines)


def _format_tool_result(content: str, is_error: bool) -> str:
    """Format a tool result with preview and line count."""
    color = bold_red if is_error else green
    lines = content.split("\n") if content else [""]

    if len(lines) <= _PREVIEW_LINES:
        # Short result: show everything
        first = f"{_INDENT}\u2192 {lines[0]}"
        result_lines = [color(first)]
        for extra in lines[1:]:
            result_lines.append(color(f"{_RESULT_INDENT}{extra}"))
        return "\n".join(result_lines)

    # Long result: preview + overflow indicator
    first = f"{_INDENT}\u2192 {lines[0]}"
    result_lines = [color(first)]
    for extra in lines[1:_PREVIEW_LINES]:
        result_lines.append(color(f"{_RESULT_INDENT}{extra}"))
    remaining = len(lines) - _PREVIEW_LINES
    result_lines.append(dim(f"{_RESULT_INDENT}... +{remaining} lines"))
    return "\n".join(result_lines)


def discover_block_handlers() -> dict[str, BlockHandler]:
    """Discover all BlockHandler subclasses and create instances.

    Scans mutobj's class registry for BlockHandler subclasses that have
    a ``_BLOCK_TYPE`` class constant, instantiates them, and returns a
    mapping of block_type -> handler instance.

    Returns:
        Dict mapping block type strings to BlockHandler instances.
    """
    import mutobj

    handlers = {}
    for cls in mutobj.discover_subclasses(BlockHandler):
        # Get the block type from the _BLOCK_TYPE class constant
        block_type = getattr(cls, '_BLOCK_TYPE', None)
        if block_type and isinstance(block_type, str):
            try:
                instance = cls(block_type=block_type)
                handlers[block_type] = instance
            except Exception:
                pass
    return handlers
