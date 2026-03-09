# agent.run() 重构

**状态**：✅ 已完成
**日期**：2026-03-01
**类型**：重构

## 背景

Message 模型重构（`feature-message-model.md`）完成后，mutbot 迁移分析（`mutbot/docs/specifications/refactor-message-model-migration.md`）发现：当前 agent.run() 只管核心循环（接收输入 → LLM 调用 → 工具执行），大量本该由 agent 处理的职责泄漏到了应用层（mutbot bridge）。

mutbot bridge 当前承担的 agent 级职责：

| 泄漏的职责 | bridge 代码量 | 本质归属 |
|-----------|-------------|---------|
| Message 元数据（id, timestamp, model, duration, tokens） | ~40 行，6 次 `_gen_msg_id()`，5 次 `_get_model()` | agent 创建 Message，应自行设置 |
| 中断清理（partial text 提交、ToolUseBlock 标记） | ~60 行 `_commit_partial_state()` | agent 内部状态管理 |
| Response 生命周期（首个 text_delta 检测、duration 计算） | ~20 行，追踪 `_response_first_delta` / `_response_start_mono` | agent 知道 response 何时开始/结束 |
| Turn 边界标记 | ~15 行 turn_start/turn_done chat_message | agent 知道 turn 何时开始/结束 |
| InputEvent 纯文本输入 | InputEvent.text: str，不支持多模态 | agent 输入应与 Message blocks 统一 |

**设计目标**：agent.run() 对 `context.messages` 全权负责——创建、元数据、中断清理。应用层只做网络转发和持久化触发。输入流直接使用 Message，消除 InputEvent → Message 转换的信息丢失。

---

## 设计方案

### 一、输入统一：Message 替代 InputEvent

**决策**：删除 InputEvent，agent.run() 的输入流直接使用 `AsyncIterator[Message]`。

**问题**：InputEvent 是 Message 的弱类型影子——`type` 可由 blocks 内容推断，`text` 等价于 `TextBlock`，`data: dict` 散装了 Message 的 typed 字段（id, sender, timestamp）。两层转换（InputEvent → Message）有信息丢失风险——`data` 中未被显式 copy 的字段消失在 context.messages 之外。更关键的是，Message 模型已支持多模态（ImageBlock、DocumentBlock），但 InputEvent 只有 `text: str`，用户输入的多模态路径被堵死。

**方案选型**（详见 `feature-multichat.md` 方案分析）：

| 方案 | 思路 | 结论 |
|------|------|------|
| A: InputEvent 携带 blocks | `data.blocks = [...]` | 排除：绕开 ContentBlock 体系搞平行序列化 |
| D: data 扩展 | `data.images = [...]` | 排除：每种模态一套约定，不可持续 |
| B: InputEvent 携带 Message | `InputEvent.message = Message(...)` | 可行，但 InputEvent 退化为纯信封 |
| **C: Message 替代 InputEvent** | 输入流直接是 Message | **采用** |

B 和 C 在信息完整性、类型安全上等效。选 C 的理由：

1. **概念更少** — 一个类（Message）vs 两个类（InputEvent + Message），输入 = 存储格式，零信息丢失
2. **激活信号自然表达** — 有 TurnStartBlock → 触发处理，没有 → 只存储。不需要 `type` 字段做事件分发
3. **契合 mutobj 理念** — 新能力 = 新 ContentBlock 子类，不需要改签名或加枚举值

**新签名**：

```python
# 当前
async def run(self, input_stream: AsyncIterator[InputEvent], ...) -> AsyncIterator[StreamEvent]:

# 新
async def run(self, input_stream: AsyncIterator[Message], ...) -> AsyncIterator[StreamEvent]:
```

**agent.run() 主循环**：

```python
async for msg in input_stream:
    self.context.messages.append(msg)
    if any(isinstance(b, TurnStartBlock) for b in msg.blocks):
        # 有 TurnStartBlock → 开始处理
        async for event in self._process_turn():
            yield event
    # 无 TurnStartBlock → 只存储，不处理（多人聊天场景）
```

**单人模式**：bridge 将 TurnStartBlock 附在用户消息里，每条消息自动触发处理：

```python
msg = Message(role="user", blocks=[
    TurnStartBlock(turn_id=uuid4().hex),
    TextBlock(text=user_input),
], sender="user")
await input_queue.put(msg)
```

**多人模式**：由 block 组合区分"只存储"和"存储+处理"：

```python
# 纯消息（无 TurnStartBlock，只存储）
Message(role="user", blocks=[TextBlock("大家好")], sender="Alice")
Message(role="user", blocks=[TextBlock("今天开会吗")], sender="Bob")

# 激活（有 TurnStartBlock，触发处理）
Message(role="user", blocks=[TurnStartBlock(turn_id="t1"), TextBlock("@bot 总结")], sender="Carol")

# 纯激活（UI 按钮，无文本内容）
Message(role="user", blocks=[TurnStartBlock(turn_id="t2")])
```

### 二、Message 元数据：agent 内部计算

**原则**：agent 创建的 Message 由 agent 设置元数据。`context.messages` 自包含，持久化时不需要外部注入。

#### User Message

输入流直接是 Message，应用层构建完整的 user Message（含 id、timestamp、sender、多模态 blocks），agent.run() 直接 append 到 context.messages，不做二次构建：

```python
# 当前：agent 从 InputEvent 构建 Message（line 47-49）
self.context.messages.append(
    Message(role="user", blocks=[TextBlock(text=input_event.text)])
)

# 新：应用层构建完整 Message，agent 直接存储
async for msg in input_stream:
    self.context.messages.append(msg)  # 零信息丢失
```

User Message 的 id/timestamp/sender 由应用层设置。CLI 模式不设置时保持默认值，不影响功能。

#### Assistant Message

agent.run() 在 step() 完成后、append response.message 前，设置元数据：

```python
# 当前（line 78）
self.context.messages.append(response.message)

# 新
response.message.id = _gen_id()
response.message.timestamp = _response_start_ts
response.message.model = getattr(self.llm, "model", "")
response.message.duration = time.time() - _response_start_ts
response.message.input_tokens = response.usage.get("input_tokens", 0)
response.message.output_tokens = response.usage.get("output_tokens", 0)
self.context.messages.append(response.message)
```

`_response_start_ts` 在 step() 调用前记录。id 由 agent 生成（简单 uuid）。

### 三、response_start 事件

**问题**：text_delta 先于 response.message 存在。应用层（前端）需要在首个 text_delta 前获知消息的 id/model/timestamp 以创建消息卡片。

**方案**：新增 `response_start` StreamEvent（与 `response_done` 对称），在每次调用 step() 前 yield：

```python
# agent_impl.py run() — step() 前
msg_id = _gen_id()
_response_start_ts = time.time()
model = getattr(self.llm, "model", "")

yield StreamEvent(
    type="response_start",
    response=Response(
        message=Message(id=msg_id, model=model, timestamp=_response_start_ts),
    ),
)

async for event in self.step(stream=stream):
    yield event
    ...
```

`response_start` 复用 StreamEvent 现有的 `response` 字段，携带预生成的 Message 元数据（id, model, timestamp）。response_done 的 response.message 携带完整元数据（含 duration, tokens）。

**前端流式协议**：
```
response_start(id, model, timestamp) → 创建消息卡片
text_delta(text) → 追加文本
tool_use_start/delta/end → 工具调用构建
response_done(duration, tokens) → 完成本次 LLM 调用
tool_exec_start/end → 工具执行
... (可能多轮 response_start → response_done)
turn_done(turn_id, duration) → 整轮结束
```

### 四、Turn 边界标记

Turn 边界的完整设计（多人聊天场景）记录在 `feature-multichat.md`。本次重构建立基础机制，后续多人聊天在此基础上扩展。

#### 新增 ContentBlock 子类

```python
# messages.py
@dataclass
class TurnStartBlock(ContentBlock):
    type: str = "turn_start"
    turn_id: str = ""

@dataclass
class TurnEndBlock(ContentBlock):
    type: str = "turn_end"
    turn_id: str = ""
    duration: float = 0         # 整轮耗时（秒）
```

#### Turn 生命周期

- **Turn 开始**：输入 Message 包含 TurnStartBlock → agent 识别并开始处理（见"一、输入统一"）
- **Turn 结束**：处理完成后，agent 在最后一条 assistant Message 的 blocks 末尾追加 `TurnEndBlock(turn_id=..., duration=...)`

Provider 忽略未知 block 类型。Turn blocks 随 Message 持久化和恢复。

**单人模式**：bridge 自动在每条用户消息的 blocks 中附带 TurnStartBlock，行为与当前一致。
**多人模式**：应用层决定何时在消息中包含 TurnStartBlock，实现选择性激活。

### 五、中断清理

agent.run() 在 `finally` 块中自处理中断清理，保证 `context.messages` 在任何退出路径下都处于有效状态。

```python
async def run(self, input_stream: AsyncIterator[Message], ...):
    _partial_text: list[str] = []
    try:
        async for msg in input_stream:
            ...
            _partial_text.clear()
            while True:
                async for event in self.step(stream=stream):
                    if event.type == "text_delta" and event.text:
                        _partial_text.append(event.text)
                    yield event
                    ...
                # response_done 后
                self.context.messages.append(response.message)
                _partial_text.clear()  # 已提交到 message，清空
                ...  # tool execution
    finally:
        # 提交部分文本（正常退出时 _partial_text 为空，no-op）
        if _partial_text:
            self.context.messages.append(Message(
                role="assistant",
                blocks=[TextBlock(text="".join(_partial_text) + "\n\n[interrupted]")],
            ))
        # 标记未完成 ToolUseBlock（正常退出时全部 done，no-op）
        if self.context.messages and self.context.messages[-1].role == "assistant":
            for b in self.context.messages[-1].blocks:
                if isinstance(b, ToolUseBlock) and b.status != "done":
                    b.status = "done"
                    b.result = "[interrupted]"
                    b.is_error = True
```

**覆盖的中断场景**：
- **LLM 流中断**：`_partial_text` 非空 → 提交 partial assistant Message
- **工具执行中断**：response.message 已在 context.messages，ToolUseBlock status="running" → 标记
- **并行工具部分完成**：已完成的 ToolUseBlock 是 "done"，未完成的被标记
- **正常退出**：`_partial_text` 为空，所有 ToolUseBlock 都是 "done"，cleanup 无操作

应用层中断处理简化为：取消 task + 广播状态。无需了解 agent 内部状态。

---

## 受影响文件

### `src/mutagent/messages.py`

- 新增 `TurnStartBlock`、`TurnEndBlock` 定义
- 删除 `InputEvent`（由 Message 替代）
- StreamEvent docstring 新增 `response_start` 事件类型说明

### `src/mutagent/builtins/agent_impl.py`

主要修改集中在 `run()` 函数：

| 修改点 | 当前行号 | 变更 |
|--------|---------|------|
| run() 签名 | 36 | `AsyncIterator[InputEvent]` → `AsyncIterator[Message]` |
| User Message 创建 | 47-49 | 删除：不再从 InputEvent 构建 Message，直接 append 输入 Message |
| 主循环分发 | 43-51 | 按 TurnStartBlock 存在与否决定"只存储"或"存储+处理" |
| step() 调用前 | 53-56 | 新增：预生成元数据 + yield response_start |
| response.message append | 78 | 设置 id/timestamp/model/duration/tokens 后再 append |
| turn_done | 153 | 追加 TurnEndBlock 到最后一条 assistant Message |
| 整个 run() | 42-153 | 包裹 try/finally，finally 中做中断清理 |
| text_delta 追踪 | 56-57 | 在 yield event 前追踪 _partial_text |

### Provider 兼容

TurnStartBlock / TurnEndBlock 是 ContentBlock 子类。Provider 的 `_messages_to_*` 转换逻辑对未知 block 类型应跳过（不崩溃）。

需验证：
- `_block_to_claude()` — 未知类型返回 None（`anthropic_provider.py:85`），已有兜底
- `_messages_to_openai()` — 需确认未知 block 的处理方式

---

## 代码量预估

| 文件 | 新增/修改 | 说明 |
|------|-----------|------|
| `messages.py` | ~10 行（+删除 InputEvent） | TurnStartBlock + TurnEndBlock + docstring |
| `agent_impl.py` | ~40 行 | 输入统一 + 元数据设置 + response_start + TurnBlock + finally 块 |
| **合计** | **~50 行** | |

---

## 关键参考

### 源码

- `mutagent/src/mutagent/messages.py` — Message/ContentBlock/StreamEvent 定义
- `mutagent/src/mutagent/builtins/agent_impl.py:36-153` — agent.run() 主循环（所有修改集中在此）
- `mutagent/src/mutagent/builtins/anthropic_provider.py:85` — `_block_to_claude()` 未知类型兜底
- `mutagent/src/mutagent/builtins/openai_provider.py` — `_messages_to_openai()` 需确认未知 block 处理

### 相关规范

- `mutbot/docs/specifications/refactor-message-model-migration.md` — mutbot 迁移文档（依赖本文档完成后实施）
- `mutagent/docs/specifications/feature-message-model.md` — Message 模型设计（已完成）
- `mutagent/docs/specifications/feature-multichat.md` — Turn 完整设计（后续展开）

---

## 实施步骤清单

### Phase 1: messages.py — 类型定义变更 [✅ 已完成]

- [x] **Task 1.1**: 新增 TurnStartBlock 和 TurnEndBlock
  - 在 `messages.py` 的 ContentBlock 子类区域（ToolUseBlock 之后）新增两个 dataclass
  - `TurnStartBlock(type="turn_start", turn_id="")`
  - `TurnEndBlock(type="turn_end", turn_id="", duration=0)`
  - 状态：✅ 已完成

- [x] **Task 1.2**: 更新 StreamEvent docstring
  - 在 StreamEvent 的事件类型列表中新增 `"response_start"` 说明
  - 新增 `turn_id: str = ""` 字段
  - 状态：✅ 已完成

- [x] **Task 1.3**: 删除 InputEvent
  - 移除 `InputEvent` dataclass 定义
  - 更新文件顶部注释区块名称（`InputEvent` 相关）
  - 状态：✅ 已完成

### Phase 2: agent 核心 — run() 重写 [✅ 已完成]

- [x] **Task 2.1**: 更新 `agent.py` 声明
  - `run()` 签名：`AsyncIterator[InputEvent]` → `AsyncIterator[Message]`
  - TYPE_CHECKING import：移除 `InputEvent`，新增 `Message`
  - 状态：✅ 已完成

- [x] **Task 2.2**: 重写 `agent_impl.py` run()
  - import 更新：移除 `InputEvent`，新增 `TurnStartBlock`、`TurnEndBlock`、`Response`；新增 `from uuid import uuid4`
  - 新增辅助函数 `_gen_id()` — 返回 `uuid4().hex[:12]`
  - 签名：`AsyncIterator[InputEvent]` → `AsyncIterator[Message]`
  - 主循环重写（对照设计方案一~五）
  - response_start 事件、assistant Message 元数据、text_delta 追踪、TurnEndBlock、try/finally 中断清理
  - 状态：✅ 已完成

- [x] **Task 2.3**: 更新 turn_done StreamEvent 定义
  - 在 StreamEvent 中新增 `turn_id: str = ""` 字段
  - 状态：✅ 已完成

### Phase 3: 调用方迁移 — InputEvent → Message [✅ 已完成]

- [x] **Task 3.1**: `userio_impl.py` — input_stream()
  - `yield InputEvent(...)` → `yield Message(role="user", blocks=[TurnStartBlock(...), TextBlock(text=user_input)])`
  - interactions data：短期不传递（CLI 模式不需要）
  - 状态：✅ 已完成

- [x] **Task 3.2**: `userio.py` — 声明更新
  - `input_stream()` docstring 更新：`InputEvent` → `Message`
  - 状态：✅ 已完成

- [x] **Task 3.3**: `delegate_impl.py` — delegate()
  - `yield InputEvent(...)` → `yield Message(role="user", blocks=[TurnStartBlock(...), TextBlock(text=task)])`
  - 状态：✅ 已完成

- [x] **Task 3.4**: `main_impl.py` — CLI 入口
  - `yield InputEvent(...)` → `yield Message(role="user", blocks=[TurnStartBlock(...), TextBlock(text=text)])`
  - 状态：✅ 已完成

### Phase 4: 测试更新 [✅ 已完成]

- [x] **Task 4.1**: `test_agent.py` — 核心测试
  - helper 函数 `_single_input()` / `_multi_input()`：`InputEvent` → `Message` + `TurnStartBlock`
  - 更新事件序列断言（新增 `response_start` 事件）
  - 状态：✅ 已完成

- [x] **Task 4.2**: `test_e2e.py` — 端到端测试
  - `_single_input()` 更新：`InputEvent` → `Message` + `TurnStartBlock`
  - 状态：✅ 已完成

- [x] **Task 4.3**: `test_tool_set.py` — 工具集测试
  - `yield InputEvent(...)` → `yield Message(...)` + `TurnStartBlock`
  - 状态：✅ 已完成

- [x] **Task 4.4**: `test_userio.py` — InputEvent 测试清理
  - 删除 `TestInputEventData` 类（InputEvent 已不存在）
  - 更新 `TestInputStreamInteractions` 断言：`event.text` → `TextBlock` 提取
  - 状态：✅ 已完成

### Phase 5: Provider 兼容验证 [✅ 已完成]

- [x] **Task 5.1**: 验证 Provider 对 TurnStartBlock/TurnEndBlock 的处理
  - `anthropic_provider.py` `_block_to_claude()` — 未知类型返回 None ✓
  - `openai_provider.py` `_messages_to_openai()` — 未知类型静默跳过 ✓
  - 状态：✅ 已完成

### Phase 6: 构建与测试验证 [✅ 已完成]

- [x] **Task 6.1**: 运行全量测试
  - 631 passed, 5 skipped（skipped 均为 pre-existing）
  - 状态：✅ 已完成

## 实施备注

### interactions data 迁移

当前 `userio_impl.py` 将 interactive blocks 的用户响应放在 `InputEvent.data['interactions']` 中，agent_impl.py 并不读取此字段（由 mutbot bridge 消费）。迁移到 Message 后，此数据暂无对应字段。考虑方案：
- **短期**：不传递 interactions（CLI 模式不需要）
- **后续**：作为新的 ContentBlock 子类（如 InteractionResultBlock）或在 mutbot 迁移时处理

### hidden 消息

当前 `agent_impl.py:46` 通过 `InputEvent.data.get("hidden")` 跳过 context.messages 存储。新设计中所有 Message 都会被 append。hidden 消息是 mutbot setup wizard 专用（`mutbot/agent_bridge.py:509`），在 mutbot 迁移文档中处理。

## 测试验证

全量测试通过：631 passed, 5 skipped（pre-existing）。

修改的源文件：
- `src/mutagent/messages.py` — +TurnStartBlock/TurnEndBlock, -InputEvent, StreamEvent 新增 response_start+turn_id
- `src/mutagent/builtins/agent_impl.py` — run() 重写（输入统一/元数据/response_start/TurnBlock/中断清理）
- `src/mutagent/agent.py` — 声明签名更新
- `src/mutagent/builtins/userio_impl.py` — InputEvent → Message
- `src/mutagent/builtins/delegate_impl.py` — InputEvent → Message
- `src/mutagent/builtins/main_impl.py` — InputEvent → Message
- `src/mutagent/userio.py` — docstring 更新

修改的测试文件：
- `tests/test_agent.py` — InputEvent → Message, 事件序列断言更新
- `tests/test_e2e.py` — InputEvent → Message
- `tests/test_tool_set.py` — InputEvent → Message
- `tests/test_userio.py` — 删除 TestInputEventData, 更新 interaction 测试
