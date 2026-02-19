"""Default implementation for mutagent.userio -- basic terminal UserIO."""

from __future__ import annotations

import re
import sys

import mutagent
from mutagent.messages import Content, InputEvent
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
        if ps['line_buf']:
            print(ps['line_buf'], end="", flush=True)
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

    # In NORMAL state, flush partial line buffer if it can't be a block start
    if ps['state'] == 'NORMAL' and ps['line_buf']:
        if not userio.block_handlers or not _could_be_block_start(ps['line_buf']):
            print(ps['line_buf'], end="", flush=True)
            ps['line_buf'] = ''


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
        print(line, flush=True)
    elif ps['state'] == 'IN_BLOCK':
        if _BLOCK_CLOSE_RE.match(line):
            # Transition: IN_BLOCK → NORMAL (FLUSH)
            ps['handler'].on_end()
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
        args_summary = _summarize_args(
            event.tool_call.arguments if event.tool_call else {}
        )
        if args_summary:
            print(f"\n  [{name}({args_summary})]", flush=True)
        else:
            print(f"\n  [{name}]", flush=True)
    elif event.type == "tool_exec_end":
        if event.tool_result:
            status = "error" if event.tool_result.is_error else "done"
            summary = event.tool_result.content[:100]
            if len(event.tool_result.content) > 100:
                summary += "..."
            print(f"  -> [{status}] {summary}", flush=True)
    elif event.type == "error":
        print(f"\n[Error: {event.error}]", file=sys.stderr, flush=True)
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


@mutagent.impl(UserIO.read_input)
def read_input(self) -> str:
    """Basic terminal: read a line from stdin."""
    return input("> ").strip()


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
            yield InputEvent(type="user_message", text=user_input)
        except KeyboardInterrupt:
            if self.confirm_exit():
                print("Bye.")
                return
        except EOFError:
            return


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _summarize_args(args: dict) -> str:
    """Create a short summary of tool call arguments."""
    if not args:
        return ""
    parts = []
    for key, value in args.items():
        s = str(value)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{key}={s}")
    return ", ".join(parts)


def discover_block_handlers() -> dict[str, BlockHandler]:
    """Discover all BlockHandler subclasses and create instances.

    Scans mutobj's class registry for BlockHandler subclasses that have
    a ``_BLOCK_TYPE`` class constant, instantiates them, and returns a
    mapping of block_type -> handler instance.

    Returns:
        Dict mapping block type strings to BlockHandler instances.
    """
    from mutobj.core import _class_registry

    handlers = {}
    for cls in _class_registry.values():
        if (cls is BlockHandler
                or not isinstance(cls, type)
                or not issubclass(cls, BlockHandler)):
            continue
        # Get the block type from the _BLOCK_TYPE class constant
        block_type = getattr(cls, '_BLOCK_TYPE', None)
        if block_type and isinstance(block_type, str):
            try:
                instance = cls(block_type=block_type)
                handlers[block_type] = instance
            except Exception:
                pass
    return handlers
