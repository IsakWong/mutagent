"""Web 框架 Declaration — Server / View / Request / Response 等。

所有公开类型均为 mutobj.Declaration，实现在 _server_impl.py 中。
"""

from __future__ import annotations

import socket as _socket
from typing import Any, AsyncIterator, Sequence

import mutobj


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------


class Request(mutobj.Declaration):
    """HTTP 请求。"""
    method: str = "GET"
    path: str = "/"
    raw_path: str = "/"
    headers: dict[str, str] = mutobj.field(default_factory=dict)
    query_params: dict[str, str] = mutobj.field(default_factory=dict)
    path_params: dict[str, str] = mutobj.field(default_factory=dict)

    async def body(self) -> bytes:
        """读取原始请求体。"""
        ...

    async def json(self) -> Any:
        """读取请求体并解析为 JSON。"""
        ...


class Response(mutobj.Declaration):
    """HTTP 响应。"""
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = mutobj.field(default_factory=dict)


class StreamingResponse(mutobj.Declaration):
    """流式 HTTP 响应。"""
    status: int = 200
    headers: dict[str, str] = mutobj.field(default_factory=dict)
    body_iterator: AsyncIterator[bytes] | None = None
    media_type: str = "text/event-stream"


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


class WebSocketDisconnect(Exception):
    """WebSocket 正常断开异常。"""
    def __init__(self, code: int = 1000) -> None:
        self.code = code
        super().__init__(f"WebSocket disconnected (code={code})")


class WebSocketConnection(mutobj.Declaration):
    """WebSocket 连接。

    在 WebSocketView.connect() 中使用，通过 accept/receive/send/close 管理生命周期。
    """
    path: str = "/"
    query_params: dict[str, str] = mutobj.field(default_factory=dict)
    path_params: dict[str, str] = mutobj.field(default_factory=dict)

    async def accept(self) -> None: ...

    async def receive(self) -> dict[str, Any]:
        """接收消息。返回 ``{"type": "websocket.receive", "text": ...}`` 或 ``{"bytes": ...}``。

        对端关闭时抛出 WebSocketDisconnect。
        """
        ...

    async def receive_json(self) -> Any:
        """接收并解析 JSON 消息。"""
        ...

    async def send_json(self, data: Any) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


# ---------------------------------------------------------------------------
# Server / View / WebSocketView / StaticView
# ---------------------------------------------------------------------------


class Server(mutobj.Declaration):
    """ASGI Server。

    自动发现 View/WebSocketView/StaticView 子类并路由分发。
    子类覆盖 on_startup/on_shutdown 实现生命周期管理。

    子类可设置 ``views`` 限制只路由到指定的 View 子类（元组），
    用于多 Server 实例避免路由冲突。
    """
    host: str = "127.0.0.1"
    port: int = 0
    # 不带注解，作为普通类变量，避免被 DeclarationMeta 转换为 AttributeDescriptor
    views = None  # type: tuple[type[View], ...] | None

    async def route(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """ASGI 入口 — 自动发现 View/WebSocketView 并路径匹配分发。"""
        ...

    async def on_startup(self) -> None:
        """生命周期：启动时调用。子类覆盖以初始化资源。"""
        ...

    async def on_shutdown(self) -> None:
        """生命周期：关闭时调用。子类覆盖以清理资源。"""
        ...

    def run(self, *, listen: Sequence[str | _socket.socket] | None = None) -> None:
        """阻塞运行。listen 接受 "ip:port" 字符串或预创建 socket 的数组。"""
        ...

    async def start(self, *, listen: Sequence[str | _socket.socket] | None = None) -> None:
        """异步启动（在已有 event loop 中使用）。"""
        ...

    async def stop(self) -> None:
        """异步停止。"""
        ...


class View(mutobj.Declaration):
    """HTTP 路由视图基类。

    子类设置 ``path`` 并覆盖对应 HTTP method。path 支持路径参数，如 ``/api/{id}``，
    匹配值通过 ``request.path_params["id"]`` 获取。Server 自动发现所有 View 子类。

    示例::

        class HelloView(View):
            path = "/hello/{name}"

            async def get(self, request: Request) -> Response:
                return json_response({"hello": request.path_params["name"]})
    """
    path: str = ""

    async def get(self, request: Request) -> Response | StreamingResponse: ...
    async def post(self, request: Request) -> Response | StreamingResponse: ...
    async def put(self, request: Request) -> Response | StreamingResponse: ...
    async def delete(self, request: Request) -> Response | StreamingResponse: ...


class WebSocketView(mutobj.Declaration):
    """WebSocket 路由视图基类。

    子类设置 ``path`` 并覆盖 ``connect``。path 格式同 View。
    ``connect`` 返回即断开连接。
    """
    path: str = ""

    async def connect(self, ws: WebSocketConnection) -> None:
        """WebSocket 生命周期入口。方法返回即断开。"""
        ...


class StaticView(View):
    """静态文件服务。directory 为文件系统绝对路径。"""
    directory: str = ""


# ---------------------------------------------------------------------------
# 辅助函数（公开 API）
# ---------------------------------------------------------------------------


def json_response(data: Any, status: int = 200) -> Response:
    """创建 JSON 响应。"""
    import json as _json
    body = _json.dumps(data, ensure_ascii=False).encode("utf-8")
    return Response(
        status=status,
        body=body,
        headers={"content-type": "application/json; charset=utf-8"},
    )


def html_response(html: str, status: int = 200) -> Response:
    """创建 HTML 响应。"""
    body = html.encode("utf-8")
    return Response(
        status=status,
        body=body,
        headers={"content-type": "text/html; charset=utf-8"},
    )
