"""mutagent.runtime.ansi -- Lightweight ANSI color utilities.

Provides terminal color detection and ANSI SGR wrapper functions for the
basic interactive mode.  Uses only standard 8-color indices (30-37) so that
actual RGB values are determined by the user's terminal color scheme --
dark and light themes both get readable colors automatically.

Respects the ``NO_COLOR`` / ``FORCE_COLOR`` environment conventions.
"""

from __future__ import annotations

import os
import re
import sys
from functools import lru_cache

# ---------------------------------------------------------------------------
# Terminal capability detection
# ---------------------------------------------------------------------------

def _enable_windows_ansi() -> bool:
    """Enable ANSI/VT processing on Windows 10+."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _color_supported() -> bool:
    """Check if the terminal supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        return _enable_windows_ansi()
    return True


# ---------------------------------------------------------------------------
# ANSI SGR codes
# ---------------------------------------------------------------------------

_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_ITALIC = "\033[3m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"

# ---------------------------------------------------------------------------
# Color wrapper functions
# ---------------------------------------------------------------------------

def dim(text: str) -> str:
    """Wrap *text* with dim (faint) styling."""
    if not _color_supported():
        return text
    return f"{_DIM}{text}{_RESET}"


def bold(text: str) -> str:
    """Wrap *text* with bold styling."""
    if not _color_supported():
        return text
    return f"{_BOLD}{text}{_RESET}"


def green(text: str) -> str:
    """Wrap *text* with green foreground."""
    if not _color_supported():
        return text
    return f"{_GREEN}{text}{_RESET}"


def red(text: str) -> str:
    """Wrap *text* with red foreground."""
    if not _color_supported():
        return text
    return f"{_RED}{text}{_RESET}"


def bold_red(text: str) -> str:
    """Wrap *text* with bold red foreground."""
    if not _color_supported():
        return text
    return f"{_BOLD}{_RED}{text}{_RESET}"


def yellow(text: str) -> str:
    """Wrap *text* with yellow foreground."""
    if not _color_supported():
        return text
    return f"{_YELLOW}{text}{_RESET}"


def cyan(text: str) -> str:
    """Wrap *text* with cyan foreground."""
    if not _color_supported():
        return text
    return f"{_CYAN}{text}{_RESET}"


def bold_cyan(text: str) -> str:
    """Wrap *text* with bold cyan foreground."""
    if not _color_supported():
        return text
    return f"{_BOLD}{_CYAN}{text}{_RESET}"


# ---------------------------------------------------------------------------
# Markdown lightweight syntax highlighting
# ---------------------------------------------------------------------------

# Line-start patterns that highlight the ENTIRE line (marker + content)
_MD_LINE_FULL = [
    re.compile(r'^#{1,6}\s'),              # headings
    re.compile(r'^>\s?'),                   # blockquote
]

# Line-start patterns that highlight ONLY the marker (content stays default)
_MD_LINE_MARKER_ONLY = [
    (re.compile(r'^(\s*[-*+]\s)(.*)$'), 1),      # unordered list
    (re.compile(r'^(\s*\d+\.\s)(.*)$'), 1),       # ordered list
]

# Inline markers (can match multiple times per line)
_MD_BOLD_RE = re.compile(r'(\*\*[^*]+\*\*|__[^_]+__)')
_MD_INLINE_CODE_RE = re.compile(r'(`[^`]+`)')


def highlight_markdown_line(line: str) -> str:
    """Apply lightweight Markdown syntax highlighting to a single line.

    - Headings and blockquotes: entire line highlighted
    - Lists: only the marker highlighted, content stays default
    - Bold and inline code: entire span highlighted
    Returns the line unchanged when color is disabled.
    """
    if not _color_supported():
        return line

    # Headings and blockquotes: highlight entire line
    for pattern in _MD_LINE_FULL:
        if pattern.match(line):
            return f"{_CYAN}{line}{_RESET}"

    # Lists: highlight only the marker
    for pattern, group_idx in _MD_LINE_MARKER_ONLY:
        m = pattern.match(line)
        if m:
            marker = m.group(group_idx)
            rest = m.group(group_idx + 1)
            rest = _apply_inline_patterns(rest)
            return f"{_CYAN}{marker}{_RESET}{rest}"

    # No line-start match -- apply inline patterns to the whole line
    return _apply_inline_patterns(line)


def _apply_inline_patterns(text: str) -> str:
    """Apply inline Markdown highlighting (bold spans, inline code)."""
    # Inline code first (takes precedence) -- yellow to distinguish from cyan headings
    text = _MD_INLINE_CODE_RE.sub(f"{_YELLOW}\\1{_RESET}", text)
    # Bold spans -- cyan
    text = _MD_BOLD_RE.sub(f"{_CYAN}\\1{_RESET}", text)
    return text
