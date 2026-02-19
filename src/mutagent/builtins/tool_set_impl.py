"""mutagent.builtins.tool_set -- ToolSet implementation."""

import logging
from typing import Any

import mutagent
from mutagent.messages import ToolCall, ToolResult, ToolSchema
from mutagent.schema import get_declaration_method, make_schema
from mutagent.tool_set import ToolEntry, ToolSet

logger = logging.getLogger(__name__)


def _get_entries(self: ToolSet) -> dict[str, ToolEntry]:
    """Get or initialize the internal entries dict."""
    entries = getattr(self, '_entries', None)
    if entries is None:
        entries = {}
        object.__setattr__(self, '_entries', entries)
    return entries


@mutagent.impl(ToolSet.add)
def add(self: ToolSet, source: Any, methods: list[str] | None = None) -> None:
    """Add tools from a source object or callable."""
    entries = _get_entries(self)

    if callable(source) and not isinstance(source, type) and methods is None:
        # Single callable (function or lambda)
        name = getattr(source, '__name__', 'unknown')
        schema = make_schema(source, name)
        entries[name] = ToolEntry(
            name=name, callable=source, schema=schema, source=source,
        )
        return

    # Object instance: register its methods
    cls = type(source)
    cls_dict = cls.__dict__

    if methods is not None:
        method_names = methods
    else:
        # All public methods defined directly on the class
        method_names = [
            name for name, val in cls_dict.items()
            if not name.startswith("_") and callable(val)
        ]

    for method_name in method_names:
        if method_name not in cls_dict:
            logger.warning("Method %s not found in %s.__dict__, skipping", method_name, cls.__name__)
            continue
        bound_method = getattr(source, method_name)
        # Use declaration method for schema (preserves original signature/docstring)
        decl_method = get_declaration_method(cls, method_name)
        schema = make_schema(decl_method, method_name)
        entries[method_name] = ToolEntry(
            name=method_name,
            callable=bound_method,
            schema=schema,
            source=source,
        )


@mutagent.impl(ToolSet.remove)
def remove(self: ToolSet, tool_name: str) -> bool:
    """Remove a tool by name."""
    entries = _get_entries(self)
    if tool_name in entries:
        del entries[tool_name]
        return True
    return False


@mutagent.impl(ToolSet.query)
def query(self: ToolSet, tool_name: str) -> ToolSchema | None:
    """Query a tool's schema by name."""
    entries = _get_entries(self)
    entry = entries.get(tool_name)
    return entry.schema if entry else None


@mutagent.impl(ToolSet.get_tools)
def get_tools(self: ToolSet) -> list[ToolSchema]:
    """Return all tool schemas."""
    entries = _get_entries(self)
    return [entry.schema for entry in entries.values()]


@mutagent.impl(ToolSet.dispatch)
def dispatch(self: ToolSet, tool_call: ToolCall) -> ToolResult:
    """Dispatch a tool call to the corresponding implementation."""
    entries = _get_entries(self)
    entry = entries.get(tool_call.name)
    if entry is None:
        return ToolResult(
            tool_call_id=tool_call.id,
            content=f"Unknown tool: {tool_call.name}",
            is_error=True,
        )
    try:
        result = entry.callable(**tool_call.arguments)
        return ToolResult(tool_call_id=tool_call.id, content=str(result))
    except Exception as e:
        return ToolResult(
            tool_call_id=tool_call.id,
            content=f"{type(e).__name__}: {e}",
            is_error=True,
        )
