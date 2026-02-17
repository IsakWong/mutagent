"""mutagent.builtins.tool_set -- ToolSet implementation."""

import inspect
import logging
from typing import Any

import mutagent
from mutagent.messages import ToolCall, ToolResult, ToolSchema
from mutagent.tool_set import ToolEntry, ToolSet

logger = logging.getLogger(__name__)


def _get_entries(self: ToolSet) -> dict[str, ToolEntry]:
    """Get or initialize the internal entries dict."""
    entries = getattr(self, '_entries', None)
    if entries is None:
        entries = {}
        object.__setattr__(self, '_entries', entries)
    return entries


def _make_schema_from_function(func, name: str | None = None) -> ToolSchema:
    """Generate a ToolSchema from a standalone function using inspect."""
    import ast
    import textwrap

    func_name = name or getattr(func, '__name__', 'unknown')

    # Try AST parsing first for accurate signatures
    try:
        source = inspect.getsource(func)
        source = textwrap.dedent(source)
        tree = ast.parse(source)
        node = tree.body[0]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            from mutagent.builtins.selector_impl import _extract_from_funcdef, _python_type_to_json_type
            docstring, params = _extract_from_funcdef(node)
            description = docstring.split("\n")[0].strip() if docstring else func_name

            properties: dict[str, Any] = {}
            required: list[str] = []

            for param in params:
                prop: dict[str, Any] = {
                    "type": _python_type_to_json_type(param["type"]),
                    "description": param["name"],
                }
                if not param.get("required", True):
                    prop["default"] = param.get("default")
                else:
                    required.append(param["name"])
                properties[param["name"]] = prop

            input_schema: dict[str, Any] = {
                "type": "object",
                "properties": properties,
            }
            if required:
                input_schema["required"] = required

            return ToolSchema(
                name=func_name,
                description=description,
                input_schema=input_schema,
            )
    except (OSError, TypeError):
        pass

    # Fallback: use inspect.signature
    sig = inspect.signature(func)
    doc = inspect.getdoc(func) or func_name
    description = doc.split("\n")[0].strip()

    properties = {}
    required = []
    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        prop = {"type": "string", "description": pname}
        if param.default is inspect.Parameter.empty:
            required.append(pname)
        else:
            prop["default"] = param.default
        properties[pname] = prop

    input_schema = {"type": "object", "properties": properties}
    if required:
        input_schema["required"] = required

    return ToolSchema(name=func_name, description=description, input_schema=input_schema)


@mutagent.impl(ToolSet.add)
def add(self: ToolSet, source: Any, methods: list[str] | None = None) -> None:
    """Add tools from a source object or callable."""
    entries = _get_entries(self)

    if callable(source) and not isinstance(source, type) and methods is None:
        # Single callable (function or lambda)
        name = getattr(source, '__name__', 'unknown')
        schema = _make_schema_from_function(source, name)
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

    from mutagent.builtins.selector_impl import make_schema_from_method

    for method_name in method_names:
        if method_name not in cls_dict:
            logger.warning("Method %s not found in %s.__dict__, skipping", method_name, cls.__name__)
            continue
        bound_method = getattr(source, method_name)
        schema = make_schema_from_method(source, method_name)
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
