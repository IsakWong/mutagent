"""MCP Declaration 扩展 — MCPToolSet + MCPView。

通过 mutobj.Declaration 自动发现 MCP tool，零注册。
用户继承 MCPToolSet 定义 tool 方法，MCPView 提供 Streamable HTTP 端点。
"""

from __future__ import annotations

from typing import Any

import mutobj

from mutagent.net.server import View, Request, Response, StreamingResponse


class MCPToolSet(mutobj.Declaration):
    """MCP tool 集合基类。一个类定义一组 tool，方法名就是 tool name。

    归属目标 MCPView 通过两种方式指定（二选一）：

    - ``view``: 直接引用 MCPView 子类（或元组）
    - ``path``: 按路径匹配 MCPView.path（或元组）

    ``prefix`` 为 tool name 前缀，如 prefix="fs" 则方法 read 注册为 "fs_read"。
    """
    prefix: str = ""
    view: type[MCPView] | tuple[type[MCPView], ...] | None = None
    path: str | tuple[str, ...] = ""


class MCPView(View):
    """MCP Streamable HTTP 端点。

    继承 View，被 Server.route 统一发现和分发。
    impl 中包含 JSON-RPC 分发、session 管理、MCPToolProvider 逻辑。
    """
    path: str = ""
    name: str = ""
    version: str = ""
    instructions: str | None = None

    async def post(self, request: Request) -> Response | StreamingResponse: ...
    async def delete(self, request: Request) -> Response: ...
