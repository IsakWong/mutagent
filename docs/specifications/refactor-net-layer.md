# mutagent.net 网络层下沉 设计规范

**状态**：✅ 已完成
**日期**：2026-03-12
**类型**：重构

## 背景

mutbot 移除 FastAPI 后，自研了完整的 ASGI Server（h11 + wsproto）和基于 mutobj.Declaration 的 View/WebSocketView 路由框架。这些组件是通用基础设施，不含 mutbot 业务逻辑。

同时，MCP（Model Context Protocol）是 agent 接入工具生态的标准协议，agent 作为 MCP server 暴露能力、作为 MCP client 调用工具都是基本需求。目前 MCP 实现也在 mutbot.server 中，导致任何想暴露 MCP 的 mutagent 应用都必须依赖整个 mutbot。

### 动机

- **mutagent 定位**：为应用提供集成 AI agent 的能力。应用集成 agent 所需的公共基建（server、MCP、View 路由）应在 mutagent 层提供
- **避免重复基建**：每个基于 mutagent 的应用不应重复搭建 server、MCP、WebSocket 路由
- **依赖方向合理化**：通用基础设施不应锁在上层应用（mutbot）中

### 迁移来源

| 来源模块 | 行数 | 说明 |
|----------|------|------|
| `mutbot/server/_server.py` | 281 | ASGI Server + 生命周期管理 |
| `mutbot/server/_http.py` | 467 | h11 HTTP/1.1 协议层 |
| `mutbot/server/_ws.py` | 234 | wsproto WebSocket 协议层 |
| `mutbot/server/_sse.py` | 17 | SSE 辅助 |
| `mutbot/server/_mcp_server.py` | 584 | MCP Server |
| `mutbot/server/_mcp_client.py` | 232 | MCP Client |
| `mutbot/server/_jsonrpc.py` | 200 | JSON-RPC 协议层 |
| `mutbot/server/_mcp_types.py` | 134 | MCP 类型定义 |
| `mutbot/web/view.py` | 512 | View / WebSocketView / Router |
| 合计 | ~2661 | |

## 设计方案

### 模块结构

新增 `mutagent.net` 顶级 package，所有文件平铺。公开模块不带 `_` 前缀，实现文件带 `_` 前缀。

公开模块分两类：**基础设施**按角色分（client / server），**扩展框架**按 Declaration 域分（view / mcp）。

```
mutagent/net/
├── __init__.py            ~20   聚合导出
├── client.py              ~270  公开：HttpClient, MCPClient（出站连接）
├── server.py              ~220  公开：Server, MCPServer（入站服务）
├── view.py                ~512  公开+实现：View, WebSocketView, Router（Declaration 扩展）
├── mcp.py                 ~70   公开：MCPToolSet, MCPToolProvider（Declaration 扩展）
├── _asgi.py               ~280  实现：ASGI Server + 生命周期
├── _protocol.py           ~718  实现：h11 HTTP/1.1 + SSE + wsproto WebSocket
└── _mcp_proto.py          ~334  实现：JSON-RPC 协议 + MCP 类型定义
```

**8 个文件** | 4 公开 + 3 实现 + `__init__` | 总计 ~2404 行

**用户导入路径**：

```python
from mutagent.net.client import HttpClient, MCPClient
from mutagent.net.server import Server, MCPServer
from mutagent.net.view import View, WebSocketView, Router
from mutagent.net.mcp import MCPToolSet, MCPToolProvider
```

### 模块依赖

| 文件 | 依赖内部模块 | 依赖外部包 |
|------|-------------|-----------|
| `_protocol.py` | — | h11, wsproto |
| `_asgi.py` | `_protocol` | — |
| `_mcp_proto.py` | — | — |
| `server.py` | `_asgi`, `_mcp_proto`, `_protocol`(SSE) | — |
| `client.py` | `_mcp_proto` | httpx |
| `view.py` | — | mutobj |
| `mcp.py` | `_mcp_proto` | mutobj |

依赖方向干净，无循环。`view.py` 和 `mcp.py` 都依赖 mutobj（Declaration 扩展），但不依赖其他内部模块。

### 公开模块设计

**基础设施（按角色分）**：

- **`client.py`**：所有出站连接。HttpClient（httpx 封装，~30 行）+ MCPClient（~232 行）直接包含实现，无需拆分实现文件
- **`server.py`**：所有入站服务。聚合 Server（from `_asgi`）+ MCPServer 实现（去掉装饰器注册后 ~200 行，直接写在 server.py 中）

**扩展框架（按 Declaration 域分）**：

- **`view.py`**：View/WebSocketView 是 Declaration 子类，用户继承扩展。直接包含全部实现
- **`mcp.py`**：MCPToolSet 是 Declaration 子类，用户继承定义 MCP tool。MCPToolProvider 桥接 Declaration 发现到 MCP handler

### MCPServer 简化

原 `mutbot.server._mcp_server.py`（584 行）包含四部分：

1. **装饰器注册 API**（`@mcp.tool()` 等）— **删除**，与 mutobj 零注册理念冲突
2. **`handle_mcp` 纯函数**（~80 行）— **删除**，与装饰器模式平行的冗余用法，且无调用方
3. **MCPToolSet + MCPToolProvider** — **移入 `mcp.py`** 公开模块
4. **ASGI 端点 + JSON-RPC 分发 + `mount_mcp`** — **保留**，简化后 ~200 行，直接写在 `server.py` 中

MCPServer 不再自行维护 tool 注册表，改为使用 `mcp.py` 的 MCPToolProvider（Declaration 自动发现）。

### 依赖变更

mutagent `pyproject.toml` 新增依赖（直接依赖，不用 extras）：

```toml
dependencies = [
    ...,
    "h11>=0.14.0",
    "wsproto>=1.2.0",
]
```

h11 和 wsproto 都是纯 Python 小包，无 C 扩展依赖。

### mutbot 侧变更

mutbot 移除 `mutbot.server` 和 `mutbot.web.view`，改为从 mutagent 导入：

```python
# 之前
from mutbot.server import Server, MCPServer, MCPClient, mount_mcp
from mutbot.web.view import View, WebSocketView, Router

# 之后
from mutagent.net.server import Server, MCPServer, mount_mcp
from mutagent.net.client import HttpClient, MCPClient
from mutagent.net.view import View, WebSocketView, Router
from mutagent.net.mcp import MCPToolSet, MCPToolProvider
```

mutbot 保留的模块（业务逻辑层）：

| 模块 | 说明 |
|------|------|
| `mutbot.web.routes` | 具体路由（AppWebSocket、WorkspaceWebSocket 等） |
| `mutbot.web.rpc` | RPC 框架 + 分发器（应用层协议选择） |
| `mutbot.web.rpc_app` | App 级 RPC handler |
| `mutbot.web.rpc_workspace` | Workspace 级 RPC handler |
| `mutbot.web.rpc_session` | Session 级 RPC handler |
| `mutbot.web.transport` | Client / Channel 多路复用 |
| `mutbot.web.server` | 启动入口、lifespan、全局 manager |

### mutagent.http 迁移

现有 `mutagent.http` 模块（HttpClient，~30 行）移入 `mutagent.net.client`。旧路径 `mutagent/http.py` 直接删除，不保留兼容层。

### 设计决策

- **平铺不分子 package**：总量 ~2400 行，子 package 增加不必要的层级
- **公开模块两类**：基础设施按角色分（client/server 保持依赖隔离），扩展框架按 Declaration 域分（view/mcp）
- **MCP 按角色拆分**：MCPClient → client，MCPServer → server，MCPToolSet → mcp。避免单一 mcp 模块导致 `import MCPClient` 连带引入 server 依赖链
- **`_asgi.py` 而非 `_server.py`**：避免与公开模块 `server.py` 同名混淆
- **`_protocol.py` 合并 h11 + SSE + wsproto**：同层次传输协议，~718 行，内聚合理
- **`_mcp_proto.py` 合并 JSON-RPC + MCP 类型**：MCP 协议基础设施，~334 行
- **MCPServer 删除装饰器注册**：与 mutobj 零注册理念冲突，统一用 Declaration 自动发现
- **RPC 不下沉**：WebSocket RPC 没有标准协议，是应用层的协议选择
- **不用 extras**：h11 + wsproto 是轻量纯 Python 依赖，所有 mutagent 用户直接可用
- **不保留任何兼容层**：mutagent 用户少，mutbot 无下游依赖，直接改路径

### 实施概要

分三步：先在 mutagent 创建 `net` package 并迁入代码，然后修改 mutbot 的 import 指向新路径，最后清理 mutbot 中的旧模块。需要确保 mutagent 和 mutbot 的测试全部通过。

## 实施步骤清单

### 阶段一：mutagent 侧创建 net package [✅ 已完成]

- [x] **Task 1.1**: 创建 `mutagent/net/` 目录和 `__init__.py`
  - 状态：✅ 已完成

- [x] **Task 1.2**: 创建 `_protocol.py` — 合并 `_http.py` + `_sse.py` + `_ws.py`
  - 迁移 mutbot/server/_http.py、_sse.py、_ws.py，调整内部 import
  - 状态：✅ 已完成

- [x] **Task 1.3**: 创建 `_asgi.py` — 迁移 ASGI Server
  - 迁移 mutbot/server/_server.py，import 改为从 `_protocol` 导入
  - 状态：✅ 已完成

- [x] **Task 1.4**: 创建 `_mcp_proto.py` — 合并 JSON-RPC + MCP 类型
  - 迁移 mutbot/server/_jsonrpc.py + _mcp_types.py
  - 状态：✅ 已完成

- [x] **Task 1.5**: 创建 `view.py` — 迁移 View/WebSocketView/Router
  - 迁移 mutbot/web/view.py，调整 import（mutbot.server → mutagent.net 内部）
  - 状态：✅ 已完成

- [x] **Task 1.6**: 创建 `client.py` — HttpClient + MCPClient
  - 迁移 mutagent/http.py（HttpClient）+ mutbot/server/_mcp_client.py（MCPClient），合并到一个文件
  - 状态：✅ 已完成

- [x] **Task 1.7a**: 创建 `server.py` — Server + MCPServer 原样迁移
  - 聚合 Server（from _asgi）+ MCPServer 原样迁移（保留装饰器 API），跑通测试
  - 状态：✅ 已完成

- [x] **Task 1.7b**: MCPServer 简化重构
  - 删除装饰器注册 API，MCPServer 改用 MCPToolProvider（Declaration 自动发现）
  - 移除 resource/prompt 支持（后续需要时用 Declaration 模式添加）
  - 删除 server.py 中的 _infer_schema（mcp.py 已有）
  - 状态：✅ 已完成

- [x] **Task 1.8**: 创建 `mcp.py` — MCPToolSet + MCPToolProvider
  - 从 _mcp_server.py 拆出 MCPToolSet、MCPToolProvider、_infer_schema
  - 状态：✅ 已完成

- [x] **Task 1.9**: 更新 mutagent `pyproject.toml` 新增 h11 + wsproto 依赖
  - 状态：✅ 已完成

- [x] **Task 1.10**: 删除 `mutagent/http.py`（旧 HttpClient 路径）
  - 状态：✅ 已完成

- [x] **Task 1.11**: mutagent 测试通过
  - 运行 pytest，687 passed，2 failed（既有问题，非迁移引入）
  - 状态：✅ 已完成

### 阶段二：mutbot 侧 import 迁移 [✅ 已完成]

- [x] **Task 2.1**: 全量替换 mutbot 中所有旧 import 路径
  - 替换 `from mutbot.server import ...`、`from mutbot.web.view import ...`、`from mutagent.http import ...`
  - 含测试文件 test_mcp.py、test_server.py
  - 状态：✅ 已完成

- [x] **Task 2.2**: mutbot 测试通过
  - 467 passed
  - 状态：✅ 已完成

### 阶段三：清理 mutbot 旧模块 [✅ 已完成]

- [x] **Task 3.1**: 删除 `mutbot/server/` 整个目录
  - 状态：✅ 已完成

- [x] **Task 3.2**: 删除 `mutbot/web/view.py`
  - 状态：✅ 已完成

- [x] **Task 3.3**: 更新 mutbot `pyproject.toml` 移除 h11 + wsproto 依赖（已由 mutagent 传递）
  - 状态：✅ 已完成

- [x] **Task 3.4**: 双项目最终测试通过
  - mutagent: 687 passed, 2 failed（既有）; mutbot: 467 passed
  - 状态：✅ 已完成

## 关键参考

### 源码
- `mutbot/src/mutbot/server/` — 当前 ASGI Server + MCP 实现（2167 行）
- `mutbot/src/mutbot/web/view.py` — View/WebSocketView/Router（512 行）
- `mutagent/src/mutagent/http.py` — 现有 HttpClient
- `mutbot/docs/specifications/refactor-remove-fastapi.md` — FastAPI 移除重构规范

### 相关规范
- `mutobj/docs/specifications/bugfix-subclass-attribute-override.md` — Declaration 子类属性覆盖修复（迁移后 View 子类依赖此修复）
