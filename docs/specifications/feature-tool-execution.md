# 工具执行增强设计规范

**状态**：📝 设计中
**日期**：2026-03-01
**类型**：功能设计

## 背景

当前工具执行框架存在两个正交的问题：

**用户视角 — 执行期间无反馈**：工具执行是"黑盒"模式，`tool_exec_start` → （沉默等待）→ `tool_exec_end`。耗时较长的工具（网页抓取、代码执行等）期间用户看不到任何进度。

**LLM 视角 — 结果全量占用 context**：工具返回大量文本（50KB 网页、完整代码文件等），全量放入对话历史。LLM 可能只需其中一小段，却浪费大量 context。如果需要查看细节，只能重新执行工具。

### 两个子问题的关系

| 子问题 | 视角 | 核心矛盾 | 解法 |
|--------|------|----------|------|
| 流式输出 | 用户 | 执行太久，无进度反馈 | `tool_output_delta` 事件 + `_emit` 回调 |
| 渐进式查询 | LLM | 结果太长，context 浪费 | 框架截断 + `Result-query` 内置工具 |

两者正交，共享工具执行基础设施（dispatch、ToolUseBlock、StreamEvent），合并设计避免重复改动。

---

## 设计方案一：流式输出

### 现有机制的局限

**日志捕获**（`_tool_log_buffer` ContextVar）：能收集工具执行期间的 logging 输出，但是**批量追加**到 `block.result`，不是实时流式的。并且日志内容是追加给 LLM 看的，不是面向用户的展示。

**StreamEvent 缺少中间类型**：LLM 侧有 `text_delta`（流式文本）、`tool_use_delta`（流式 JSON），但工具执行侧没有对应的中间事件。

### 新增 StreamEvent 类型

```python
@dataclass
class StreamEvent:
    # ... 现有字段 ...
    # 新增
    tool_output: str = ""           # 工具中间输出片段
```

新增事件类型 `tool_output_delta`：

| 事件类型 | 含义 | 字段 |
|----------|------|------|
| `tool_output_delta` | 工具执行中产生的中间输出 | `tool_call` = 当前 ToolUseBlock, `tool_output` = 输出片段 |

### 工具端：`_emit` 回调注入

工具函数通过可选的 `_emit` 回调参数发送中间输出。框架在调用工具时检测并注入：

```python
# 工具声明（Toolkit 方法）
class MyToolkit(mutagent.Toolkit):
    async def execute(self, code: str, *, _emit=None) -> str:
        """执行代码。"""
        for line in run_code(code):
            if _emit:
                _emit(line)           # 发送中间输出
        return final_result

# 普通工具（无 _emit）— 行为不变
class SimpleToolkit(mutagent.Toolkit):
    def search(self, query: str) -> str:
        return do_search(query)
```

**设计选择**：

- `_emit` 是**可选**的——不接受 `_emit` 参数的工具完全不受影响
- 下划线前缀表示框架注入参数，不出现在 tool schema 中
- `_emit` 是同步回调（不是 async），降低工具实现复杂度
- schema 生成时自动过滤 `_` 前缀参数

### 框架端：实时转发方案

**核心难点**：`run()` 是 `AsyncIterator[StreamEvent]`，回调发生在 `await dispatch()` 内部，yield 点在外部。需要一种机制将 dispatch 内部的回调转为外部的 yield。

#### 方案对比

| 方案 | 实时性 | 复杂度 | 改动范围 |
|------|--------|--------|----------|
| A) 回调 + 缓冲 | 否（dispatch 后才发出） | 低 | dispatch 签名 |
| B) AsyncIterator dispatch | 是 | 高（工具实现复杂） | dispatch 返回类型 |
| C) asyncio.Queue 桥接 | 是 | 高（Task + 异常管理） | agent_impl |
| D) ContextVar + 轮询 | 是（~100ms 延迟） | 中 | agent_impl |

#### 推荐方案 D：ContextVar + 轮询

复用现有的 ContextVar 模式（和日志捕获一致），但改为实时轮询消费：

```python
# 新增 ContextVar
_tool_output_buffer: ContextVar[list[str] | None] = ContextVar(...)

# dispatch 注入 _emit 回调，写入 ContextVar buffer
def emit_callback(text):
    buf = _tool_output_buffer.get(None)
    if buf is not None:
        buf.append(text)

# agent_impl.py — 并发运行 dispatch + 定时消费 buffer
buf: list[str] = []
token = _tool_output_buffer.set(buf)

dispatch_task = asyncio.create_task(self.tools.dispatch(block))

while not dispatch_task.done():
    await asyncio.sleep(0.1)  # 100ms 轮询间隔
    if buf:
        for text in buf:
            yield StreamEvent(type="tool_output_delta", tool_call=block, tool_output=text)
        buf.clear()

# 排空
_tool_output_buffer.reset(token)
if buf:
    for text in buf:
        yield StreamEvent(type="tool_output_delta", tool_call=block, tool_output=text)

await dispatch_task  # 传播异常
yield StreamEvent(type="tool_exec_end", tool_call=block)
```

**优点**：复用 ContextVar 模式、真正实时（100ms 可接受）、不改变 dispatch 返回类型。
**缺点**：轮询有微小延迟、需要 `asyncio.create_task` 管理。

### UserIO 渲染

```
  search(query="python")
    Connecting...              ← tool_output_delta
    Fetching results...        ← tool_output_delta
  → Found 5 results (ok)      ← tool_exec_end
```

缩进 + dim 样式，与 tool_exec_start/end 的渲染风格一致。

---

## 设计方案二：渐进式查询

### 核心思路

框架在工具结果超过阈值时主动截断，同时提供"查询工具结果"的内置能力。LLM 不需要重新执行工具，而是对已有结果进行渐进式深入查看。

```
第一次：Web-fetch(url) → 结果 50KB → 框架截断为 2KB 摘要 + "[截断，使用 Result-query 查看更多]"
第二次：Result-query(tool_call_id="t1", keyword="authentication") → 从完整结果中提取相关段落
第三次：Result-query(tool_call_id="t1", offset=100, limit=50) → 从完整结果中取指定行范围
```

工具本身不感知截断——它正常返回完整结果，框架在 ToolUseBlock 层面处理存储和截断。

### 结果存储

ToolUseBlock 上区分"完整结果"和"发送给 LLM 的结果"：

```python
@dataclass
class ToolUseBlock(ContentBlock):
    # ... 现有字段 ...
    result: str = ""              # 发送给 LLM 的结果（可能被截断）
    _full_result: str = ""        # 完整结果（框架内部使用）
```

- `dispatch()` 执行后将完整结果同时写入 `result` 和 `_full_result`
- Provider 发送前（或 `prepare_messages()` 中），检查 `_full_result` 长度
- 超过阈值：`result` 替换为截断版 + 查询提示
- 未超过阈值：`result` 保持不变

**为什么不在 dispatch 时截断**：工具执行后、LLM 调用前有一个窗口期，消费者（如 UI）可能需要展示完整结果。截断是 LLM 视角的行为，不应影响 UI 渲染。

### 截断策略

```python
TRUNCATE_THRESHOLD = 4000    # 字符数阈值（约 1000 tokens）
TRUNCATE_PREVIEW = 2000      # 截断后保留的预览长度

def truncate_result(result: str, tool_call_id: str) -> str:
    """截断过长的工具结果，附加查询提示。"""
    if len(result) <= TRUNCATE_THRESHOLD:
        return result

    preview = result[:TRUNCATE_PREVIEW]
    total_lines = result.count('\n') + 1
    return (
        f"{preview}\n\n"
        f"[结果已截断：共 {len(result)} 字符 / {total_lines} 行。"
        f"使用 Result-query(tool_call_id=\"{tool_call_id}\") 查看更多内容。]"
    )
```

### Result-query 内置工具

框架自动注册一个内置工具，不需要用户配置：

```python
class ResultQuery(mutagent.Toolkit):
    """查询已执行工具的完整结果。"""

    def query(
        self,
        tool_call_id: str,
        *,
        keyword: str = "",
        offset: int = 0,
        limit: int = 100,
    ) -> str:
        """查询工具调用的完整结果。

        Args:
            tool_call_id: 要查询的工具调用 ID（从截断提示中获取）
            keyword: 关键词搜索，返回包含该关键词的上下文行
            offset: 起始行号（0-based）
            limit: 返回行数上限
        """
        ...
```

**查询模式**：
- `keyword` 非空：从完整结果中搜索包含关键词的行，返回匹配行及其上下文（前后各 3 行）
- `keyword` 为空：返回从 `offset` 开始的 `limit` 行（分页浏览）

### 结果定位

Result-query 需要从对话历史中找到目标 ToolUseBlock：

```python
def _find_tool_block(context: AgentContext, tool_call_id: str) -> ToolUseBlock | None:
    """在对话历史中按 tool_call_id 查找 ToolUseBlock。"""
    for msg in reversed(context.messages):
        for block in msg.blocks:
            if isinstance(block, ToolUseBlock) and block.id == tool_call_id:
                return block
    return None
```

Result-query 需要访问 AgentContext。通过 `ToolSet.agent` 反向引用（已有机制）或注入。

### Provider 集成点

截断发生在 Provider 转换 ToolUseBlock → tool_result 消息时：

```python
# anthropic_provider.py / openai_provider.py
def _tool_result_content(block: ToolUseBlock) -> str:
    full = getattr(block, '_full_result', '') or block.result
    if full and len(full) > TRUNCATE_THRESHOLD:
        return truncate_result(full, block.id)
    return block.result
```

### 工具注册与生命周期

- Result-query 在 Agent 有工具时自动注册到 ToolSet
- 无工具（`tools.get_tools()` 为空）时不注册——纯对话不需要
- 可通过配置禁用

---

## 待定问题

### 流式输出

#### QUEST Q1: 流式输出方案选择

**问题**：四个方案各有取舍，选择哪个？

**选项**：
- A) 回调 + 事件缓冲 — 最简单但非实时
- B) AsyncIterator dispatch — 最彻底但改动大且工具实现复杂
- C) asyncio.Queue 桥接 — 实时但复杂
- D) ContextVar + 轮询 — 实时且复杂度可控（推荐）

**建议**：方案 D。和现有日志捕获机制风格一致，实时性足够，不改变 dispatch 接口签名。

#### QUEST Q2: _emit 回调是否支持结构化输出

**问题**：`_emit(text: str)` 只支持纯文本。是否需要支持结构化数据（进度百分比、状态标签等）？

**建议**：先纯文本。如需结构化，可以用 JSON 字符串，消费者按需解析。不引入新类型。

#### QUEST Q3: 中间输出是否追加到最终 result

**问题**：中间输出（如 "Compiling..." "Running tests..."）是否应该追加到 `block.result`（从而被 LLM 看到）？

**建议**：不追加。中间输出是面向用户的进度信息，对 LLM 没有价值。`block.result` 只保留工具的最终返回值。和日志捕获语义不同——日志是有信息量的执行细节，中间输出是进度提示。

#### QUEST Q4: 是否与日志捕获合并

**问题**：当前已有 `_tool_log_buffer` 捕获 logging 输出。新增 `_tool_output_buffer` 捕获 `_emit` 输出。两套缓冲是否合并？

**建议**：不合并。两者语义不同：
- 日志捕获 → logging 输出 → 追加到 result → LLM 可见
- 工具输出 → _emit 回调 → 实时流式展示 → 仅用户可见

### 渐进式查询

#### QUEST Q5: 截断阈值是全局还是按工具配置

**问题**：不同工具的结果特征差异大（搜索结果 vs 文件内容 vs 代码）。是否需要按工具名配置不同阈值？

**建议**：先全局阈值，后续按需添加按工具配置。全局阈值覆盖 80% 场景。

#### QUEST Q6: _full_result 的内存管理

**问题**：完整结果可能很大（50KB+），长对话中多个大结果会占用大量内存。是否需要过期清理？

**建议**：保留最近 N 轮（如 5 轮）的完整结果，更早的自动清理（`_full_result = ""`）。被清理后 Result-query 返回"结果已过期"。

#### QUEST Q7: 截断时机

**问题**：截断在哪个阶段执行？三个选项：
- A) `dispatch()` 后立即截断 `result`
- B) `prepare_messages()` 中截断
- C) Provider 转换时截断

**建议**：方案 C（Provider 转换时）。`result` 字段保持完整供 UI 使用；截断只影响发给 LLM 的内容。但 C 需要每个 Provider 都实现截断逻辑。方案 B 是折中——在 AgentContext 层统一处理，Provider 无感知。

---

## 关键参考

### 源码

- `mutagent/src/mutagent/messages.py:146` — StreamEvent 定义、ToolUseBlock
- `mutagent/src/mutagent/builtins/agent_impl.py:112-144` — 工具执行循环 + 日志捕获
- `mutagent/src/mutagent/builtins/tool_set_impl.py:337-361` — dispatch 实现
- `mutagent/src/mutagent/runtime/log_store.py` — `_tool_log_buffer` ContextVar 日志捕获机制
- `mutagent/src/mutagent/builtins/userio_impl.py:173-184` — tool_exec_start/end 渲染
- `mutagent/src/mutagent/builtins/anthropic_provider.py` — ToolUseBlock → tool_result 转换
- `mutagent/src/mutagent/builtins/openai_provider.py` — ToolUseBlock → tool_result 转换

### 相关规范

- `mutagent/docs/specifications/feature-message-model.md` — Message 模型（ToolUseBlock/StreamEvent 设计）
- `mutagent/docs/specifications/feature-context-management.md` — Context 管理策略
