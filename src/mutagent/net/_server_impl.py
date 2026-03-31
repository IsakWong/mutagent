"""server.py Declaration 实现 — @impl + Extension。"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import socket as _socket
from pathlib import Path
from typing import Any, Sequence, cast
from urllib.parse import parse_qs, unquote

import mutobj

from mutagent.net.server import (
    Request,
    Response,
    StreamingResponse,
    WebSocketConnection,
    WebSocketDisconnect,
    Server,
    View,
    WebSocketView,
    StaticView,
)

logger = logging.getLogger("mutagent.net.server")


# ---------------------------------------------------------------------------
# Extensions — 承载 ASGI 私有状态
# ---------------------------------------------------------------------------


class _RequestExt(mutobj.Extension[Request]):
    """Request 的 ASGI 内部状态。"""
    _receive: Any = None
    _body: bytes | None = None


class _WebSocketExt(mutobj.Extension[WebSocketConnection]):
    """WebSocketConnection 的 ASGI 内部状态。"""
    _receive: Any = None
    _send: Any = None


class _StreamingResponseExt(mutobj.Extension[StreamingResponse]):
    """StreamingResponse 的 ASGI 内部状态（备用，body_iterator 已在 Declaration 中）。"""


# ---------------------------------------------------------------------------
# Extension for Server — 承载 _asgi.Server 实例 + 路由状态
# ---------------------------------------------------------------------------


class _ServerExt(mutobj.Extension[Server]):
    """Server 的运行时状态。"""
    _asgi_server: Any = None
    _routes: list[Any] = mutobj.field(default_factory=list)
    _static_dirs: list[tuple[str, Path]] = mutobj.field(default_factory=list)
    _gen: int = -1
    _allowed_views: tuple[type, ...] | None = None  # 缓存的 views 限制


# ---------------------------------------------------------------------------
# Request @impl
# ---------------------------------------------------------------------------


@mutobj.impl(Request.body)
async def _request_body(self: Request) -> bytes:
    ext = _RequestExt.get_or_create(self)
    if ext._body is not None:
        return ext._body
    chunks: list[bytes] = []
    while True:
        msg = await ext._receive()
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body", False):
            break
    ext._body = b"".join(chunks)
    return ext._body


@mutobj.impl(Request.json)
async def _request_json(self: Request) -> Any:
    raw = await self.body()
    return json.loads(raw)


# ---------------------------------------------------------------------------
# WebSocketConnection @impl
# ---------------------------------------------------------------------------


@mutobj.impl(WebSocketConnection.accept)
async def _ws_accept(self: WebSocketConnection) -> None:
    ext = _WebSocketExt.get_or_create(self)
    await ext._send({"type": "websocket.accept"})


@mutobj.impl(WebSocketConnection.receive)
async def _ws_receive(self: WebSocketConnection) -> dict[str, Any]:
    ext = _WebSocketExt.get_or_create(self)
    return await ext._receive()


@mutobj.impl(WebSocketConnection.receive_json)
async def _ws_receive_json(self: WebSocketConnection) -> Any:
    ext = _WebSocketExt.get_or_create(self)
    while True:
        msg = await ext._receive()
        if msg.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect(msg.get("code", 1000))
        if "text" in msg:
            return json.loads(msg["text"])


@mutobj.impl(WebSocketConnection.send_json)
async def _ws_send_json(self: WebSocketConnection, data: Any) -> None:
    ext = _WebSocketExt.get_or_create(self)
    await ext._send({
        "type": "websocket.send",
        "text": json.dumps(data, ensure_ascii=False),
    })


@mutobj.impl(WebSocketConnection.send_bytes)
async def _ws_send_bytes(self: WebSocketConnection, data: bytes) -> None:
    ext = _WebSocketExt.get_or_create(self)
    await ext._send({"type": "websocket.send", "bytes": data})


@mutobj.impl(WebSocketConnection.close)
async def _ws_close(self: WebSocketConnection, code: int = 1000, reason: str = "") -> None:
    ext = _WebSocketExt.get_or_create(self)
    await ext._send({"type": "websocket.close", "code": code, "reason": reason})


# ---------------------------------------------------------------------------
# View / WebSocketView / StaticView 默认 @impl
# ---------------------------------------------------------------------------


@mutobj.impl(View.get)
async def _view_get(self: View, request: Request) -> Response | StreamingResponse:
    return Response(status=405)


@mutobj.impl(View.post)
async def _view_post(self: View, request: Request) -> Response | StreamingResponse:
    return Response(status=405)


@mutobj.impl(View.put)
async def _view_put(self: View, request: Request) -> Response | StreamingResponse:
    return Response(status=405)


@mutobj.impl(View.delete)
async def _view_delete(self: View, request: Request) -> Response | StreamingResponse:
    return Response(status=405)


@mutobj.impl(WebSocketView.connect)
async def _ws_view_connect(self: WebSocketView, ws: WebSocketConnection) -> None:
    await ws.close(code=4405, reason="Not implemented")


@mutobj.impl(StaticView.get)
async def _static_view_get(self: StaticView, request: Request) -> Response | StreamingResponse:
    if not self.directory:
        return Response(status=404, body=b"Not Found")
    directory = Path(self.directory)
    rel = request.path
    if not rel or rel == "/":
        rel = "/index.html"
    file_path = directory / rel.lstrip("/")
    try:
        resolved = file_path.resolve()
        if not str(resolved).startswith(str(directory.resolve())):
            return Response(status=403, body=b"Forbidden")
    except (OSError, ValueError):
        return Response(status=404, body=b"Not Found")
    if resolved.is_file():
        return _serve_file(resolved)
    if "." not in resolved.name:
        index = directory / "index.html"
        if index.is_file():
            return _serve_file(index)
    return Response(status=404, body=b"Not Found")


def _serve_file(file_path: Path) -> Response:
    """读取文件并构造 Response。"""
    content_type, _ = mimetypes.guess_type(str(file_path))
    if content_type is None:
        content_type = "application/octet-stream"

    body = file_path.read_bytes()
    headers: dict[str, str] = {
        "content-type": content_type,
        "content-length": str(len(body)),
    }
    if content_type.startswith("text/html"):
        headers["cache-control"] = "no-cache"
    else:
        headers["cache-control"] = "public, max-age=86400"

    return Response(status=200, body=body, headers=headers)


# ---------------------------------------------------------------------------
# Server @impl — on_startup / on_shutdown 默认空操作
# ---------------------------------------------------------------------------


@mutobj.impl(Server.on_startup)
async def _server_on_startup(self: Server) -> None:
    pass


@mutobj.impl(Server.on_shutdown)
async def _server_on_shutdown(self: Server) -> None:
    pass


@mutobj.impl(Server.before_route)
async def _server_before_route(self: Server, scope: dict[str, Any], path: str) -> Response | None:
    return None


# ---------------------------------------------------------------------------
# Server @impl — route（吸收 Router 逻辑）
# ---------------------------------------------------------------------------


_PARAM_RE = re.compile(r"\{(\w+)\}")


def _compile_path(path: str) -> tuple[re.Pattern[str], list[str]]:
    """将 /foo/{bar}/baz 编译为正则 + 参数名列表。"""
    param_names: list[str] = []
    regex_parts: list[str] = []
    last_end = 0
    for m in _PARAM_RE.finditer(path):
        regex_parts.append(re.escape(path[last_end:m.start()]))
        regex_parts.append(r"([^/]+)")
        param_names.append(m.group(1))
        last_end = m.end()
    regex_parts.append(re.escape(path[last_end:]))
    pattern = re.compile("^" + "".join(regex_parts) + "$")
    return pattern, param_names


class _Route:
    __slots__ = ("path", "pattern", "param_names", "handler", "is_ws")

    def __init__(self, path: str, handler: Any, *, is_ws: bool = False) -> None:
        self.path = path
        self.pattern, self.param_names = _compile_path(path)
        self.handler = handler
        self.is_ws = is_ws


def _is_view_allowed(ext: _ServerExt, view_cls: type) -> bool:
    """检查 view 是否在 Server.views 限制范围内。"""
    if ext._allowed_views is None:
        return True
    # 使用类名比较，避免 reload 导致的身份不匹配
    allowed_names = {v.__name__ for v in ext._allowed_views}
    return view_cls.__name__ in allowed_names


def _discover_routes(ext: _ServerExt, server: Server) -> None:
    """从 Declaration 注册表发现 View/WebSocketView 子类，缓存路由。"""
    gen = mutobj.get_registry_generation()
    if gen == ext._gen:
        return
    ext._gen = gen
    ext._routes = []
    ext._static_dirs = []
    # 缓存 views 限制
    ext._allowed_views = server.views

    for view_cls in mutobj.discover_subclasses(View):
        # 检查是否在 views 限制范围内
        if not _is_view_allowed(ext, view_cls):
            continue
        # StaticView 特殊处理 — 记录静态目录
        if issubclass(view_cls, StaticView):
            view = view_cls()
            if view.path and view.directory:
                ext._static_dirs.append((view.path.rstrip("/"), Path(view.directory)))
            continue
        view = view_cls()
        if view.path:
            ext._routes.append(_Route(view.path, view, is_ws=False))

    for ws_cls in mutobj.discover_subclasses(WebSocketView):
        # 检查是否在 views 限制范围内
        if not _is_view_allowed(ext, ws_cls):
            continue
        ws_view = ws_cls()
        if ws_view.path:
            ext._routes.append(_Route(ws_view.path, ws_view, is_ws=True))


def _match_route(
    ext: _ServerExt, path: str, *, ws: bool = False,
) -> tuple[Any, dict[str, str]] | None:
    """路径匹配。"""
    for route in ext._routes:
        if route.is_ws != ws:
            continue
        m = route.pattern.match(path)
        if m:
            params = {
                name: unquote(val)
                for name, val in zip(route.param_names, m.groups())
            }
            return route.handler, params
    return None


def _make_request(scope: dict[str, Any], receive: Any, path_params: dict[str, str]) -> Request:
    """从 ASGI scope 构造 Request，通过 Extension 附加 _receive。"""
    raw_headers = scope.get("headers", [])
    headers = {
        k.decode("latin-1"): v.decode("latin-1")
        for k, v in raw_headers
    }

    qs = scope.get("query_string", b"")
    if isinstance(qs, bytes):
        qs = qs.decode("latin-1")
    parsed_qs_raw = parse_qs(qs, keep_blank_values=True)
    query_params = {k: v[0] for k, v in parsed_qs_raw.items()}

    raw_path_bytes = scope.get("raw_path", scope.get("path", "/").encode())
    raw_path_str = raw_path_bytes.decode("latin-1") if isinstance(raw_path_bytes, bytes) else scope.get("path", "/")

    request = Request(
        method=scope.get("method", "GET"),
        path=scope.get("path", "/"),
        raw_path=raw_path_str,
        headers=headers,
        query_params=query_params,
        path_params=path_params,
    )
    _RequestExt.get_or_create(request)._receive = receive
    return request


def _make_ws_connection(
    scope: dict[str, Any], receive: Any, send: Any, path_params: dict[str, str],
) -> WebSocketConnection:
    """从 ASGI scope 构造 WebSocketConnection，通过 Extension 附加 ASGI 回调。"""
    raw_headers = scope.get("headers", [])
    headers = {
        k.decode("latin-1"): v.decode("latin-1")
        for k, v in raw_headers
    }

    qs = scope.get("query_string", b"")
    if isinstance(qs, bytes):
        qs = qs.decode("latin-1")
    parsed_qs_raw = parse_qs(qs, keep_blank_values=True)
    query_params = {k: v[0] for k, v in parsed_qs_raw.items()}

    ws = WebSocketConnection(
        path=scope.get("path", "/"),
        query_params=query_params,
        path_params=path_params,
        headers=headers,
    )
    ext = _WebSocketExt.get_or_create(ws)
    ext._receive = receive
    ext._send = send
    return ws


async def _send_response(response: Response, send_fn: Any) -> None:
    """将 Response 通过 ASGI send 发出。"""
    raw_headers: list[tuple[bytes, bytes]] = [
        (k.encode(), v.encode()) for k, v in response.headers.items()
    ]
    if "content-length" not in response.headers:
        raw_headers.append((b"content-length", str(len(response.body)).encode()))
    await send_fn({
        "type": "http.response.start",
        "status": response.status,
        "headers": raw_headers,
    })
    await send_fn({"type": "http.response.body", "body": response.body})


async def _send_streaming_response(response: StreamingResponse, send_fn: Any) -> None:
    """将 StreamingResponse 通过 ASGI send 发出。"""
    headers = dict(response.headers)
    headers.setdefault("content-type", response.media_type)
    raw_headers: list[tuple[bytes, bytes]] = [
        (k.encode(), v.encode()) for k, v in headers.items()
    ]
    await send_fn({
        "type": "http.response.start",
        "status": response.status,
        "headers": raw_headers,
    })
    if response.body_iterator is not None:
        async for chunk in response.body_iterator:
            await send_fn({
                "type": "http.response.body",
                "body": chunk if isinstance(chunk, bytes) else chunk.encode(),
                "more_body": True,
            })
    await send_fn({"type": "http.response.body", "body": b"", "more_body": False})


@mutobj.impl(Server.route)
async def _server_route(
    self: Server, scope: dict[str, Any], receive: Any, send: Any,
) -> None:
    """ASGI 入口 — 路径匹配 + View/WebSocketView 自动发现 + 静态文件 fallback。"""
    ext = cast(_ServerExt, _ServerExt.get_or_create(self))
    _discover_routes(ext, self)

    scope_type = scope.get("type")
    path: str = scope.get("path", "/")

    # --- base_path strip ---
    bp = self.base_path
    if bp:
        if path == bp or path.startswith(bp + "/"):
            path = path[len(bp):] or "/"
        else:
            # 不匹配 base_path → 404
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 4404, "reason": "Not found"})
            elif scope_type == "http":
                await _send_response(Response(status=404), send)
            return

    # --- WebSocket ---
    if scope_type == "websocket":
        result = _match_route(ext, path, ws=True)
        if result:
            # before_route 钩子
            intercept = await self.before_route(scope, path)
            if intercept is not None:
                await send({"type": "websocket.close", "code": intercept.status, "reason": "Unauthorized"})
                return
            ws_view, params = result
            ws_conn = _make_ws_connection(scope, receive, send, params)
            try:
                await ws_view.connect(ws_conn)
            except Exception:
                logger.exception("WebSocket error: %s", path)
        else:
            await send({"type": "websocket.close", "code": 4404, "reason": "Not found"})
        return

    # --- HTTP ---
    if scope_type == "http":
        # before_route 钩子（对所有 HTTP 请求，含静态文件）
        intercept = await self.before_route(scope, path)
        if intercept is not None:
            if isinstance(intercept, StreamingResponse):
                await _send_streaming_response(intercept, send)
            else:
                await _send_response(intercept, send)
            return
        result = _match_route(ext, path, ws=False)
        if result:
            view, params = result
            request = _make_request(scope, receive, params)
            method = scope.get("method", "GET").lower()
            handler = getattr(view, method, None)
            if handler is None:
                resp: Response | StreamingResponse = Response(status=405)
            else:
                try:
                    resp = await handler(request)
                except Exception:
                    logger.exception("HTTP handler error: %s %s", scope.get("method"), path)
                    from mutagent.net.server import json_response
                    resp = json_response({"error": "Internal Server Error"}, status=500)
            if isinstance(resp, StreamingResponse):
                await _send_streaming_response(resp, send)
            else:
                await _send_response(resp, send)
            return

        # 静态文件 fallback
        if scope.get("method") == "GET":
            for prefix, directory in ext._static_dirs:
                rel = path[len(prefix):] if path.startswith(prefix) else None
                if rel is None:
                    continue
                if not rel or rel == "/":
                    rel = "/index.html"
                file_path = directory / rel.lstrip("/")
                try:
                    resolved = file_path.resolve()
                    if not str(resolved).startswith(str(directory.resolve())):
                        continue
                except (OSError, ValueError):
                    continue
                if resolved.is_file():
                    resp = _serve_file(resolved)
                    await _send_response(resp, send)
                    return
                if "." not in resolved.name:
                    index = directory / "index.html"
                    if index.is_file():
                        resp = _serve_file(index)
                        await _send_response(resp, send)
                        return

        resp = Response(status=404, body=b"Not Found")
        await _send_response(resp, send)
        return


# ---------------------------------------------------------------------------
# Server @impl — run / start / stop
# ---------------------------------------------------------------------------


def _parse_listen_arg(
    listen: Sequence[str | _socket.socket] | None,
    server: Server,
) -> tuple[list[_socket.socket], str | None, int | None]:
    """解析 listen 参数，返回 (sockets, host, port)。"""
    if listen is None:
        # 用 Declaration 属性
        return [], server.host, server.port

    sockets: list[_socket.socket] = []
    host: str | None = None
    port: int | None = None

    for item in listen:
        if isinstance(item, _socket.socket):
            sockets.append(item)
        elif isinstance(item, str):
            if ":" in item:
                h, p = item.rsplit(":", 1)
                host, port = h, int(p)
            else:
                host, port = "127.0.0.1", int(item)
            # 创建 socket
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            sockets.append(sock)

    if sockets:
        return sockets, None, None
    return [], host, port


@mutobj.impl(Server.run)
def _server_run(
    self: Server,
    *,
    listen: Sequence[str | _socket.socket] | None = None,
) -> None:
    from mutagent.net.asgi import Server as _ASGIServer

    sockets, host, port = _parse_listen_arg(listen, self)

    # 包装 ASGI app：lifespan → on_startup/on_shutdown，其余 → route
    async def _asgi_app(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await _handle_lifespan(self, scope, receive, send)
        else:
            await self.route(scope, receive, send)

    asgi_server = _ASGIServer(_asgi_app)
    ext = cast(_ServerExt, _ServerExt.get_or_create(self))
    ext._asgi_server = asgi_server

    if sockets:
        asgi_server.run(sockets=sockets)
    elif host is not None and port is not None:
        asgi_server.run(host=host, port=port)
    else:
        asgi_server.run(host=self.host, port=self.port)


@mutobj.impl(Server.start)
async def _server_start(
    self: Server,
    *,
    listen: Sequence[str | _socket.socket] | None = None,
) -> None:
    from mutagent.net.asgi import Server as _ASGIServer

    sockets, host, port = _parse_listen_arg(listen, self)

    async def _asgi_app(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await _handle_lifespan(self, scope, receive, send)
        else:
            await self.route(scope, receive, send)

    asgi_server = _ASGIServer(_asgi_app)
    ext = cast(_ServerExt, _ServerExt.get_or_create(self))
    ext._asgi_server = asgi_server

    # lifespan startup
    await asgi_server._lifespan_startup()
    if asgi_server._lifespan_startup_failed:
        raise RuntimeError("Server lifespan startup failed")

    # TCP startup
    if sockets:
        await asgi_server.startup(sockets=sockets)
    elif host is not None and port is not None:
        await asgi_server.startup(host=host, port=port)
    else:
        await asgi_server.startup(host=self.host, port=self.port)


@mutobj.impl(Server.stop)
async def _server_stop(self: Server) -> None:
    ext = cast(_ServerExt, _ServerExt.get_or_create(self))
    if ext._asgi_server is not None:
        await ext._asgi_server.shutdown()
        await ext._asgi_server._lifespan_shutdown()


# ---------------------------------------------------------------------------
# Lifespan 桥接 — 将 ASGI lifespan 协议转为 on_startup/on_shutdown 调用
# ---------------------------------------------------------------------------


async def _handle_lifespan(
    server: Server, scope: dict, receive: Any, send: Any,
) -> None:
    """桥接 ASGI lifespan 协议到 Server.on_startup/on_shutdown。"""
    started = False
    try:
        msg = await receive()
        if msg["type"] == "lifespan.startup":
            await server.on_startup()
            started = True
            await send({"type": "lifespan.startup.complete"})

        msg = await receive()
        if msg["type"] == "lifespan.shutdown":
            await server.on_shutdown()
            await send({"type": "lifespan.shutdown.complete"})
    except Exception:
        logger.exception("Lifespan error")
        if not started:
            await send({
                "type": "lifespan.startup.failed",
                "message": "Lifespan startup failed",
            })
        else:
            await send({"type": "lifespan.shutdown.complete"})
