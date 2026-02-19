"""mutagent.extras.rich.block_handlers -- Rich BlockHandler implementations.

Rendering component layer: defines Rich-enhanced BlockHandler subclasses.
These handlers depend only on the ``rich`` library and an injected Console,
making them reusable by TUI or other rich-based frontends.

Each handler uses the same ``_BLOCK_TYPE`` as the built-in handler it replaces.
Because extras are loaded after builtins, ``discover_block_handlers()`` will
select the rich version (last registered wins).
"""

from __future__ import annotations

from mutagent.userio import BlockHandler


def _handler_console(handler):
    """Get console from saved reference or create fallback.

    Normal path: Console is injected via ``on_start(metadata)`` or
    ``content.metadata`` by the UserIO implementation layer.

    Fallback: create a stdout Console as a safety net for direct usage.
    """
    console = getattr(handler, '_console', None)
    if console is None:
        from rich.console import Console
        console = Console(highlight=False)
    return console


class RichTasksHandler(BlockHandler):
    """Rich handler for mutagent:tasks blocks.

    Replaces task markers with coloured symbols:
    - ``[x]`` -> green checkmark
    - ``[~]`` -> yellow hourglass
    - ``[ ]`` -> dim square
    """
    _BLOCK_TYPE = "tasks"

    def on_start(self, metadata):
        object.__setattr__(self, '_console', metadata.get('console'))

    def on_line(self, text):
        _handler_console(self).print(_tasks_markup(text))

    def on_end(self):
        pass

    def render(self, content):
        console = content.metadata.get('console') or _handler_console(self)
        if content.body:
            for line in content.body.split('\n'):
                console.print(_tasks_markup(line))


class RichStatusHandler(BlockHandler):
    """Rich handler for mutagent:status blocks.

    Buffers lines during streaming, renders as a Panel on block end.
    """
    _BLOCK_TYPE = "status"

    def on_start(self, metadata):
        object.__setattr__(self, '_console', metadata.get('console'))
        object.__setattr__(self, '_buffer', [])

    def on_line(self, text):
        self._buffer.append(text)

    def on_end(self):
        from rich.panel import Panel
        body = '\n'.join(getattr(self, '_buffer', []))
        _handler_console(self).print(Panel(body, title="Status"))
        object.__setattr__(self, '_buffer', [])

    def render(self, content):
        from rich.panel import Panel
        console = content.metadata.get('console') or _handler_console(self)
        if content.body:
            console.print(Panel(content.body, title="Status"))


class RichCodeHandler(BlockHandler):
    """Rich handler for mutagent:code blocks.

    Buffers code lines during streaming, renders with ``rich.syntax.Syntax``
    for language-aware syntax highlighting on block end.
    """
    _BLOCK_TYPE = "code"

    def on_start(self, metadata):
        object.__setattr__(self, '_console', metadata.get('console'))
        # Parse language from raw metadata (e.g. "lang=python" or just "python")
        raw = metadata.get('raw', '')
        lang = _parse_lang(raw)
        object.__setattr__(self, '_lang', lang)
        object.__setattr__(self, '_lines', [])

    def on_line(self, text):
        self._lines.append(text)

    def on_end(self):
        from rich.syntax import Syntax
        code = '\n'.join(getattr(self, '_lines', []))
        lang = getattr(self, '_lang', '') or 'text'
        _handler_console(self).print(
            Syntax(code, lang, line_numbers=True, theme="monokai")
        )
        object.__setattr__(self, '_lines', [])

    def render(self, content):
        from rich.syntax import Syntax
        console = content.metadata.get('console') or _handler_console(self)
        if content.body:
            lang = content.metadata.get('lang', '') or 'text'
            console.print(
                Syntax(content.body, lang, line_numbers=True, theme="monokai")
            )


class RichThinkingHandler(BlockHandler):
    """Rich handler for mutagent:thinking blocks.

    Renders each line in dim italic style for de-emphasized thinking output.
    """
    _BLOCK_TYPE = "thinking"

    def on_start(self, metadata):
        object.__setattr__(self, '_console', metadata.get('console'))

    def on_line(self, text):
        _handler_console(self).print(f"[dim italic]{text}[/]")

    def on_end(self):
        pass

    def render(self, content):
        console = content.metadata.get('console') or _handler_console(self)
        if content.body:
            console.print(f"[dim italic]{content.body}[/]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tasks_markup(line: str) -> str:
    """Replace task markers with coloured rich markup."""
    if '[x]' in line:
        return line.replace('[x]', '[bold green]\u2705[/]', 1)
    if '[~]' in line:
        return line.replace('[~]', '[bold yellow]\u23f3[/]', 1)
    if '[ ]' in line:
        return line.replace('[ ]', '[dim]\u25fb[/]', 1)
    return line


def _parse_lang(raw: str) -> str:
    """Extract language from raw metadata string.

    Supports formats: ``lang=python``, ``python``, ``lang=python file=x.py``.
    """
    if not raw:
        return ''
    for part in raw.split():
        if part.startswith('lang='):
            return part[5:]
    # First token as bare language name
    return raw.split()[0]
