# 双向流式接口 设计规范

**状态**：✅ 已完成
**日期**：2026-02-15
**类型**：功能设计

## 1. 背景

### Phase 1: 流式输出 [✅ 已完成]

已实现三层流式输出支持：LLMClient 层 SSE streaming、Agent 层事件透传、REPL 层实时渲染。所有层级使用统一的 `AsyncIterator[StreamEvent]` 返回类型。详见 2.1–2.6 节。

### Phase 2: 流式输入 + 单次 run 循环 [当前]

当前 `Agent.run()` 接受单个字符串 `user_input`，每轮用户输入都需要外部循环重新调用 `agent.run()`：

```
REPL while True:
    user_input = input("> ")
    async for event in agent.run(user_input):   ← 每轮调用一次
        render(event)
```

**问题**：
- 多轮对话循环逻辑在 REPL 层（调用方），Agent 只处理单轮
- 输入和输出接口不对称——输出是 `AsyncIterator[StreamEvent]`，输入是普通 `str`
- 框架无法感知会话的生命周期（每次 `run` 调用都是独立的）

**目标**：
- 输入改为迭代器接口 `AsyncIterator[InputEvent]`，与输出对称
- `agent.run()` 只调用一次，内部驱动完整多轮对话循环
- 框架层面支持实时双向传输。当前实现中每轮传递一条完整消息即可，但接口设计为未来的实时流式输入（如语音转文字逐词输入）预留空间

### 目标调用链

```
REPL ──async for──▸ agent.run(input_stream)
                        │
                        ├── async for input_event in input_stream   ← 等待用户输入
                        │       │
                        │       └── InputEvent(type="user_message", text="...")
                        │
                        ├── while tool_use:
                        │       async for event in step()            ← LLM 流式响应
                        │           yield StreamEvent(text_delta / tool_* / response_done)
                        │       handle_tool_calls()
                        │           yield StreamEvent(tool_exec_*)
                        │
                        ├── yield StreamEvent(type="turn_done")      ← 一轮完毕
                        │
                        └── 回到顶部，等待下一个 input_event
```

## 2. 设计方案

### Phase 1: 流式输出 [✅ 已完成]

> 以下 2.1–2.6 节为已完成的 Phase 1 设计，保留作为参考。

#### 2.1 核心设计决策

**统一接口模式**：所有层级使用统一的 `AsyncIterator[StreamEvent]` 返回类型，通过 `stream` 参数控制底层 HTTP 行为：

- `stream=True`（默认）：LLM 客户端走 SSE 流式协议，逐 token yield 事件
- `stream=False`：LLM 客户端走普通 HTTP 请求，将完整响应包装为少量事件 yield

调用方始终使用 `async for event in ...` 消费，无需关心底层是否真正流式。

**SSE 手动解析**：Claude API 的 SSE 格式固定，手动解析，不引入额外依赖。

**工具调用显示（中等详细度）**：REPL 中显示工具名称 + 参数摘要 + 执行结果摘要。

**错误处理（MVP）**：不做自动重试，通过 `StreamEvent(type="error")` 上报错误。

#### 2.2 Claude API Streaming 协议

Claude Messages API 支持 `stream: true` 参数，返回 SSE（Server-Sent Events）流。关键事件类型：

| 事件类型 | 含义 | 关键字段 |
|---------|------|---------|
| `message_start` | 消息开始 | `message.usage.input_tokens` |
| `content_block_start` | 内容块开始（text 或 tool_use） | `content_block.type`, `content_block.id` |
| `content_block_delta` | 增量内容 | `delta.text` 或 `delta.partial_json` |
| `content_block_stop` | 内容块结束 | — |
| `message_delta` | 消息级别更新 | `delta.stop_reason`, `usage.output_tokens` |
| `message_stop` | 消息结束 | — |

#### 2.3 流式事件模型（`messages.py`）

```python
@dataclass
class StreamEvent:
    """流式响应中的单个事件。"""
    type: str          # "text_delta" | "tool_use_start" | "tool_use_delta" | "tool_use_end"
                       # | "tool_exec_start" | "tool_exec_end" | "response_done" | "error"
    text: str = ""                          # type="text_delta" 时的文本片段
    tool_call: ToolCall | None = None       # type="tool_use_start" / "tool_exec_start" 时
    tool_json_delta: str = ""               # type="tool_use_delta" 时，partial JSON 片段
    tool_result: ToolResult | None = None   # type="tool_exec_end" 时，工具执行结果
    response: Response | None = None        # type="response_done" 时，完整的 Response 对象
    error: str = ""                         # type="error" 时的错误信息
```

#### 2.4 LLMClient 层：`send_message`

```python
async def send_message(
    self,
    messages: list[Message],
    tools: list[ToolSchema],
    system_prompt: str = "",
    stream: bool = True,
) -> AsyncIterator[StreamEvent]:
    ...
```

#### 2.5 Agent 层：`run` 和 `step`（Phase 1 版本，将被 Phase 2 替代）

```python
async def run(self, user_input: str, stream: bool = True) -> AsyncIterator[StreamEvent]:
    ...

async def step(self, stream: bool = True) -> AsyncIterator[StreamEvent]:
    ...
```

#### 2.6 REPL 层：流式渲染（Phase 1 版本，将被 Phase 2 替代）

```python
while True:
    user_input = input("> ")
    async for event in agent.run(user_input):
        # 按事件类型渲染到终端
```

---

### Phase 2: 流式输入 + 单次 run 循环

#### 2.7 InputEvent 数据模型（`messages.py` 新增）

```python
@dataclass
class InputEvent:
    """流式输入中的单个事件。"""
    type: str          # "user_message"
    text: str = ""     # type="user_message" 时的用户消息文本
```

事件类型说明：
- `user_message` — 一条完整的用户消息（当前唯一类型）
- 未来可扩展：`cancel`（取消当前处理）、`input_delta`（实时流式输入片段）等

#### 2.8 StreamEvent 新增 `turn_done` 事件类型

在现有 `StreamEvent.type` 中新增 `"turn_done"` 类型，表示 Agent 完成了一轮用户消息的完整处理（包括所有工具调用循环），即将等待下一个输入。

**为什么需要 `turn_done`**：

在 Phase 1 中，`agent.run()` 在一轮结束后直接 return，调用方通过 `async for` 结束自然知道一轮完毕。但在 Phase 2 中 `agent.run()` 不再 return（它会继续等待下一个输入），因此需要显式事件通知调用方：

- REPL 收到 `turn_done` 后可以打印换行、更新状态
- 一轮可能包含多个 `response_done`（多次工具调用），`turn_done` 明确标识整轮结束
- 程序化调用方可以用 `turn_done` 划分轮次边界

`StreamEvent` 的 `type` 注释更新为：

```python
type: str  # "text_delta" | "tool_use_start" | "tool_use_delta" | "tool_use_end"
           # | "tool_exec_start" | "tool_exec_end" | "response_done"
           # | "turn_done" | "error"
```

`turn_done` 事件不携带额外数据，所有字段保持默认值。

#### 2.9 Agent.run 签名变更

**当前**（Phase 1）：

```python
async def run(self, user_input: str, stream: bool = True) -> AsyncIterator[StreamEvent]:
```

**变更为**（Phase 2）：

```python
async def run(
    self,
    input_stream: AsyncIterator[InputEvent],
    stream: bool = True,
) -> AsyncIterator[StreamEvent]:
    """Run the agent conversation loop, consuming input events and yielding output events.

    This is the main entry point. It consumes InputEvents from input_stream,
    processes each through the LLM (with tool call loops), and yields
    StreamEvents for each piece of incremental output.

    The generator runs until input_stream is exhausted.

    Args:
        input_stream: Async iterator of user input events.
        stream: Whether to use SSE streaming for HTTP requests.

    Yields:
        StreamEvent instances for each piece of incremental output.
        A "turn_done" event is yielded after each user message is fully processed.
    """
    ...
```

**`step` 签名不变**——它仍然处理单次 LLM 调用，不涉及输入。

#### 2.10 Agent.run 实现逻辑

```python
async def run(self, input_stream, stream=True):
    async for input_event in input_stream:
        if input_event.type == "user_message":
            self.messages.append(Message(role="user", content=input_event.text))

            while True:
                response = None
                async for event in self.step(stream=stream):
                    yield event
                    if event.type == "response_done":
                        response = event.response
                    elif event.type == "error":
                        break

                if response is None:
                    yield StreamEvent(type="error", error="No response_done event received from LLM")
                    break

                self.messages.append(response.message)

                if response.stop_reason == "tool_use" and response.message.tool_calls:
                    results = []
                    for call in response.message.tool_calls:
                        yield StreamEvent(type="tool_exec_start", tool_call=call)
                        result = await self.tool_selector.dispatch(call)
                        yield StreamEvent(type="tool_exec_end", tool_call=call, tool_result=result)
                        results.append(result)
                    self.messages.append(Message(role="user", tool_results=results))
                else:
                    break  # end_turn，结束内层循环，等待下一个 input

            yield StreamEvent(type="turn_done")
```

与 Phase 1 实现的关键差异：
- 外层循环从 `input_stream` 消费输入，而非接受单个 `user_input` 字符串
- 内层工具调用循环结束后 `break` 而非 `return`，回到外层等待下一轮输入
- 每轮结束后 yield `turn_done` 事件

#### 2.11 REPL 层适配（`__main__.py`）

```python
async def _input_stream():
    """Async generator that reads user input from stdin."""
    loop = asyncio.get_event_loop()
    while True:
        try:
            user_input = await loop.run_in_executor(None, input, "> ")
        except (EOFError, KeyboardInterrupt):
            return
        if not user_input.strip():
            return
        yield InputEvent(type="user_message", text=user_input)


async def _main():
    config = load_config()
    agent = create_agent(...)

    print(f"mutagent ready  (model: {config['model']})")
    print("Type your message. Empty line or Ctrl+C to exit.\n")

    async for event in agent.run(_input_stream()):
        if event.type == "text_delta":
            print(event.text, end="", flush=True)
        elif event.type == "tool_exec_start":
            name = event.tool_call.name if event.tool_call else "?"
            args_summary = _summarize_args(event.tool_call.arguments if event.tool_call else {})
            if args_summary:
                print(f"\n  [{name}({args_summary})]", flush=True)
            else:
                print(f"\n  [{name}]", flush=True)
        elif event.type == "tool_exec_end":
            if event.tool_result:
                status = "error" if event.tool_result.is_error else "done"
                summary = event.tool_result.content[:100]
                if len(event.tool_result.content) > 100:
                    summary += "..."
                print(f"  -> [{status}] {summary}", flush=True)
        elif event.type == "error":
            print(f"\n[Error: {event.error}]", file=sys.stderr, flush=True)
        elif event.type == "turn_done":
            print()  # 轮次结束换行

    print("Bye.")
```

**核心变化**：
- 外层 `while True` + `input()` 循环消失，替换为单次 `async for event in agent.run(_input_stream())`
- 用户输入的读取移入 `_input_stream()` 异步生成器
- `input()` 需要用 `run_in_executor` 包装为异步调用（因为在 async generator 上下文中）
- 退出逻辑由 `_input_stream()` 的 return 控制——Ctrl+C / EOF / 空行时生成器结束，`agent.run` 的外层 `async for` 耗尽后自然结束

#### 2.12 删除 `run_agent()` 便捷函数（`main.py`）

`run_agent()` 当前未被使用，直接删除。后续有需求时再考虑封装。

#### 2.13 执行流程分析：异步生成器的协作

单次 `agent.run()` 的可行性依赖于 Python async generator 的协作式执行模型。以下是完整的执行流程：

```
1. REPL: async for event in agent.run(_input_stream())
2.   → agent.run.__anext__() 被调用
3.   → agent.run 执行: async for input_event in input_stream
4.     → _input_stream.__anext__() 被调用
5.     → _input_stream: await run_in_executor(None, input, "> ")
6.     → 用户看到 "> " 提示符，等待输入（整个 async 链挂起）
7.     → 用户输入 "hello"
8.     → _input_stream yields InputEvent(type="user_message", text="hello")
9.   → agent.run 处理消息，调用 self.step()
10.  → agent.run yields StreamEvent(type="text_delta", text="Hi")
11.  → REPL 收到事件，print("Hi", end="")
12.  → REPL 再次调用 agent.run.__anext__()
13.  → agent.run 继续 yield 更多事件 ...
14.  → agent.run yields StreamEvent(type="turn_done")
15.  → REPL 收到 turn_done，print("\n")
16.  → REPL 再次调用 agent.run.__anext__()
17.  → agent.run 回到外层循环: async for input_event in input_stream
18.  → 回到步骤 4，等待下一个输入
```

关键点：当 `agent.run` 在等待 `input_stream` 的下一个元素时，REPL 侧的 `async for` 也在等待 `agent.run` 的下一个 yield。两者通过 asyncio 事件循环协作——`input_stream` 通过 `run_in_executor` 在线程中等待 `input()`，不阻塞事件循环。

## 3. 已确认决策

### Phase 1

- **统一接口模式**：所有层级返回 `AsyncIterator[StreamEvent]`，`stream` 参数控制底层 HTTP 行为
- **`stream` 默认 `True`**：交互模式是主要使用场景，程序化调用方可传 `stream=False`
- **SSE 手动解析**：不引入额外依赖
- **工具调用中等详细度**：工具名 + 参数摘要 + 结果摘要
- **MVP 不自动重试**：错误通过 `StreamEvent(type="error")` 上报
- **破坏性变更可接受**：项目 0.1.0 早期阶段

### Phase 2

- **InputEvent dataclass**：使用独立数据类型（非裸 `str`），与 `StreamEvent` 对称，预留扩展空间
- **Ctrl+C = 退出会话**：方案 A，Ctrl+C 终止整个 `agent.run` generator。行为简单一致。后续如需"打断当前轮次但不退出"，可通过 `InputEvent(type="cancel")` 或 REPL 外层 `while True` 重建 `agent.run` 实现（见下方可行性说明）
- **error 后继续**：error 事件 + turn_done 后继续等待下一个输入，不终止会话
- **删除 `run_agent()`**：`main.py` 中的 `run_agent()` 当前未被使用，直接删除

#### 关于"双击 Ctrl+C"可行性说明

> 用户提问：是否可以实现第一次 Ctrl+C 打断 AI、第二次 Ctrl+C 退出程序？

**可行**，但不在本次 Phase 2 范围内。实现思路：

```python
async def _main():
    agent = create_agent(...)
    while True:
        try:
            async for event in agent.run(_input_stream()):
                ...
            break  # input_stream 正常结束 → 退出
        except KeyboardInterrupt:
            print("\n[Interrupted]")
            # agent 对象复用，messages 历史保留
            # 下一轮创建新的 agent.run + _input_stream
            continue
```

工作原理：
- 第一次 Ctrl+C（LLM 处理中）→ `KeyboardInterrupt` 打断 `async for` → `agent.run` generator 被关闭 → 外层 `except` 捕获 → `continue` 重建 `agent.run(_input_stream())`
- 第二次 Ctrl+C（等待输入时）→ `_input_stream()` 内部 `input()` 抛出 → generator return → `agent.run` 耗尽 → `break` → 正常退出

**注意事项**：被中断时 `agent.messages` 可能处于不完整状态（已添加 user message 但无 assistant response）。重建 `agent.run` 后 LLM 会看到这条悬空的 user message 并继续回答，这在大多数情况下是可接受的行为。如需严格清理，可在 `except` 块中 pop 最后一条 user message。

此方案可作为后续增强，不影响 Phase 2 核心设计。

## 4. 待定问题

（无——所有问题已在本轮确认，决策已合并至第 3 节。）

## 5. 实施步骤清单

### 阶段一：数据模型与 LLMClient 流式支持 [✅ 已完成]
- [x] **Task 1.1**: 在 `messages.py` 中新增 `StreamEvent` 数据类
  - [x] 定义 `StreamEvent` dataclass 及所有字段
  - [x] 确保类型注解完备
  - 状态：✅ 已完成

- [x] **Task 1.2**: 修改 `client.py` 中 `send_message` 声明
  - [x] 添加 `stream: bool = True` 参数
  - [x] 返回类型改为 `AsyncIterator[StreamEvent]`
  - [x] 添加必要的导入
  - 状态：✅ 已完成

- [x] **Task 1.3**: 在 `claude.impl.py` 中实现流式 + 非流式双路径
  - [x] 实现 SSE 行解析逻辑（手动解析）
  - [x] `stream=True`：逐事件 yield StreamEvent
  - [x] `stream=False`：包装完整响应为事件序列 yield
  - [x] 内部累积状态，流结束时组装完整 Response
  - [x] 错误处理（HTTP 错误、连接中断 → error 事件）
  - 状态：✅ 已完成

### 阶段二：Agent 流式循环 [✅ 已完成]
- [x] **Task 2.1**: 修改 `agent.py` 中 `run` 和 `step` 声明
  - [x] `run` 添加 `stream` 参数，返回类型改为 `AsyncIterator[StreamEvent]`
  - [x] `step` 添加 `stream` 参数，返回类型改为 `AsyncIterator[StreamEvent]`
  - 状态：✅ 已完成

- [x] **Task 2.2**: 修改 `agent.impl.py` 实现
  - [x] `step`：透传 `send_message` 事件流
  - [x] `run`：流式循环 + 从 `response_done` 事件提取 Response 驱动循环
  - [x] `run`：工具调用阶段 yield `tool_exec_start` / `tool_exec_end` 事件
  - [x] 正确维护 `self.messages` 历史
  - 状态：✅ 已完成

### 阶段三：REPL 流式渲染 [✅ 已完成]
- [x] **Task 3.1**: 修改 `__main__.py` 使用流式输出
  - [x] 将 `await agent.run()` 替换为 `async for event in agent.run()`
  - [x] 实现各事件类型的终端渲染（文本增量、工具执行、错误）
  - [x] 保持 Ctrl+C 优雅退出
  - 状态：✅ 已完成

- [x] **Task 3.2**: 同步修改 `main.py` 中的 `run_agent()`
  - [x] 适配 `Agent.run()` 新返回类型（消费事件流，提取文本）
  - 状态：✅ 已完成

### 阶段四：测试验证 [✅ 已完成]
- [x] **Task 4.1**: Agent 流式循环测试（`test_agent.py`）
  - [x] 适配原有 5 个测试用例到流式接口
  - [x] 新增 4 个流式事件序列测试
  - [x] 覆盖：简单响应、工具调用、多工具、错误事件、stream=False
  - 状态：✅ 已完成

- [x] **Task 4.2**: LLMClient 集成测试（`test_claude_impl.py`）
  - [x] 适配 3 个 send_message 集成测试到流式接口
  - 状态：✅ 已完成

- [x] **Task 4.3**: E2E 测试（`test_e2e.py`）
  - [x] 适配 4 个 e2e 测试到流式接口
  - 状态：✅ 已完成

---

### 阶段五：流式输入 + 单次 run [✅ 已完成]

- [x] **Task 5.1**: 在 `messages.py` 中新增 `InputEvent` 数据类
  - [x] 定义 `InputEvent` dataclass（type, text）
  - [x] 在 `StreamEvent.type` 文档注释中补充 `"turn_done"`
  - 状态：✅ 已完成

- [x] **Task 5.2**: 修改 `agent.py` 中 `run` 声明
  - [x] 参数从 `user_input: str` 改为 `input_stream: AsyncIterator[InputEvent]`
  - [x] 更新 docstring
  - [x] 添加 `InputEvent` 的 TYPE_CHECKING 导入
  - 状态：✅ 已完成

- [x] **Task 5.3**: 修改 `agent.impl.py` 中 `run` 实现
  - [x] 外层循环改为 `async for input_event in input_stream`
  - [x] 内层保留 step + tool_calls 循环
  - [x] 每轮结束 yield `StreamEvent(type="turn_done")`
  - [x] error 事件后继续等待输入（不 return）
  - 状态：✅ 已完成

- [x] **Task 5.4**: 修改 `__main__.py` 适配新接口
  - [x] 实现 `_input_stream()` 异步生成器（run_in_executor + input）
  - [x] 移除外层 `while True` 循环
  - [x] 改为单次 `async for event in agent.run(_input_stream())`
  - [x] 处理 `turn_done` 事件
  - 状态：✅ 已完成

- [x] **Task 5.5**: 删除 `main.py` 中的 `run_agent()` 函数
  - [x] 删除 `run_agent()` 函数定义
  - [x] 确认无其他模块引用该函数（仅 CLAUDE.md 文档引用，已同步更新）
  - 状态：✅ 已完成

### 阶段六：测试适配 [✅ 已完成]

- [x] **Task 6.1**: 适配 `test_agent.py`
  - [x] 所有调用 `agent.run(user_input)` 的地方改为传入 InputEvent 迭代器
  - [x] 新增 turn_done 事件断言
  - [x] 新增多轮对话测试：连续多个 InputEvent 通过单次 run 处理
  - [x] 新增 error 后继续测试：error 事件后能继续处理下一个输入
  - 状态：✅ 已完成

- [x] **Task 6.2**: 适配 `test_e2e.py`
  - [x] 适配 e2e 测试到新的 `run(input_stream)` 签名
  - 状态：✅ 已完成

- [x] **Task 6.3**: 验证 `test_claude_impl.py` 无需改动
  - [x] `send_message` 签名未变，测试全部通过
  - 状态：✅ 已完成

---

### 实施进度总结
- ✅ **阶段一：数据模型与 LLMClient** — 100% 完成 (3/3 任务)
- ✅ **阶段二：Agent 流式循环** — 100% 完成 (2/2 任务)
- ✅ **阶段三：REPL 流式渲染** — 100% 完成 (2/2 任务)
- ✅ **阶段四：测试验证** — 100% 完成 (3/3 任务)
- ✅ **阶段五：流式输入 + 单次 run** — 100% 完成 (5/5 任务)
- ✅ **阶段六：测试适配** — 100% 完成 (3/3 任务)

**Phase 1 测试结果：164/165 通过，1 个 pre-existing 失败（版本号断言），2 个跳过（需 API key）**
**Phase 2 测试结果：166/167 通过，1 个 pre-existing 失败（版本号断言），2 个跳过（需 API key）**

## 6. 测试验证

### Phase 1 [✅ 已完成]

#### 单元测试
- [x] `StreamEvent` 组装：text_delta、tool_use 系列事件、response_done
- [x] 非流式包装：完整响应正确转为事件序列
- [x] 错误场景：error 事件正确生成和传递
- 执行结果：12/12 通过

#### 集成测试
- [x] LLMClient 非流式路径：成功、工具调用、API 错误
- [x] 完整 e2e 流程：inspect → patch → run → save
- [x] 工具调用流式事件序列正确性
- [x] 自我演化流程（创建新工具并使用）
- 执行结果：164/165 通过（1 个 pre-existing 版本号断言失败）

### Phase 2 [✅ 已完成]

#### 单元测试
- [x] `InputEvent` 构造与字段验证（通过所有使用 InputEvent 的测试隐式覆盖）
- [x] `agent.run(input_stream)` 单轮：单条 InputEvent → 事件序列包含 turn_done
- [x] `agent.run(input_stream)` 多轮：多条 InputEvent → 每轮各自的事件序列 + turn_done
- [x] error 后继续：error 事件后 agent 继续消费下一个 InputEvent
- 执行结果：14/14 agent 测试通过（含 2 个新增多轮/错误恢复测试）

#### 集成测试
- [x] 适配已有 4 个 e2e 用例到新签名
- [x] `test_claude_impl.py` 无需改动，全部通过
- 执行结果：166/167 通过（1 个 pre-existing 版本号断言失败）
