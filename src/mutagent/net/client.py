"""出站连接 Declaration — HttpClient + MCPClient。"""

from __future__ import annotations

from typing import Any

import httpx

import mutobj


# ---------------------------------------------------------------------------
# HttpClient (Declaration)
# ---------------------------------------------------------------------------


class HttpClient(mutobj.Declaration):
    """HTTP 客户端工厂。

    提供统一的 httpx.AsyncClient 创建入口，集中管理默认 headers（User-Agent 等）。
    上层项目（如 mutbot）可通过 @impl 覆盖以定制 User-Agent。
    """

    @staticmethod
    def create(**kwargs: Any) -> httpx.AsyncClient:
        """创建 httpx.AsyncClient，统一设置默认 headers。"""
        ...


# ---------------------------------------------------------------------------
# MCPClient (Declaration)
# ---------------------------------------------------------------------------


class MCPClient(mutobj.Declaration):
    """MCP client — 通过 Streamable HTTP 连接 MCP server。

    用法::

        client = MCPClient(url="http://localhost:8000/mcp")
        await client.connect()
        try:
            tools = await client.list_tools()
            result = await client.call_tool("search", query="hello")
        finally:
            await client.close()
    """
    url: str = ""
    client_name: str = "mutagent"
    client_version: str = "0.1.0"
    timeout: float = 30.0
    server_info: dict[str, Any] = mutobj.field(default_factory=dict)
    server_capabilities: dict[str, Any] = mutobj.field(default_factory=dict)

    async def connect(self) -> None:
        """连接并完成 MCP initialize 握手。"""
        ...

    async def close(self) -> None:
        """关闭连接。"""
        ...

    async def list_tools(self) -> list[dict[str, Any]]:
        """获取 server 可用 tools。"""
        ...

    async def call_tool(self, name: str, **arguments: Any) -> dict[str, Any]:
        """调用 tool。返回 ``{"content": [...], "isError": bool}``。"""
        ...

    async def list_resources(self) -> list[dict[str, Any]]:
        """获取 server 可用 resources。"""
        ...

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """读取 resource。"""
        ...

    async def list_prompts(self) -> list[dict[str, Any]]:
        """获取 server 可用 prompts。"""
        ...

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """获取 prompt。"""
        ...

    async def ping(self) -> None:
        """Ping server。"""
        ...


# ---------------------------------------------------------------------------
# MCPError
# ---------------------------------------------------------------------------


class MCPError(Exception):
    """MCP 协议错误。"""
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"MCP error {code}: {message}")
