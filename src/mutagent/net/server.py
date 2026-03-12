"""入站服务 — ASGI Server + MCP Server。"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any

from mutagent.net._asgi import Server, scope_runner
from mutagent.net._mcp_proto import (
    JsonRpcDispatcher,
    JsonRpcError,
    INVALID_PARAMS,
    PROTOCOL_VERSION,
    ServerCapabilities,
    ToolResult,
)
from mutagent.net._protocol import format_sse
from mutagent.net.mcp import MCPToolProvider

logger = logging.getLogger("mutagent.net.server")

__all__ = ["Server", "MCPServer", "mount_mcp", "scope_runner"]


class MCPServer:
    """MCP server — 通过 Declaration 自动发现 tool，Streamable HTTP 提供服务。

    用法::

        mcp = MCPServer(name="my-server", version="1.0.0")
        app = mount_mcp(app, "/mcp", mcp)

    tool 通过继承 MCPToolSet 定义，MCPToolProvider 自动发现::

        class MyTools(MCPToolSet):
            async def search(self, query: str) -> str:
                return "results..."
    """

    def __init__(
        self,
        name: str = "mutagent",
        version: str = "0.1.0",
        *,
        instructions: str | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.instructions = instructions

        self._tool_provider = MCPToolProvider()
        self._sessions: dict[str, _MCPSession] = {}

        self._dispatch = JsonRpcDispatcher()
        self._setup_handlers()

    def _setup_handlers(self) -> None:
        """注册 MCP JSON-RPC 方法。"""
        self._dispatch.add_method("initialize", self._handle_initialize)
        self._dispatch.add_notification("notifications/initialized", self._handle_initialized)
        self._dispatch.add_method("ping", self._handle_ping)
        self._dispatch.add_method("tools/list", self._handle_tools_list)
        self._dispatch.add_method("tools/call", self._handle_tools_call)

    # --- MCP handlers ---

    async def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        tools = self._tool_provider.list_tools()
        capabilities = ServerCapabilities(
            tools={"listChanged": False} if tools else None,
        )
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": capabilities.to_dict(),
            "serverInfo": {
                "name": self.name,
                "version": self.version,
            },
            **({"instructions": self.instructions} if self.instructions else {}),
        }

    async def _handle_initialized(self, params: dict[str, Any]) -> None:
        pass

    async def _handle_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def _handle_tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"tools": self._tool_provider.list_tools()}

    async def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name")
        if not tool_name:
            raise JsonRpcError(INVALID_PARAMS, "Missing tool name")

        arguments = params.get("arguments", {})
        try:
            result = await self._tool_provider.call_tool(tool_name, arguments)
        except JsonRpcError:
            raise
        except Exception as e:
            logger.exception("Tool %s raised exception", tool_name)
            result = ToolResult.error(str(e))

        return result.to_dict()

    # --- ASGI endpoint ---

    async def handle_request(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        """处理 MCP HTTP 端点请求。"""
        method = scope.get("method", "GET")

        if method == "POST":
            await self._handle_post(scope, receive, send)
        elif method == "GET":
            await self._handle_get(scope, receive, send)
        elif method == "DELETE":
            await self._handle_delete(scope, receive, send)
        else:
            await _send_json_response(send, 405, {"error": "Method not allowed"})

    async def _handle_post(self, scope: dict, receive: Any, send: Any) -> None:
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body", False):
                break

        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            await _send_json_response(send, 400, {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            })
            return

        messages = parsed if isinstance(parsed, list) else [parsed]
        has_request = any(
            isinstance(m, dict) and "id" in m and "method" in m
            for m in messages
        )

        if not has_request:
            for msg in messages:
                if isinstance(msg, dict):
                    await self._dispatch.handle(msg)
            await _send_empty_response(send, 202)
            return

        headers = dict(scope.get("headers", []))
        accept = headers.get(b"accept", b"").decode()

        if isinstance(parsed, list):
            responses = []
            for msg in parsed:
                if isinstance(msg, dict):
                    resp = await self._dispatch.handle(msg)
                    if resp is not None:
                        responses.append(resp)
            result_data = responses if len(responses) != 1 else responses[0]
        else:
            result_data = await self._dispatch.handle(parsed)

        if result_data is None:
            await _send_empty_response(send, 202)
            return

        extra_headers: list[tuple[bytes, bytes]] = []
        if isinstance(parsed, dict) and parsed.get("method") == "initialize":
            session_id = secrets.token_hex(16)
            session = _MCPSession(session_id=session_id)
            self._sessions[session_id] = session
            extra_headers.append((b"mcp-session-id", session_id.encode()))

        if "text/event-stream" in accept:
            await _send_sse_response(send, result_data, extra_headers)
        else:
            await _send_json_response(send, 200, result_data, extra_headers)

    async def _handle_get(self, scope: dict, receive: Any, send: Any) -> None:
        await _send_json_response(send, 405, {"error": "GET not supported"})

    async def _handle_delete(self, scope: dict, receive: Any, send: Any) -> None:
        headers = dict(scope.get("headers", []))
        session_id = headers.get(b"mcp-session-id", b"").decode()
        if session_id and session_id in self._sessions:
            del self._sessions[session_id]
            await _send_empty_response(send, 200)
        else:
            await _send_empty_response(send, 404)


class _MCPSession:
    """MCP session 状态。"""
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.initialized = False


def mount_mcp(app: Any, path: str, mcp: MCPServer) -> Any:
    """将 MCPServer 挂载到 ASGI app 的指定路径。"""
    path = path.rstrip("/")

    async def mcp_app(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"].rstrip("/") == path:
            await mcp.handle_request(scope, receive, send)
        else:
            await app(scope, receive, send)

    return mcp_app


# --- 内部辅助 ---

async def _send_json_response(
    send: Any,
    status: int,
    data: Any,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = json.dumps(data).encode()
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _send_empty_response(send: Any, status: int) -> None:
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-length", b"0")],
    })
    await send({"type": "http.response.body", "body": b""})


async def _send_sse_response(
    send: Any,
    data: Any,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"text/event-stream"),
        (b"cache-control", b"no-cache"),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": 200, "headers": headers})

    sse_data = format_sse(json.dumps(data), event="message")
    await send({"type": "http.response.body", "body": sse_data, "more_body": False})
