"""传输协议层 — h11 HTTP/1.1 + SSE + wsproto WebSocket。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import unquote

import h11
import wsproto
import wsproto.events as ws_events

logger = logging.getLogger("mutagent.net.protocol")

# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------


def format_sse(data: str, event: str | None = None, id: str | None = None) -> bytes:
    """格式化单条 SSE 消息。"""
    lines: list[str] = []
    if id is not None:
        lines.append(f"id: {id}")
    if event is not None:
        lines.append(f"event: {event}")
    for line in data.split("\n"):
        lines.append(f"data: {line}")
    lines.append("")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


# ---------------------------------------------------------------------------
# Flow Control
# ---------------------------------------------------------------------------

# 64 KiB — 超过此大小暂停读取，等待 app 消费
HIGH_WATER_LIMIT = 65536


class FlowControl:
    """Transport 层读写背压控制。"""

    __slots__ = ("_transport", "_is_writable", "_read_paused")

    def __init__(self, transport: asyncio.Transport) -> None:
        self._transport = transport
        self._is_writable = asyncio.Event()
        self._is_writable.set()
        self._read_paused = False

    def pause_reading(self) -> None:
        if not self._read_paused:
            self._read_paused = True
            self._transport.pause_reading()

    def resume_reading(self) -> None:
        if self._read_paused:
            self._read_paused = False
            self._transport.resume_reading()

    def pause_writing(self) -> None:
        self._is_writable.clear()

    def resume_writing(self) -> None:
        self._is_writable.set()

    async def drain(self) -> None:
        await self._is_writable.wait()


# ---------------------------------------------------------------------------
# HTTP/1.1 Protocol (h11)
# ---------------------------------------------------------------------------


class HTTPProtocol(asyncio.Protocol):
    """每个 TCP 连接一个实例。解析 HTTP/1.1 请求，桥接到 ASGI app。"""

    def __init__(
        self,
        app: Any,
        *,
        server_state: dict[str, Any],
        root_path: str = "",
    ) -> None:
        self.app = app
        self.server_state = server_state
        self.root_path = root_path

        self.conn = h11.Connection(h11.SERVER)
        self.transport: asyncio.Transport = None  # type: ignore[assignment]
        self.flow: FlowControl = None  # type: ignore[assignment]

        self.client: tuple[str, int] | None = None
        self.server: tuple[str, int] | None = None

        self.cycle: RequestResponseCycle | None = None
        self.task: asyncio.Task[None] | None = None

        self._keep_alive = True
        self._timeout_handle: asyncio.TimerHandle | None = None

    # --- asyncio.Protocol callbacks ---

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        self.flow = FlowControl(self.transport)

        self.server_state["connections"].add(self)

        peername = transport.get_extra_info("peername")
        if peername:
            self.client = (str(peername[0]), int(peername[1]))
        sockname = transport.get_extra_info("sockname")
        if sockname:
            self.server = (str(sockname[0]), int(sockname[1]))

        self._schedule_timeout()

    def connection_lost(self, exc: Exception | None) -> None:
        self.server_state["connections"].discard(self)
        self._cancel_timeout()

        if self.cycle and not self.cycle.response_complete:
            self.cycle.disconnected = True
            self.cycle._body_event.set()

        if self.task and not self.task.done():
            self.task.cancel()

    def pause_writing(self) -> None:
        self.flow.pause_writing()

    def resume_writing(self) -> None:
        self.flow.resume_writing()

    def data_received(self, data: bytes) -> None:
        self._cancel_timeout()
        self.conn.receive_data(data)
        self._handle_events()

    def eof_received(self) -> bool | None:
        return False

    # --- HTTP event handling ---

    def _handle_events(self) -> None:
        while True:
            try:
                event = self.conn.next_event()
            except h11.RemoteProtocolError:
                self._send_error_response(400, "Bad Request")
                return

            if event is h11.NEED_DATA:
                break

            if event is h11.PAUSED:
                self.flow.pause_reading()
                break

            if isinstance(event, h11.Request):
                self._handle_request(event)

            elif isinstance(event, h11.Data):
                if self.cycle:
                    self.cycle._body += event.data
                    if len(self.cycle._body) > HIGH_WATER_LIMIT:
                        self.flow.pause_reading()
                    self.cycle._body_event.set()

            elif isinstance(event, h11.EndOfMessage):
                if self.cycle:
                    self.cycle._more_body = False
                    self.cycle._body_event.set()

            elif isinstance(event, h11.ConnectionClosed):
                break

    def _handle_request(self, event: h11.Request) -> None:
        method = event.method.decode("ascii")
        target = event.target.decode("ascii")
        http_version = event.http_version.decode("ascii")

        if "?" in target:
            raw_path, _, qs = target.partition("?")
        else:
            raw_path = target
            qs = ""

        path = unquote(raw_path)
        headers = [(k.lower(), v) for k, v in event.headers]

        upgrade = None
        for name, value in headers:
            if name == b"upgrade":
                upgrade = value.lower()
                break

        if upgrade == b"websocket":
            self._handle_ws_upgrade(event, path, raw_path, qs, headers, http_version)
            return

        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": http_version,
            "server": self.server,
            "client": self.client,
            "scheme": "http",
            "method": method,
            "root_path": self.root_path,
            "path": path,
            "raw_path": raw_path.encode("ascii"),
            "query_string": qs.encode("ascii"),
            "headers": headers,
        }

        self.cycle = RequestResponseCycle(
            scope=scope,
            conn=self.conn,
            transport=self.transport,
            flow=self.flow,
            keep_alive=self._keep_alive,
            on_response_complete=self._on_response_complete,
        )
        self.task = asyncio.get_running_loop().create_task(
            self.cycle.run(self.app)
        )

    def _handle_ws_upgrade(
        self,
        event: h11.Request,
        path: str,
        raw_path: str,
        query_string: str,
        headers: list[tuple[bytes, bytes]],
        http_version: str,
    ) -> None:
        """WebSocket upgrade — 切换到 WSProtocol。"""
        scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": http_version,
            "server": self.server,
            "client": self.client,
            "scheme": "ws",
            "root_path": self.root_path,
            "path": path,
            "raw_path": raw_path.encode("ascii"),
            "query_string": query_string.encode("ascii"),
            "headers": headers,
        }

        ws_protocol = WSProtocol(
            app=self.app,
            scope=scope,
            server_state=self.server_state,
        )

        raw_request = _reconstruct_raw_request(event)

        self.server_state["connections"].discard(self)
        self.transport.set_protocol(ws_protocol)
        ws_protocol.connection_made(self.transport)
        ws_protocol.data_received(raw_request)

    def _on_response_complete(self) -> None:
        self.conn.start_next_cycle()
        self.cycle = None
        self.task = None

        if self._keep_alive:
            self.flow.resume_reading()
            self._schedule_timeout()
            self._handle_events()
        else:
            self.transport.close()

    def _send_error_response(self, status_code: int, reason: str) -> None:
        body = f"{status_code} {reason}".encode()
        try:
            response = self.conn.send(h11.Response(
                status_code=status_code,
                headers=[(b"content-type", b"text/plain"),
                         (b"content-length", str(len(body)).encode()),
                         (b"connection", b"close")],
            ))
            data = self.conn.send(h11.Data(data=body))
            end = self.conn.send(h11.EndOfMessage())
            self.transport.write(response + data + end)
        except h11.LocalProtocolError:
            pass
        self.transport.close()

    def _schedule_timeout(self, seconds: float = 30.0) -> None:
        self._cancel_timeout()
        loop = asyncio.get_running_loop()
        self._timeout_handle = loop.call_later(seconds, self._on_timeout)

    def _cancel_timeout(self) -> None:
        if self._timeout_handle:
            self._timeout_handle.cancel()
            self._timeout_handle = None

    def _on_timeout(self) -> None:
        self.transport.close()

    def shutdown(self) -> None:
        """Graceful shutdown — 标记不再 keep-alive，等待当前请求完成。"""
        self._keep_alive = False
        if self.cycle is None or self.cycle.response_complete:
            self.transport.close()


class RequestResponseCycle:
    """单个 HTTP 请求/响应的 ASGI 桥接。"""

    __slots__ = (
        "scope", "conn", "transport", "flow", "keep_alive",
        "on_response_complete",
        "_body", "_body_event", "_more_body",
        "disconnected", "response_started", "response_complete",
        "_chunked", "_expected_content_length",
    )

    def __init__(
        self,
        scope: dict[str, Any],
        conn: h11.Connection,
        transport: asyncio.Transport,
        flow: FlowControl,
        keep_alive: bool,
        on_response_complete: Any,
    ) -> None:
        self.scope = scope
        self.conn = conn
        self.transport = transport
        self.flow = flow
        self.keep_alive = keep_alive
        self.on_response_complete = on_response_complete

        self._body = b""
        self._body_event = asyncio.Event()
        self._more_body = True
        self.disconnected = False
        self.response_started = False
        self.response_complete = False
        self._chunked = False
        self._expected_content_length: int | None = None

    async def run(self, app: Any) -> None:
        try:
            await app(self.scope, self.receive, self.send)
        except Exception:
            if not self.response_started:
                self._send_500()
            logger.exception("ASGI app raised exception for %s %s",
                             self.scope["method"], self.scope["path"])
        finally:
            if not self.response_complete:
                self.response_complete = True
                try:
                    self.on_response_complete()
                except Exception:
                    pass

    async def receive(self) -> dict[str, Any]:
        if self.disconnected:
            return {"type": "http.disconnect"}

        if not self._more_body:
            body = self._body
            self._body = b""
            return {"type": "http.request", "body": body, "more_body": False}

        await self._body_event.wait()
        self._body_event.clear()

        if self.disconnected:
            return {"type": "http.disconnect"}

        body = self._body
        self._body = b""
        self.flow.resume_reading()

        return {
            "type": "http.request",
            "body": body,
            "more_body": self._more_body,
        }

    async def send(self, message: dict[str, Any]) -> None:
        msg_type = message["type"]

        if msg_type == "http.response.start":
            await self._send_response_start(message)
        elif msg_type == "http.response.body":
            await self._send_response_body(message)

    async def _send_response_start(self, message: dict[str, Any]) -> None:
        self.response_started = True
        status_code: int = message["status"]
        headers: list[tuple[bytes, bytes]] = message.get("headers", [])

        has_content_length = False
        has_transfer_encoding = False
        for name, value in headers:
            low = name.lower()
            if low == b"content-length":
                has_content_length = True
                self._expected_content_length = int(value)
            elif low == b"transfer-encoding":
                has_transfer_encoding = True

        if not has_content_length and not has_transfer_encoding:
            self._chunked = True

        try:
            data = self.conn.send(h11.Response(
                status_code=status_code,
                headers=headers,
            ))
        except h11.LocalProtocolError as exc:
            logger.error("h11 protocol error sending response: %s", exc)
            self.transport.close()
            return

        await self.flow.drain()
        self.transport.write(data)

    async def _send_response_body(self, message: dict[str, Any]) -> None:
        body: bytes = message.get("body", b"")
        more_body: bool = message.get("more_body", False)

        if body:
            try:
                data = self.conn.send(h11.Data(data=body))
            except h11.LocalProtocolError:
                return
            await self.flow.drain()
            self.transport.write(data)

        if not more_body:
            try:
                data = self.conn.send(h11.EndOfMessage())
            except h11.LocalProtocolError:
                pass
            else:
                self.transport.write(data)

            self.response_complete = True
            self.on_response_complete()

    def _send_500(self) -> None:
        body = b"Internal Server Error"
        try:
            response = self.conn.send(h11.Response(
                status_code=500,
                headers=[(b"content-type", b"text/plain"),
                         (b"content-length", b"21"),
                         (b"connection", b"close")],
            ))
            data_bytes = self.conn.send(h11.Data(data=body))
            end = self.conn.send(h11.EndOfMessage())
            self.transport.write(response + data_bytes + end)
        except h11.LocalProtocolError:
            pass


def _reconstruct_raw_request(event: h11.Request) -> bytes:
    """从 h11.Request 重建原始 HTTP 请求（用于 WebSocket 协议交接）。"""
    lines: list[bytes] = []
    lines.append(event.method + b" " + event.target + b" HTTP/" + event.http_version)
    for name, value in event.headers:
        lines.append(name + b": " + value)
    lines.append(b"")
    lines.append(b"")
    return b"\r\n".join(lines)


# ---------------------------------------------------------------------------
# WebSocket Protocol (wsproto)
# ---------------------------------------------------------------------------

# WebSocket 消息队列上限 — 超过后暂停读取
MAX_QUEUE_SIZE = 16


class WSProtocol(asyncio.Protocol):
    """WebSocket 连接处理器。

    由 HTTPProtocol 在检测到 upgrade 后创建，通过 transport.set_protocol() 交接。
    """

    def __init__(
        self,
        app: Any,
        scope: dict[str, Any],
        *,
        server_state: dict[str, Any],
    ) -> None:
        self.app = app
        self.scope = scope
        self.server_state = server_state

        self.transport: asyncio.Transport = None  # type: ignore[assignment]
        self.ws: wsproto.WSConnection = wsproto.WSConnection(wsproto.ConnectionType.SERVER)
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.task: asyncio.Task[None] | None = None

        self._handshake_complete = False
        self._closed = False
        self._close_sent = False
        self._text_buffer: list[str] = []
        self._bytes_buffer: list[bytes] = []

    # --- asyncio.Protocol callbacks ---

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        self.server_state["connections"].add(self)

    def connection_lost(self, exc: Exception | None) -> None:
        self.server_state["connections"].discard(self)
        self._closed = True
        self.queue.put_nowait({"type": "websocket.disconnect", "code": 1006})

        if self.task and not self.task.done():
            self.task.cancel()

    def data_received(self, data: bytes) -> None:
        self.ws.receive_data(data)
        self._handle_events()

    def eof_received(self) -> bool | None:
        return False

    # --- wsproto event handling ---

    def _handle_events(self) -> None:
        for event in self.ws.events():
            if isinstance(event, ws_events.Request):
                self._handle_connect(event)

            elif isinstance(event, ws_events.TextMessage):
                self._handle_text(event)

            elif isinstance(event, ws_events.BytesMessage):
                self._handle_bytes(event)

            elif isinstance(event, ws_events.CloseConnection):
                self._handle_close(event)

            elif isinstance(event, ws_events.Ping):
                self.transport.write(self.ws.send(ws_events.Pong(payload=event.payload)))

    def _handle_connect(self, event: ws_events.Request) -> None:
        self.queue.put_nowait({"type": "websocket.connect"})
        loop = asyncio.get_running_loop()
        self.task = loop.create_task(self._run_asgi())

    def _handle_text(self, event: ws_events.TextMessage) -> None:
        self._text_buffer.append(event.data)
        if event.message_finished:
            text = "".join(self._text_buffer)
            self._text_buffer.clear()
            self._enqueue({"type": "websocket.receive", "text": text})

    def _handle_bytes(self, event: ws_events.BytesMessage) -> None:
        self._bytes_buffer.append(event.data)
        if event.message_finished:
            data = b"".join(self._bytes_buffer)
            self._bytes_buffer.clear()
            self._enqueue({"type": "websocket.receive", "bytes": data})

    def _handle_close(self, event: ws_events.CloseConnection) -> None:
        code = event.code or 1000
        if not self._close_sent:
            self._close_sent = True
            try:
                data = self.ws.send(ws_events.CloseConnection(code=code))
                self.transport.write(data)
            except Exception:
                pass
        self._closed = True
        self.queue.put_nowait({"type": "websocket.disconnect", "code": code})

    def _enqueue(self, message: dict[str, Any]) -> None:
        self.queue.put_nowait(message)
        if self.queue.qsize() >= MAX_QUEUE_SIZE:
            self.transport.pause_reading()

    # --- ASGI interface ---

    async def _run_asgi(self) -> None:
        try:
            await self.app(self.scope, self.receive, self.send)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("ASGI app raised exception for WebSocket %s",
                             self.scope["path"])
        finally:
            if not self._closed:
                self.transport.close()

    async def receive(self) -> dict[str, Any]:
        msg = await self.queue.get()
        if self.queue.qsize() < MAX_QUEUE_SIZE:
            try:
                self.transport.resume_reading()
            except Exception:
                pass
        return msg

    async def send(self, message: dict[str, Any]) -> None:
        msg_type = message["type"]

        if msg_type == "websocket.accept":
            await self._send_accept(message)

        elif msg_type == "websocket.send":
            await self._send_data(message)

        elif msg_type == "websocket.close":
            await self._send_close(message)

        elif msg_type == "websocket.http.response.start":
            await self._send_http_reject_start(message)

        elif msg_type == "websocket.http.response.body":
            await self._send_http_reject_body(message)

    async def _send_accept(self, message: dict[str, Any]) -> None:
        headers = message.get("headers", [])
        subprotocol = message.get("subprotocol")

        extra_headers = [(k, v) for k, v in headers]
        data = self.ws.send(ws_events.AcceptConnection(
            subprotocol=subprotocol,
            extra_headers=extra_headers,
        ))
        self.transport.write(data)
        self._handshake_complete = True

    async def _send_data(self, message: dict[str, Any]) -> None:
        if "text" in message:
            data = self.ws.send(ws_events.TextMessage(data=message["text"]))
        elif "bytes" in message:
            data = self.ws.send(ws_events.BytesMessage(data=message["bytes"]))
        else:
            return
        self.transport.write(data)

    async def _send_close(self, message: dict[str, Any]) -> None:
        code = message.get("code", 1000)
        reason = message.get("reason", "")
        if not self._close_sent:
            self._close_sent = True
            try:
                data = self.ws.send(ws_events.CloseConnection(code=code, reason=reason))
                self.transport.write(data)
            except Exception:
                pass
        self.transport.close()

    async def _send_http_reject_start(self, message: dict[str, Any]) -> None:
        status = message["status"]
        headers = message.get("headers", [])
        data = self.ws.send(ws_events.RejectConnection(
            status_code=status,
            headers=headers,
            has_body=True,
        ))
        self.transport.write(data)

    async def _send_http_reject_body(self, message: dict[str, Any]) -> None:
        body = message.get("body", b"")
        if body:
            data = self.ws.send(ws_events.RejectData(data=body))
            self.transport.write(data)
        if not message.get("more_body", False):
            self.transport.close()

    def shutdown(self) -> None:
        """Graceful shutdown — 发送 1012 close 帧。"""
        if self._handshake_complete and not self._close_sent:
            self._close_sent = True
            try:
                data = self.ws.send(ws_events.CloseConnection(
                    code=1012,
                    reason="Server shutting down",
                ))
                self.transport.write(data)
            except Exception:
                pass
        self.transport.close()
