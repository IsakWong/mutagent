# ADR: mutagent 全异步架构

**日期**：2026-02-26
**状态**：已采纳

## 背景

mutagent 在 2026-02-16 移除了 asyncio，回归纯同步架构。当时理由充分：agent 循环串行，无并发需求，`input()` 包装成异步多此一举。

但 mutbot Web 层引入后，sync/async 边界产生了架构张力：
- `AgentBridge` 需要 daemon thread + `call_soon_threadsafe` + `queue.Queue` + Future 包装
- 同步 `requests.post()` 在线程中不可取消，`stop()` 只能等超时后放弃
- shutdown 需要三层防御（lifespan cleanup + 双 Ctrl+C + watchdog）
- 跨线程通信存在 loop 关闭竞态、Future 状态竞争等边界问题

## 决策

**mutagent 内部全异步，CLI 的 `input()` 不在 asyncio 世界中。**

### 关键设计决策

1. **只提供 async 接口**：`run()`、`step()`、`send_message()`、`send()`、`dispatch()` 全部为 `async def`，不保留 sync 版本。CLI 适配层展示了如何在 sync 上下文中使用。

2. **ToolSet.dispatch 透明处理同步工具**：`dispatch()` 内部自动检测 async callable 直接 `await`，sync callable 用 `asyncio.to_thread()` 包装。工具作者无需关心 async/sync。

3. **CLI 使用独立线程 event loop**：主线程保持 `input()` 同步调用，asyncio event loop 在 daemon 线程中 `run_forever()`，通过 `queue.Queue` 跨线程通信。

4. **方法命名保持不变**：只有 async 一个版本，不加 `a` 前缀（`run()` 而非 `arun()`）。

5. **HTTP 客户端**：`requests` → `httpx`（async），同时支持 sync/async 接口，API 兼容性好。

## 影响

### mutagent 侧
- `agent.py`、`client.py`、`provider.py`、`tools.py`：声明改为 async
- `builtins/agent_impl.py`、`client_impl.py`、`tool_set_impl.py`：实现改为 async
- `builtins/anthropic_provider.py`、`openai_provider.py`：`requests` → `httpx.AsyncClient`
- `builtins/main_impl.py`：CLI 适配层（独立线程 event loop + queue 通信）
- `pyproject.toml`：`requests` → `httpx`，添加 `pytest-asyncio`

### mutbot 侧
- `AgentBridge` 大幅简化：移除 daemon thread / `call_soon_threadsafe` / `threading.Event` / `queue.Queue`，agent 作为 asyncio task 在同一事件循环运行
- `WebUserIO` 完全移除
- `ConnectionManager.broadcast()` 改为遍历 set 快照（`list(conns)`），避免同一事件循环中 await 挂起导致集合变更

## 验证

- mutagent 单元测试：705 passed, 4 skipped
- mutbot 单元测试：250 passed
- CLI 手动验证：input → agent → event 流程、Ctrl+C/Ctrl+D/Ctrl+Z 退出、exit 命令退出
