"""mutagent.builtins.block_handlers -- Built-in BlockHandler implementations.

Each handler subclass sets ``_BLOCK_TYPE`` as a class constant identifying
the block type it handles. BlockHandler subclasses are automatically
discovered by UserIO via ``discover_block_handlers()``.
"""

from __future__ import annotations

import mutagent
from mutagent.userio import BlockHandler


class DefaultHandler(BlockHandler):
    """Fallback handler for unknown block types.

    Outputs block content as plain text (code block style).
    """
    _BLOCK_TYPE = "default"

    def on_start(self, metadata):
        block_type = metadata.get('type', 'unknown')
        print(f"```{block_type}", flush=True)

    def on_line(self, text):
        print(text, flush=True)

    def on_end(self):
        print("```", flush=True)

    def render(self, content):
        if content.body:
            print(f"```{content.type}", flush=True)
            print(content.body, flush=True)
            print("```", flush=True)


class TasksHandler(BlockHandler):
    """Handler for mutagent:tasks blocks.

    Basic terminal: renders each task line immediately as it arrives.
    """
    _BLOCK_TYPE = "tasks"

    def on_line(self, text):
        print(text, flush=True)

    def render(self, content):
        if content.body:
            for line in content.body.split('\n'):
                print(line, flush=True)


class StatusHandler(BlockHandler):
    """Handler for mutagent:status blocks.

    Basic terminal: buffers content, renders all at once on block end.
    """
    _BLOCK_TYPE = "status"

    def on_start(self, metadata):
        object.__setattr__(self, '_buffer', [])

    def on_line(self, text):
        buf = getattr(self, '_buffer', None)
        if buf is not None:
            buf.append(text)
        else:
            print(text, flush=True)

    def on_end(self):
        buf = getattr(self, '_buffer', None)
        if buf:
            print('\n'.join(buf), flush=True)
        object.__setattr__(self, '_buffer', None)

    def render(self, content):
        if content.body:
            print(content.body, flush=True)


class CodeHandler(BlockHandler):
    """Handler for mutagent:code blocks.

    Basic terminal: outputs as a standard code block.
    """
    _BLOCK_TYPE = "code"

    def on_start(self, metadata):
        lang = metadata.get('raw', '').split()[0] if metadata.get('raw') else ''
        if lang:
            print(f"```{lang}", flush=True)
        else:
            print("```", flush=True)

    def on_line(self, text):
        print(text, flush=True)

    def on_end(self):
        print("```", flush=True)

    def render(self, content):
        lang = content.metadata.get('lang', '')
        if lang:
            print(f"```{lang}", flush=True)
        else:
            print("```", flush=True)
        if content.body:
            print(content.body, flush=True)
        print("```", flush=True)


class ThinkingHandler(BlockHandler):
    """Handler for mutagent:thinking blocks.

    Basic terminal: streams content as-is (real-time display).
    """
    _BLOCK_TYPE = "thinking"

    def on_line(self, text):
        print(text, flush=True)

    def render(self, content):
        if content.body:
            print(content.body, flush=True)


# ---------------------------------------------------------------------------
# Interactive block helpers
# ---------------------------------------------------------------------------

def _parse_ask_block(lines):
    """Parse ask block lines into question and options.

    Lines starting with ``- `` are treated as option lines. All non-empty
    lines before the first option form the question text.

    Args:
        lines: List of block content lines.

    Returns:
        Tuple of (question_str, options_list).
    """
    question_parts = []
    options = []
    in_options = False
    for line in lines:
        if line.startswith('- '):
            in_options = True
            options.append(line[2:])
        elif not in_options and line.strip():
            question_parts.append(line)
    return '\n'.join(question_parts), options


# ---------------------------------------------------------------------------
# Interactive block handlers
# ---------------------------------------------------------------------------

class AskHandler(BlockHandler):
    """Handler for mutagent:ask blocks.

    Basic terminal: streams lines as plain text, registers a pending
    interaction on block end with parsed question and options.
    """
    _BLOCK_TYPE = "ask"

    def on_start(self, metadata):
        object.__setattr__(self, '_buffer', [])

    def on_line(self, text):
        print(text, flush=True)
        buf = getattr(self, '_buffer', None)
        if buf is not None:
            buf.append(text)

    def on_end(self):
        buf = getattr(self, '_buffer', [])
        question, options = _parse_ask_block(buf)
        object.__setattr__(self, '_pending_interaction', {
            'type': 'ask',
            'question': question,
            'options': options,
            'result': None,
        })
        object.__setattr__(self, '_buffer', None)

    def render(self, content):
        if content.body:
            print(content.body, flush=True)
        lines = content.body.split('\n') if content.body else []
        question, options = _parse_ask_block(lines)
        object.__setattr__(self, '_pending_interaction', {
            'type': 'ask',
            'question': question,
            'options': options,
            'result': None,
        })


class ConfirmHandler(BlockHandler):
    """Handler for mutagent:confirm blocks.

    Basic terminal: streams lines as plain text, registers a pending
    interaction on block end with the full message as question.
    """
    _BLOCK_TYPE = "confirm"

    def on_start(self, metadata):
        object.__setattr__(self, '_buffer', [])

    def on_line(self, text):
        print(text, flush=True)
        buf = getattr(self, '_buffer', None)
        if buf is not None:
            buf.append(text)

    def on_end(self):
        buf = getattr(self, '_buffer', [])
        question = '\n'.join(line for line in buf if line.strip())
        object.__setattr__(self, '_pending_interaction', {
            'type': 'confirm',
            'question': question,
            'options': [],
            'result': None,
        })
        object.__setattr__(self, '_buffer', None)

    def render(self, content):
        if content.body:
            print(content.body, flush=True)
        lines = content.body.split('\n') if content.body else []
        question = '\n'.join(line for line in lines if line.strip())
        object.__setattr__(self, '_pending_interaction', {
            'type': 'confirm',
            'question': question,
            'options': [],
            'result': None,
        })
