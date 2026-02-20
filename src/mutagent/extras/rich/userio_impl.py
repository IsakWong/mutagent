"""mutagent.extras.rich.userio_impl -- Rich terminal @impl overrides.

Frontend binding layer: overrides UserIO.render_event and UserIO.present
with rich-enhanced output. Creates a stdout Console and injects it into
BlockHandlers. This module is only used in standalone rich terminal mode;
TUI provides its own @impl that injects a widget-targeted Console.
"""

from __future__ import annotations

import re
import sys

import mutagent
from mutagent.messages import Content
from mutagent.userio import UserIO


from mutagent.builtins.userio_impl import _transfer_pending_interaction


# Block patterns (same as base terminal)
_BLOCK_OPEN_RE = re.compile(r'^```mutagent:(\w+)(.*)$')
_BLOCK_CLOSE_RE = re.compile(r'^```\s*$')


# ---------------------------------------------------------------------------
# Console management
# ---------------------------------------------------------------------------

def _get_console(userio):
    """Get or lazily create the shared rich Console on a UserIO instance."""
    console = getattr(userio, '_console', None)
    if console is None:
        from rich.console import Console
        console = Console(highlight=False)
        object.__setattr__(userio, '_console', console)
    return console


def _get_err_console(userio):
    """Get or lazily create a stderr Console for error output."""
    console = getattr(userio, '_err_console', None)
    if console is None:
        from rich.console import Console
        console = Console(stderr=True, highlight=False)
        object.__setattr__(userio, '_err_console', console)
    return console


# ---------------------------------------------------------------------------
# State machine (extends base with text_buf for Markdown rendering)
# ---------------------------------------------------------------------------

def _get_parse_state(userio):
    """Get or lazily initialize the rich streaming parse state."""
    ps = getattr(userio, '_parse_state', None)
    if ps is None:
        ps = {
            'state': 'NORMAL',    # NORMAL or IN_BLOCK
            'line_buf': '',       # incomplete line buffer
            'handler': None,      # current BlockHandler instance
            'block_type': '',     # current block type string
            'text_buf': '',       # normal text accumulation for Markdown
        }
        object.__setattr__(userio, '_parse_state', ps)
    return ps


def _flush_text_buf(userio, ps):
    """Flush accumulated text_buf as Markdown."""
    text = ps['text_buf']
    if text:
        from rich.markdown import Markdown
        _get_console(userio).print(Markdown(text))
        ps['text_buf'] = ''


def _reset_parse_state(userio):
    """Reset parse state at turn boundaries."""
    ps = getattr(userio, '_parse_state', None)
    if ps is not None:
        if ps['state'] == 'IN_BLOCK' and ps['handler'] is not None:
            ps['handler'].on_end()
            _transfer_pending_interaction(userio, ps['handler'])
        # Flush any remaining text_buf
        if ps['line_buf']:
            ps['text_buf'] += ps['line_buf']
        _flush_text_buf(userio, ps)
        ps['state'] = 'NORMAL'
        ps['line_buf'] = ''
        ps['handler'] = None
        ps['block_type'] = ''
        ps['text_buf'] = ''


def _could_be_block_start(text):
    """Check if partial text could still become a ```mutagent: opening line."""
    prefix = "```mutagent:"
    return prefix.startswith(text)


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------

def _process_text(userio, text):
    """Process a text fragment through the rich block detection state machine."""
    ps = _get_parse_state(userio)
    ps['line_buf'] += text

    # Process all complete lines
    while '\n' in ps['line_buf']:
        line, ps['line_buf'] = ps['line_buf'].split('\n', 1)
        _process_complete_line(userio, ps, line)

    # In NORMAL state, flush partial line buffer if it can't be a block start
    if ps['state'] == 'NORMAL' and ps['line_buf']:
        if not userio.block_handlers or not _could_be_block_start(ps['line_buf']):
            ps['text_buf'] += ps['line_buf']
            ps['line_buf'] = ''


def _process_complete_line(userio, ps, line):
    """Process one complete line through the rich state machine."""
    if ps['state'] == 'NORMAL':
        m = _BLOCK_OPEN_RE.match(line) if userio.block_handlers else None
        if m:
            block_type = m.group(1)
            handler = userio.block_handlers.get(block_type)
            if handler is not None:
                # Flush text_buf before entering block
                _flush_text_buf(userio, ps)
                # Transition: NORMAL -> IN_BLOCK
                ps['state'] = 'IN_BLOCK'
                ps['handler'] = handler
                ps['block_type'] = block_type
                metadata = {
                    'type': block_type,
                    'console': _get_console(userio),
                }
                rest = m.group(2).strip()
                if rest:
                    metadata['raw'] = rest
                handler.on_start(metadata)
                return
        # Not a block start -- accumulate into text_buf
        ps['text_buf'] += line + '\n'
        # Check for paragraph boundary (double newline)
        if '\n\n' in ps['text_buf']:
            # Split at last paragraph boundary; flush completed paragraphs
            idx = ps['text_buf'].rfind('\n\n')
            completed = ps['text_buf'][:idx]
            ps['text_buf'] = ps['text_buf'][idx + 2:]
            if completed:
                from rich.markdown import Markdown
                _get_console(userio).print(Markdown(completed))
    elif ps['state'] == 'IN_BLOCK':
        if _BLOCK_CLOSE_RE.match(line):
            # Transition: IN_BLOCK -> NORMAL
            ps['handler'].on_end()
            _transfer_pending_interaction(userio, ps['handler'])
            ps['state'] = 'NORMAL'
            ps['handler'] = None
            ps['block_type'] = ''
        else:
            ps['handler'].on_line(line)


# ---------------------------------------------------------------------------
# Helper
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


# ---------------------------------------------------------------------------
# @impl overrides
# ---------------------------------------------------------------------------

@mutagent.impl(UserIO.render_event)
def render_event(self, event) -> None:
    """Rich terminal: render events with Markdown and rich styling."""
    ps = _get_parse_state(self)

    if event.type == "text_delta":
        if not self.block_handlers:
            # Fast path: no handlers, just accumulate and flush as Markdown
            ps['text_buf'] += event.text
        else:
            _process_text(self, event.text)
    elif event.type == "tool_exec_start":
        # Flush buffered text before tool output to maintain display order
        _flush_text_buf(self, ps)
        console = _get_console(self)
        name = event.tool_call.name if event.tool_call else "?"
        args_summary = _summarize_args(
            event.tool_call.arguments if event.tool_call else {}
        )
        if args_summary:
            console.print(f"\n  [dim]\\[{name}({args_summary})]", highlight=False)
        else:
            console.print(f"\n  [dim]\\[{name}]", highlight=False)
    elif event.type == "tool_exec_end":
        # Flush buffered text before tool result to maintain display order
        _flush_text_buf(self, ps)
        console = _get_console(self)
        if event.tool_result:
            summary = event.tool_result.content[:100]
            if len(event.tool_result.content) > 100:
                summary += "..."
            if event.tool_result.is_error:
                console.print(
                    f"  [red bold]-> \\[error] {summary}[/]", highlight=False,
                )
            else:
                console.print(
                    f"  [green]-> \\[done] {summary}[/]", highlight=False,
                )
    elif event.type == "error":
        # Flush buffered text before error to maintain display order
        _flush_text_buf(self, ps)
        _get_err_console(self).print(
            f"\n[red bold]\\[Error: {event.error}][/]", highlight=False,
        )
    elif event.type == "turn_done":
        _reset_parse_state(self)
        _get_console(self).print()


@mutagent.impl(UserIO.present)
def present(self, content) -> None:
    """Rich terminal: render Content with rich Console."""
    console = _get_console(self)
    handler = self.block_handlers.get(content.type) if self.block_handlers else None
    if handler is None:
        # Fallback: rich console with source prefix
        prefix = f"[dim]\\[{content.source}][/] " if content.source else ""
        if content.body:
            console.print(f"{prefix}{content.body}", highlight=False)
    else:
        # Inject console into content metadata for handler
        content.metadata['console'] = console
        handler.render(content)
        _transfer_pending_interaction(self, handler)
