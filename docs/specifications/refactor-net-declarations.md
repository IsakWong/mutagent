# mutagent.net Declaration 化重构 设计规范

**状态**：✅ 已完成
**日期**：2026-03-12
**类型**：重构

## 背景

net 层下沉（refactor-net-layer）已完成，8 个文件 ~2400 行代码从 mutbot 迁入 mutagent.net。当前 4 个公开文件中混杂了 Declaration 声明、普通类和实现代码。目标：公开文件只保留 Declaration 声明，实现全部移入 `_xxx_impl.py`，遵循 mutobj 声明-实现分离范式。

### 现状

| 公开文件 | Declaration | 普通类/实现 |
|---------|-------------|------------|
| `server.py` | 无 | `MCPServer`, `mount_mcp`, 辅助函数 |
| `view.py` | `View`, `WebSocketView` | `Request`, `Response`, `Router` 等 |
| `client.py` | `HttpClient` | `MCPClient`, `@impl` 实现 |
| `mcp.py` | `MCPToolSet` | `MCPToolProvider`, `_infer_schema` |

### 目标

公开文件只保留 Declaration 声明（桩方法 + 类属性），所有实现移入对应的 `_xxx_impl.py`。

## 设计方案

### 文件结构

```
mutagent/net/
├── __init__.py            聚合导出
├── server.py              Declaration: Server, View, WebSocketView, StaticView
├── client.py              Declaration: HttpClient, MCPClient
├── mcp.py                 Declaration: MCPToolSet, MCPView
├── _server_impl.py        @impl: Server/View/WebSocketView/StaticView + Request/Response/Router 等
├── _client_impl.py        @impl: HttpClient/MCPClient 实现
├── _mcp_impl.py           @impl: MCPToolSet 发现 + MCPView JSON-RPC 分发
├── _asgi.py               TCP 监听 + 事件循环（不动）
├── _protocol.py           h11 + wsproto（不动）
└── _mcp_proto.py          JSON-RPC + MCP 类型（不动）
```

### server.py — Declaration 声明

公开文件全部是 Declaration，零实现代码。协议类型（Request/Response 等）同样 Declaration 化，私有 ASGI 状态通过 Extension 附加在 impl 中。

```python
class Request(mutobj.Declaration):
    """HTTP 请求。"""
    method: str = "GET"
    path: str = "/"
    headers: dict[str, str] = mutobj.field(default_factory=dict)
    query_params: dict[str, str] = mutobj.field(default_factory=dict)
    path_params: dict[str, str] = mutobj.field(default_factory=dict)

    async def body(self) -> bytes: ...
    async def json(self) -> Any: ...

class Response(mutobj.Declaration):
    """HTTP 响应。"""
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = mutobj.field(default_factory=dict)

class StreamingResponse(mutobj.Declaration):
    """流式 HTTP 响应。"""
    status: int = 200
    headers: dict[str, str] = mutobj.field(default_factory=dict)

class WebSocketConnection(mutobj.Declaration):
    """WebSocket 连接。"""
    path: str = "/"
    query_params: dict[str, str] = mutobj.field(default_factory=dict)
    path_params: dict[str, str] = mutobj.field(default_factory=dict)

    async def accept(self) -> None: ...
    async def receive_json(self) -> Any: ...
    async def send_json(self, data: Any) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...

class WebSocketDisconnect(Exception):
    """WebSocket 正常断开异常。"""

class Server(mutobj.Declaration):
    """ASGI Server。"""
    host: str = "127.0.0.1"
    port: int = 0

    async def route(self, scope: dict, receive: Any, send: Any) -> None: ...

    async def on_startup(self) -> None: ...
    async def on_shutdown(self) -> None: ...

    def run(self, *, listen: Sequence[str | socket] | None = None) -> None: ...
    async def start(self, *, listen: Sequence[str | socket] | None = None) -> None: ...
    async def stop(self) -> None: ...

class View(mutobj.Declaration):
    """HTTP 路由。一个 path 一个类，方法名 = HTTP method。"""
    path: str = ""

    async def get(self, request: Request) -> Response | StreamingResponse: ...
    async def post(self, request: Request) -> Response | StreamingResponse: ...
    async def put(self, request: Request) -> Response | StreamingResponse: ...
    async def delete(self, request: Request) -> Response | StreamingResponse: ...

class WebSocketView(mutobj.Declaration):
    """WebSocket 路由。"""
    path: str = ""

    async def connect(self, ws: WebSocketConnection) -> None: ...

class StaticView(View):
    """静态文件服务。"""
    directory: str = ""
```

**协议类型的 Declaration 化**：

Request、Response、StreamingResponse、WebSocketConnection 都是 Declaration。公开字段直接声明为类属性，私有 ASGI 状态（`_receive` 回调、`_send` 回调等）通过 Extension 在 impl 中附加：

```python
# _server_impl.py 中
class _RequestExt(mutobj.Extension[Request]):
    """Request 的 ASGI 内部状态。"""
    _receive: Any = None
    _body: bytes | None = None

@mutobj.impl(Request.body)
async def _body(self: Request) -> bytes:
    ext = _RequestExt.get_or_create(self)
    if ext._body is not None:
        return ext._body
    chunks = []
    while True:
        msg = await ext._receive()
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body", False):
            break
    ext._body = b"".join(chunks)
    return ext._body
```

route impl 构造 Request 时，从 ASGI scope 解析公开字段（kwargs 传入），通过 Extension 附加 `_receive` 回调。用户只看到干净的数据属性和方法签名。

Declaration 现已支持位置参数初始化（`mutobj/docs/specifications/feature-positional-init.md`），不写 `__init__` 的 Declaration 自动按字段声明顺序接受位置参数。构造示例：

```python
# Response 不写 __init__，自动支持位置参数
Response(404, b"hello")                    # ✅ 位置参数
Response(status=404, body=b"hello")        # ✅ 关键字
Response(404, body=b"hello")               # ✅ 混合

# Request 在 _server_impl.py 中通过 kwargs 构造
request = Request(
    method=scope.get("method", "GET"),
    path=scope.get("path", "/"),
    headers=parsed_headers,
    query_params=parsed_qs,
    path_params=params,
)
# 然后通过 Extension 附加私有 ASGI 状态
_RequestExt.get_or_create(request)._receive = receive
```

效率评估：Declaration 属性访问多一层 `AttributeDescriptor`，Extension 创建是微秒级开销，在 HTTP 请求粒度上可忽略。mutobj 架构已预留优化路径（`__slots__`、紧凑存储），未来可在不改用户代码的前提下提速。

**Server 核心设计**：

- `route` — ASGI 入口，默认 impl 自动发现 View/WebSocketView/StaticView 并路径匹配分发。吸收了当前 `Router` 的全部职责，Router 类消亡。内部处理 ASGI lifespan 协议，转发到 `on_startup`/`on_shutdown`
- `on_startup`/`on_shutdown` — 生命周期钩子，隐藏 ASGI lifespan 协议细节。子类覆盖即可（mutbot 在此初始化 managers、加载持久化状态、注册回调等）
- `run` — 阻塞启动，自建 event loop。无参数时用 `host`/`port` 绑定；`listen` 接受字符串（`"ip:port"`）或预创建 socket 的数组，支持多 IP 绑定
- `start`/`stop` — 异步启动/停止，在已有 event loop 中使用
- View 方法默认返回 405（在 impl 中提供）
- StaticView 继承 View，impl 提供文件查找 + MIME 类型 + 缓存头逻辑

### client.py — Declaration 声明

```python
class HttpClient(mutobj.Declaration):
    """HTTP 客户端工厂。"""

    @staticmethod
    def create(**kwargs: Any) -> httpx.AsyncClient: ...

class MCPClient(mutobj.Declaration):
    """MCP 客户端。"""
    url: str = ""
    client_name: str = "mutagent"
    client_version: str = "0.1.0"
    timeout: float = 30.0

    async def connect(self) -> None: ...
    async def close(self) -> None: ...

    async def list_tools(self) -> list[dict[str, Any]]: ...
    async def call_tool(self, name: str, **arguments: Any) -> dict[str, Any]: ...
    async def list_resources(self) -> list[dict[str, Any]]: ...
    async def read_resource(self, uri: str) -> dict[str, Any]: ...
    async def list_prompts(self) -> list[dict[str, Any]]: ...
    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]: ...
    async def ping(self) -> None: ...

    @property
    def server_info(self) -> dict[str, Any]: ...
    @property
    def server_capabilities(self) -> dict[str, Any]: ...
```

**MCPClient 设计决策**：

- 去掉 `__aenter__`/`__aexit__`，用显式 `connect()`/`close()`（不是设计必须，保持 Declaration 简洁）
- 当前基于 httpx + SSE 的实现整体搬入 `_client_impl.py`
- `MCPError` 异常类保留在 `client.py`（公开 API 的一部分，非 Declaration 但是接口契约）

### mcp.py — Declaration 声明

```python
class MCPToolSet(mutobj.Declaration):
    """MCP tool 集合。方法名 = tool name。"""
    prefix: str = ""
    view: type[MCPView] | tuple[type[MCPView], ...] | None = None
    path: str | tuple[str, ...] = ""

class MCPView(View):
    """MCP Streamable HTTP 端点。"""
    path: str = "/mcp"
    name: str = "mutagent"
    version: str = "0.1.0"
    instructions: str | None = None

    async def post(self, request: Request) -> Response | StreamingResponse: ...
    async def delete(self, request: Request) -> Response: ...
```

**MCPToolSet → MCPView 归属关系**：

MCPToolSet 声明自己归属于哪个 MCPView，支持两种方式：

1. **类引用**：`view = MyMCPView` 或 `view = (ViewA, ViewB)` — 类型安全，需 import
2. **路径匹配**：`path = "/mcp"` 或 `path = ("/mcp", "/agent/mcp")` — 松耦合，跨模块无需 import

解析优先级：`view` > `path`。都没指定则报错（必须声明归属）。目标 MCPView 不存在时报错。

示例：

```python
# 方式一：类引用
class IntrospectTools(MCPToolSet):
    view = IntrospectView

# 方式二：路径匹配
class AgentTools(MCPToolSet):
    path = "/agent/mcp"

# 归属多个 View
class CommonTools(MCPToolSet):
    view = (IntrospectView, AgentView)
```

**MCPView 设计决策**：

- 继承 View，是个特殊的 HTTP 端点，被 Server.route 统一发现和分发
- 替代当前的 `MCPServer` + `mount_mcp` 方案
- impl 中包含 JSON-RPC 分发、session 管理、MCPToolProvider 逻辑

### 实现文件职责

**`_server_impl.py`**：
- `_RequestExt(Extension[Request])`：承载 `_receive`、`_body` 等 ASGI 私有状态
- `_WebSocketExt(Extension[WebSocketConnection])`：承载 `_receive`、`_send` ASGI 回调
- `_StreamingResponseExt(Extension[StreamingResponse])`：承载 `body_iterator`
- `Request.body`/`Request.json` 的 `@impl`
- `WebSocketConnection` 全部方法的 `@impl`
- `Response`/`StreamingResponse` 的 ASGI send 逻辑（内部辅助函数，非 `@impl`）
- `Server.route` 的 `@impl`：路径匹配 + View/WebSocketView/StaticView 自动发现（当前 Router 逻辑）
- `Server.run`/`start`/`stop` 的 `@impl`：解析 `listen` 参数，委托 `_asgi` 做 TCP 监听
- `Server.on_startup`/`on_shutdown` 的默认 `@impl`：空操作
- `View.get/post/put/delete` 的默认 `@impl`：返回 405
- `WebSocketView.connect` 的默认 `@impl`：关闭连接
- `StaticView` 的 `@impl`：文件查找 + MIME + 缓存头
- 辅助函数：`_compile_path`、`_make_request`、`_make_ws_connection` 等

**`_client_impl.py`**：
- `HttpClient.create` 的 `@impl`
- `MCPClient` 全部方法的 `@impl`：httpx + SSE + JSON-RPC 握手

**`_mcp_impl.py`**：
- `MCPView.post` 的 `@impl`：JSON-RPC 分发（当前 MCPServer 逻辑）
- `MCPView.delete` 的 `@impl`：session 清理
- MCPToolProvider：generation 感知的 tool 发现 + schema 推断
- MCPToolSet → MCPView 归属解析逻辑

### 删除的内容

| 删除 | 替代 |
|------|------|
| `MCPServer` 类 | `MCPView(View)` |
| `mount_mcp()` 函数 | Server.route 自动发现 MCPView |
| `Router` 类 | Server.route 的 impl |
| `_send_json_response` 等辅助函数 | 复用 `Response`/`StreamingResponse` |
| `server.py` 中的 `_MCPSession` | 移入 `_mcp_impl.py` 内部 |

### mutbot 侧影响

mutbot 需要适配的 import 变更：

```python
# 之前
from mutagent.net.server import Server, MCPServer, mount_mcp
from mutagent.net.view import View, WebSocketView, Router

# 之后
from mutagent.net.server import Server, View, WebSocketView, StaticView
from mutagent.net.mcp import MCPView, MCPToolSet
```

mutbot 当前使用 `Router` 的地方（`mutbot/web/server.py`）改为使用 `Server`：

```python
# 之前
router = Router()
router.set_lifespan(lifespan)
router.discover()
router.add_static("/", frontend_dist)
server = Server(router)
server.run(sockets=sockets, on_startup=on_startup)

# 之后
server = Server()
server.run(listen=addresses)
```

- lifespan → 通过 View 子类或 Server impl 扩展处理
- `add_static` → 声明 `StaticView` 子类
- `discover` → Server.route impl 自动执行
- `sockets` → `listen` 参数接受字符串或 socket 数组

### 实施概要

分三个阶段：先重构 server.py（改动最大），再重构 client.py 和 mcp.py，最后更新 mutbot 并清理旧代码。每步确保测试通过。

## 实施步骤清单

### 阶段一：server.py Declaration 化

- [ ] **Task 1.1**: 重写 `server.py` — 只保留 Declaration 声明
  - Request、Response、StreamingResponse、WebSocketConnection、WebSocketDisconnect
  - Server、View、WebSocketView、StaticView
  - 删除所有实现代码，view.py 消亡（内容合并到 server.py）

- [ ] **Task 1.2**: 创建 `_server_impl.py` — 全部 @impl 实现
  - Extension：_RequestExt、_WebSocketExt、_StreamingResponseExt
  - Request.body/json 的 @impl
  - WebSocketConnection 全部方法的 @impl
  - Response/StreamingResponse 的 ASGI send 辅助函数
  - Server.route 的 @impl（吸收 Router 逻辑：路径匹配、View 自动发现、静态文件 fallback、lifespan 处理）
  - Server.run/start/stop 的 @impl（委托 _asgi）
  - Server.on_startup/on_shutdown 的默认 @impl（空操作）
  - View.get/post/put/delete 的默认 @impl（返回 405）
  - WebSocketView.connect 的默认 @impl（关闭连接）
  - StaticView 的 @impl（文件查找 + MIME + 缓存头）
  - 辅助函数：_compile_path、json_response、html_response 等

- [ ] **Task 1.3**: 更新 `__init__.py` 导出
  - 从 server.py 导出 Declaration 类型
  - 删除 view.py 相关导出

- [ ] **Task 1.4**: mutagent 测试通过

### 阶段二：client.py + mcp.py Declaration 化

- [ ] **Task 2.1**: 重写 `client.py` — 只保留 Declaration 声明
  - HttpClient、MCPClient 的 Declaration 声明
  - MCPError 异常类保留（公开接口契约）

- [ ] **Task 2.2**: 创建 `_client_impl.py` — @impl 实现
  - HttpClient.create 的 @impl
  - MCPClient 全部方法的 @impl（httpx + SSE + JSON-RPC）
  - MCPClient 的 Extension（承载 httpx client、session 状态等）

- [ ] **Task 2.3**: 重写 `mcp.py` — 只保留 Declaration 声明
  - MCPToolSet、MCPView 的 Declaration 声明
  - MCPToolProvider 移入 _mcp_impl.py

- [ ] **Task 2.4**: 创建 `_mcp_impl.py` — @impl 实现
  - MCPView.post 的 @impl（JSON-RPC 分发）
  - MCPView.delete 的 @impl（session 清理）
  - MCPToolProvider + _infer_schema（工具发现逻辑）
  - MCPToolSet → MCPView 归属解析

- [ ] **Task 2.5**: 更新 `__init__.py` 导出

- [ ] **Task 2.6**: mutagent 测试通过

### 阶段三：mutbot 适配 + 清理 [✅ 已完成]

- [x] **Task 3.1**: 更新 mutbot import 路径
  - `from mutagent.net.view import ...` → `from mutagent.net.server import ...`
  - `from mutagent.net.server import MCPServer, mount_mcp` → `from mutagent.net.mcp import MCPView`
  - Router 使用方改为 Server
  - 状态：✅ 已完成

- [x] **Task 3.2**: mutbot 启动入口适配
  - `mutbot/web/server.py` 中 Router → Server
  - lifespan → on_startup/on_shutdown
  - add_static → StaticView 子类
  - sockets → listen 参数
  - 状态：✅ 已完成

- [x] **Task 3.3**: 更新 mutbot 测试 + 删除旧文件
  - test_mcp.py 适配 MCPView Declaration（MCPServer/mount_mcp → _TestMCPView 子类）
  - MCPClient 使用 connect()/close() 替代 async with
  - 删除 `mutagent/net/view.py`（已合并到 server.py）
  - 状态：✅ 已完成

- [x] **Task 3.4**: 双项目测试通过
  - mutagent: 689 passed, 5 skipped
  - mutbot: 465 passed
  - 状态：✅ 已完成

## 关键参考

### 源码
- `mutagent/src/mutagent/net/server.py` — 当前 MCPServer + mount_mcp（265 行）
- `mutagent/src/mutagent/net/view.py` — 当前 View/WebSocketView/Router（501 行）
- `mutagent/src/mutagent/net/client.py` — 当前 HttpClient + MCPClient（260 行）
- `mutagent/src/mutagent/net/mcp.py` — 当前 MCPToolSet + MCPToolProvider（103 行）
- `mutagent/src/mutagent/net/_asgi.py` — ASGI Server 实现（267 行，不动）
- `mutagent/src/mutagent/net/_protocol.py` — h11 + wsproto（不动）
- `mutagent/src/mutagent/net/_mcp_proto.py` — JSON-RPC + MCP 类型（不动）
- `mutbot/src/mutbot/web/server.py:434-478` — mutbot 启动入口，使用 Router/Server/sockets

### 相关规范
- `mutagent/docs/specifications/refactor-net-layer.md` — net 层下沉重构（✅ 已完成，本次重构的前置）
- `mutobj/docs/design/architecture.md` — mutobj 架构设计理念（Declaration/Extension/渐进优化路径）
- `mutobj/docs/api/reference.md` — mutobj API 参考（Declaration `__init__(*args, **kwargs)`、Extension、field()）
- `mutobj/docs/specifications/feature-positional-init.md` — Declaration 位置参数初始化（✅ 已实现，本次重构依赖）
