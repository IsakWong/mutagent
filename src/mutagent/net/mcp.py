"""MCP Declaration 扩展 — MCPToolSet + MCPToolProvider。

通过 mutobj.Declaration 自动发现 MCP tool，零注册。
用户继承 MCPToolSet 定义 tool 方法，MCPToolProvider 自动发现并桥接到 MCP handler。
"""

from __future__ import annotations

import inspect
from typing import Any

import mutobj

from mutagent.net._mcp_proto import JsonRpcError, INVALID_PARAMS, ToolResult


class MCPToolSet(mutobj.Declaration):
    """MCP tool 集合基类。一个类定义一组 tool，方法名就是 tool name。"""
    prefix: str = ""


class MCPToolProvider:
    """generation 检查 + 懒刷新，桥接 Declaration 发现到 MCP handler。"""

    def __init__(self) -> None:
        self._gen: int = -1
        self._tools: dict[str, tuple[MCPToolSet, str]] = {}

    def refresh(self) -> None:
        gen = mutobj.get_registry_generation()
        if gen != self._gen:
            self._gen = gen
            self._tools = {}
            for cls in mutobj.discover_subclasses(MCPToolSet):
                instance = cls()
                prefix = instance.prefix
                for name in dir(cls):
                    if name.startswith("_"):
                        continue
                    if name in ("prefix",):
                        continue
                    attr = getattr(cls, name, None)
                    if attr is not None and (inspect.isfunction(attr) or inspect.ismethod(attr)):
                        if name in dir(MCPToolSet):
                            continue
                        tool_name = f"{prefix}{name}" if prefix else name
                        self._tools[tool_name] = (instance, name)

    def list_tools(self) -> list[dict[str, Any]]:
        """从类型注解 + docstring 自动生成 tool schema。"""
        self.refresh()
        result: list[dict[str, Any]] = []
        for tool_name, (instance, method_name) in self._tools.items():
            method = getattr(instance, method_name)
            schema = _infer_schema(method)
            result.append({
                "name": tool_name,
                "description": method.__doc__ or "",
                "inputSchema": schema,
            })
        return result

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        self.refresh()
        if name not in self._tools:
            raise JsonRpcError(INVALID_PARAMS, f"Unknown tool: {name}")
        instance, method_name = self._tools[name]
        method = getattr(instance, method_name)
        result = await method(**args)
        if isinstance(result, str):
            return ToolResult.text(result)
        if isinstance(result, ToolResult):
            return result
        return ToolResult.text(str(result))


def _infer_schema(fn: Any) -> dict[str, Any]:
    """从函数签名推断 JSON Schema（简易版）。"""
    sig = inspect.signature(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []

    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
    }

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        annotation = param.annotation
        json_type = type_map.get(annotation, "string")
        properties[name] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema
