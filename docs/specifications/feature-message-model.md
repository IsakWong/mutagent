# Message 模型设计规范

**状态**：✅ 已完成
**日期**：2026-03-01
**类型**：功能设计

## 背景

### 问题

mutagent 当前没有抽象的 Context 管理。`Agent.messages: list` 是一个无管理的原始列表，所有消息无差别地全量传递给 LLM。这导致：

- **无 Context Window 感知**：消息无限累积，直到超出模型上下文窗口才报错
- **无消息过滤**：无法标记某些消息为"不再发送"（如过时的/
工具调用结果）
- **System Prompt 不可组合**：`Agent.system_prompt: str` 是单一字符串，无法按职责分段管理
- **Token 统计仅记录不行动**：`LLMClient.context_window` 只用于查询，未参与任何主动管理
- **无多模态支持**：Message 仅支持纯文本，不支持图像/PDF/thinking 内容

### 规范整合

本规范整合了原 `feature-multimodal-thinking.md` 中的 Message 模型设计。两者高度相关 — Message 结构既是多模态的载体，也是 Context 管理的操作对象。原多模态规范在本规范稳定后弃用或归档。

### 规范范围

本规范聚焦于 **Message 数据模型** 和 **AgentContext 接口声明**。Context 管理策略（压缩、裁剪、预算分配等）将在单独的 `feature-context-management.md` 中设计。

### 应用层愿景

mutagent 需要为上层应用提供精确管理 LLM 输入的基础能力：
- **角色定位** — 系统指令，定义 Agent 的身份和行为
- **记忆** — 长期偏好和知识，跨会话持久
- **动态参考** — 代码片段、文件内容等，可能随文件修改自动更新
- **对话历史** — 完全可控（启用/禁用/删除消息管理 context）

mutagent 提供基础抽象和默认实现，**不限制上层应用的实现方式**。

---

## 设计方案

### 设计原则

1. **核心是规范，不是实现** — Declaration 只定义接口契约
2. **Agent 极简** — 只持有 `llm`、`tools`、`context` 三个核心部件
3. **外部创建，显式组装** — 所有部件由调用方创建传入
4. **messages 归属 AgentContext** — 对话历史是 context 的一部分
5. **不同 Agent 可用完全不同的 Context 实现** — 通过子类或 `@impl`

### Agent Declaration

```python
class Agent(mutagent.Declaration):
    llm: LLMClient              # LLM 通信
    tools: ToolSet              # 工具管理与分发
    context: AgentContext        # 上下文管理（消息、系统指令、token 追踪）

    async def run(input_stream, stream=True, check_pending=None) -> AsyncIterator[StreamEvent]: ...
    async def step(stream=True) -> AsyncIterator[StreamEvent]: ...
    async def handle_tool_calls(tool_calls: list[ToolUseBlock]) -> None: ...
```

移除 `system_prompt`、`messages`、`max_tool_rounds`。`handle_tool_calls` 直接更新 ToolUseBlock 的 result/is_error/duration 字段。

### AgentContext Declaration

```python
class AgentContext(mutagent.Declaration):
    context_window: int             # 模型上下文窗口大小（tokens），0 = 未知
    prompts: list[Message] = []     # 系统指令
    messages: list[Message] = []    # 对话历史

    def prepare_prompts(self) -> list[Message]: ...     # 发送前：排序、过滤
    def prepare_messages(self) -> list[Message]: ...    # 发送前：过滤（默认直接返回）

    # --- Token 用量 ---
    def update_usage(self, usage: dict[str, int]) -> None: ...
    def get_context_used(self) -> int: ...
    def get_context_percent(self) -> float | None: ...
```

**两个列表，共享 Message 类型**：`prompts` 存系统指令，`messages` 存对话历史。两者类型相同，但生命周期不同（prompts 配置式管理，messages 累积式增长）。列表直接暴露在 Declaration 上——简单明确，实现可以自由操作列表。

**prepare 的语义**：发送前的最后整理和筛选。不是唯一的读取接口，而是生成 LLM 调用输入的最终步骤。

---

## Message 模型设计

### Message 结构

```python
@dataclass
class Message:
    role: str                           # "user" | "assistant" | "system"
    blocks: list[ContentBlock] = []     # 内容

    # --- 标识 ---
    id: str = ""                        # 消息标识（空 = 未分配，应用层生成）
    label: str = ""                     # 段标识（prompt: "base"/"memory"，对话消息通常为空）
    sender: str = ""                    # 创建者身份（用户名、Agent 名，空 = 未设置）
    model: str = ""                     # AI 模型标识（如 "claude-sonnet-4-20250514"）

    # --- 事实性元数据 ---
    timestamp: float = 0                # 消息时间（0 = 未设置）
    duration: float = 0                 # 生成/执行耗时（秒，0 = 未设置）
    input_tokens: int = 0               # 此次 LLM 调用 token（0 = 未追踪）
    output_tokens: int = 0

    # --- Provider 提示 ---
    cacheable: bool = True              # Anthropic cache_control
    priority: int = 0                   # Prompt 排序优先级（值越大越靠前，对话消息为 0）
```

**元数据划分原则**：

| 类别 | 含义 | 放在 | 示例 |
|------|------|------|------|
| **标识** | 这是什么、谁创建的 | Message 成员 | id、label、sender、model |
| **事实性** | 消息发生了什么 | Message 成员 | timestamp、duration、tokens |
| **排序** | 发送顺序控制 | Message 成员 | priority |
| **管理性** | 怎么处理这条消息（策略） | AgentContext 内部 | retained、pinned |

**字段说明**：
- `id` — 消息的唯一标识。由应用层生成（mutagent 框架不生成），必须保证不重复。用于消息管理、WebSocket 广播、序列化还原、未来消息间引用（如回复、引用）。与 block 级别的 id 概念一致（ToolUseBlock.id 标识工具调用）。空串 = 未分配
- `label` — 段标识。主要用于 prompt 管理：`"base"`、`"memory"`、`"session"`、`"datetime"`。对话消息通常为空。实现层通过 label 过滤和定位 prompts 列表中的特定段
- `sender` — 消息创建者的身份标识。与 `model` 正交：多 Agent 场景中，不同 Agent 可能使用相同 model 但 sender 不同。user 消息 = 用户名/显示名，assistant 消息 = Agent 名称。空串 = 未设置
- `model` — AI 模型标识（如 `"claude-sonnet-4-20250514"`）。技术字段，用于计费、能力判断、调试。非 AI 消息为空
- `timestamp` — 消息产生时间。0 表示未设置
- `duration` — 生成耗时。assistant 消息 = LLM 响应时间。0 表示未追踪（工具执行耗时记录在 ToolUseBlock.duration 上）
- `input_tokens` / `output_tokens` — 此次 LLM 调用的 token 用量。0 表示未追踪
- `cacheable` — Provider 缓存提示。主要用于 prompt（Anthropic 的 cache_control），对话消息默认 True
- `priority` — Prompt 排序优先级。`prepare_prompts()` 按 priority 降序排列（值越大越靠前）。对话消息为 0（不参与排序）。影响 LLM 注意力分配和 Anthropic 缓存效率（cacheable 的 prompt 应靠前）

**使用示例**：
```python
# 纯文本对话消息
Message(role="user", blocks=[TextBlock(text="hello")], sender="Alice")

# 系统指令（prompt）
Message(role="system", blocks=[TextBlock(text="你是一个助手")], label="base", cacheable=True)

# 多模态
Message(role="user", blocks=[
    TextBlock(text="请看下"),
    ImageBlock(data=b64, media_type="image/png"),
    TextBlock(text="这张图片"),
], sender="Alice")

# assistant（thinking + text + tool_use，工具已执行）
Message(role="assistant", blocks=[
    ThinkingBlock(thinking="推理过程...", signature="xxx"),
    TextBlock(text="让我查一下。"),
    ToolUseBlock(id="t1", name="search", input={"q": "..."}, result="搜索结果", duration=0.5),
], sender="Research Agent", model="claude-sonnet-4-20250514", duration=2.3, output_tokens=150)
```

### ContentBlock 类型体系

```python
@dataclass
class ContentBlock:
    type: str

# --- 文本 ---
@dataclass
class TextBlock(ContentBlock):
    type: str = "text"
    text: str = ""

# --- 多模态 ---
@dataclass
class ImageBlock(ContentBlock):
    type: str = "image"
    data: str = ""              # base64
    media_type: str = ""
    url: str = ""               # 与 data 二选一

@dataclass
class DocumentBlock(ContentBlock):
    type: str = "document"
    data: str = ""              # base64
    media_type: str = ""        # "application/pdf"

# --- Thinking ---
@dataclass
class ThinkingBlock(ContentBlock):
    type: str = "thinking"
    thinking: str = ""          # 推理文本（空 = 被屏蔽）
    signature: str = ""         # Anthropic 加密签名，多轮时原样回传
    data: str = ""              # 被屏蔽时的加密数据，多轮时原样回传

# --- Tool ---
@dataclass
class ToolUseBlock(ContentBlock):
    type: str = "tool_use"
    id: str = ""                # 工具调用标识（LLM 生成）
    name: str = ""
    input: dict = field(default_factory=dict)
    # 执行状态与结果（框架执行后更新）
    status: str = ""            # "" = 未调度, "running" = 执行中, "done" = 已完成
    result: str = ""            # 结果内容
    is_error: bool = False
    duration: float = 0         # 执行耗时（秒，0 = 未执行）
```

**Thinking 块说明**：统一为单个 ThinkingBlock 类型。`thinking` 非空 = 可见推理过程，`data` 非空 = 被 Anthropic 安全系统屏蔽的加密数据（内容不可读，但后续轮次必须原样回传）。`signature` 是 Anthropic 的加密签名，用于验证 thinking 未被篡改。Provider 负责映射回 API 的两种 type（`thinking` / `redacted_thinking`）。

**ToolUseBlock 合并了工具调用和结果**：Message 层不存在独立的 ToolResult 概念。工具调用是一个完整的生命周期——请求（name/input）→ 调度（status="running"）→ 完成（status="done", result/is_error/duration 更新），全部在同一个 block 上更新。`status` 字段明确标识执行阶段：空串 = LLM 刚创建尚未调度，`"running"` = 正在执行，`"done"` = 已完成（检查 `is_error` 判断成败）。Provider 负责在发送 LLM API 时将 ToolUseBlock 拆分为 tool_use + tool_result 两条 API 消息。

**并行工具调用**：LLM 可在一次回复中请求多个工具调用（多个 ToolUseBlock），框架默认并行执行。LLM 不显式标记并行/顺序——多个调用在同一 Message 中隐含"无依赖"。

**StreamEvent 与工具结果传递**：StreamEvent 移除 `tool_result` 字段。工具执行结果通过 ToolUseBlock 原地更新传递——`handle_tool_calls()` 直接修改 assistant Message 中 ToolUseBlock 的 status/result/is_error/duration 字段。`tool_exec_end` 事件携带对应的 `ToolUseBlock` 引用（复用 `tool_call` 字段），消费者从该引用读取执行结果。不再需要独立的 tool_result 数据结构。

**中断状态处理**：当用户取消或会话异常中断时，ToolUseBlock 可能停留在 `status="running"` 或 `status=""`（未调度）。处理规则：
- `status=""` — 未调度的工具调用，设置 `status="done"`, `result="[interrupted]"`, `is_error=True`
- `status="running"` — 执行中被中断，设置 `status="done"`, `result="[interrupted]"`, `is_error=True`
- `status="done"` — 已完成，无需处理

中断恢复由应用层（如 mutbot agent_bridge）负责：遍历最后一条 assistant Message 的 ToolUseBlock，将所有非 `"done"` 状态的 block 标记为中断。Provider 发送时只处理 `status="done"` 的 ToolUseBlock 生成 tool_result——未完成的 block 不生成 tool_result，LLM 不会看到这些工具调用的结果。

### Block 扩展

ContentBlock 通过子类扩展。mutagent 定义核心类型（上述），应用层可定义自己的块类型：

```python
# 应用层扩展示例（mutbot）
@dataclass
class TurnStartBlock(ContentBlock):
    type: str = "turn_start"
    turn_id: str = ""

@dataclass
class TurnEndBlock(ContentBlock):
    type: str = "turn_end"
    turn_id: str = ""
    duration: float = 0         # 整轮耗时
```

Provider 转换时忽略不认识的块类型。应用层定义的块不发送给 LLM，但参与消息列表的存储和渲染。

### Provider 转换

Provider 负责将 Message 列表转换为 LLM API 格式，包括**从 ToolUseBlock 生成 tool_result 消息**：

| Provider | 映射方式 |
|----------|---------|
| **Anthropic** | blocks 映射为 `content` 数组；含 result 的 ToolUseBlock → 生成 `tool_use` 块（assistant msg）+ `tool_result` 块（user msg）；保证 user/assistant 严格交替 |
| **OpenAI** | TextBlock/ImageBlock → `content` 数组；ToolUseBlock → `tool_calls` 字段 + `role: "tool"` 结果消息；ThinkingBlock → 忽略 |

**Provider 的拆分职责**：Message 列表中连续两条 assistant 消息（第一条含已完成的 ToolUseBlock）→ Provider 在两者之间插入合成的 tool_result 消息，确保 LLM API 角色交替正确。

### 当前 Message 模型（对比）

```python
# 当前实现（将被替代）
@dataclass
class Message:
    role: str
    content: str = ""
    tool_calls: list[ToolCall] = []
    tool_results: list[ToolResult] = []
```

### 设计验证：消除应用层双重存储

当前 mutbot 维护两套消息格式：`mutagent.Message`（LLM 格式，4 个字段）和 `chat_messages: list[dict]`（UI 格式，含 id/timestamp/model/sender/duration 等）。导致 `_rebuild_llm_messages()` 80+ 行重建逻辑、AgentBridge 200+ 行消息构建状态机、双重序列化。

新 Message 的设计目标：**应用层直接使用 `AgentContext.messages` 作为唯一存储**，不需要额外的消息包装或转换层。

**mutbot chat_messages 字段覆盖验证**：

| mutbot chat_messages | 新 Message 对应 | 覆盖 |
|---|---|---|
| id | Message.id | ✅ |
| type (text/tool_group/...) | blocks 内容隐含 | ✅ |
| role | Message.role | ✅ |
| content | blocks (TextBlock) | ✅ |
| timestamp | Message.timestamp | ✅ |
| model | Message.model | ✅ |
| sender | Message.sender | ✅ |
| duration_ms | Message.duration | ✅ |
| tool_call_id / tool_name / arguments | ToolUseBlock | ✅ |
| result / is_error | ToolUseBlock.result / .is_error | ✅ |
| turn_id / turn_start / turn_done | 应用层 UI 事件，不属于 Message | ✅ 不需要 |

**改造后架构**：

```
AgentContext.messages: list[Message]  ← 唯一消息存储
├── LLM 调用：prepare_messages() → Provider 转换 → API
├── UI 渲染：直接读取 blocks + 元数据
├── 持久化：序列化 Message 列表
└── 管理：AgentContext 实现层维护 retained/pinned（通过 Message.id 引用）
```

---

## Prompt 设计

### 已确认：Prompt 统一使用 Message

PromptSegment 不再需要。Prompt 就是 Message——存在 `AgentContext.prompts` 列表中。

Prompt 的段标识通过 Message 的 `label` 字段管理：
- **段标识** — `label="base"`、`label="memory"` 等，直接在 Message 上
- `cacheable` — Message 自带，Provider 读取
- 列表顺序 = 发送顺序（或由 `prepare_prompts()` 内部排序）
- 实现层通过遍历 prompts 列表、按 label 过滤来定位特定段
- 不需要 AgentContext 上的辅助方法，列表操作 + label 过滤足够简单

**预定义 prompt 段名**（约定，非强制）：

| 段名 | 说明 | cacheable | priority |
|------|------|-----------|----------|
| `base` | 基础角色指令 | ✅ | 100 |
| `memory` | 长期记忆、偏好 | ✅ | 80 |
| `session` | 会话级上下文 | ⚠️ 视变化频率 | 60 |
| `datetime` | 当前日期时间 | ❌ | 20 |

### Prompt 排序

`prepare_prompts()` 按 `Message.priority` 降序排列，确保：

1. **LLM 注意力分配** — 靠前的内容通常获得更高权重
2. **Anthropic 缓存效率** — cacheable=True 的 prompt 排在前面（内容变化导致后续所有块缓存失效）

预定义段名的 priority 值预留了间隔（100/80/60/20），便于应用层在中间插入自定义 prompt 段。

### 缓存映射（Anthropic Provider）

- `prepare_prompts()` 返回 `list[Message]`
- Provider 将每个 prompt Message 的 blocks 映射为 `system` 数组中的 content block
- `msg.cacheable=True` → 加 `cache_control: {type: "ephemeral"}`

### Provider 映射

| 来源 | Anthropic API | OpenAI API |
|------|-------------|-----------|
| `prepare_prompts()` | 顶层 `system` 字段（content block 数组） | `role: "system"` 或 `"developer"` 消息 |
| `prepare_messages()` | `messages` 数组（user/assistant 交替） | `messages` 数组（多角色） |

---

## 消息的双重视角

### LLM 视角

Message 列表与 LLM API 不直接等价——Provider 负责转换。一个含工具调用的轮次：

```
Message 列表（应用层存储）：
[0] user: [TextBlock("帮我查一下天气")]
[1] assistant: [TextBlock("让我查查"), ToolUseBlock(weather, result="晴天 25°C")]
[2] assistant: [TextBlock("今天晴天，25度")]

LLM API（Provider 生成）：
[0] user: "帮我查一下天气"
[1] assistant: [text("让我查查"), tool_use(weather)]
[2] user: [tool_result("晴天 25°C")]              ← Provider 生成
[3] assistant: "今天晴天，25度"
```

Provider 将 [1] 的 ToolUseBlock 拆分为 assistant(tool_use) + user(tool_result)，保证 role 交替。

### 用户视角

用户在聊天界面看到的是"气泡"，一个用户操作产生一个气泡，不关心 LLM 内部的多轮交互：

```
[用户] 帮我查一下天气
[助手] 今天晴天，25度  (中间的 tool_call/result 可折叠或隐藏)
```

### 两者不等价

| 差异 | Message 列表 | LLM API | 聊天气泡 |
|------|------------|---------|---------|
| **数量** | 3 条 Message | 4 条 API 消息 | 2 个气泡 |
| **tool_call/result** | ToolUseBlock 上（同一 block） | 拆分为两条消息 | 折叠在助手气泡内 |
| **thinking** | blocks 中的 ThinkingBlock | 原样传递 | 可选展示/隐藏 |
| **图文混排** | 一条 Message 的 blocks 自然表达 | 映射为 content 数组 | 需要渲染引擎支持 |
| **编辑** | 修改/删除 Message | 影响 LLM 上下文 | 用户编辑粒度可能不同 |

### mutagent 的职责边界

mutagent 提供 **Message（LLM 视角的模型）**。聊天气泡的渲染、分组、编辑交互是应用层（mutbot）的职责。

但 Message 的设计不应使映射变得困难。blocks 模型有利于此：
- 应用层可检查 blocks 类型决定渲染方式
- 图文混排 = Message 的 blocks 中 TextBlock 和 ImageBlock 交错，应用层逐 block 渲染
- 用户编辑 = 应用层操作 blocks（增删改特定 block），重新 add_message

### 聊天消息的设计考量

**简单方案**：每条聊天消息对应一条 Message，不同类型内容不混排（一张图片是独立的 Message）。

**复杂方案**：一条聊天消息内图文混排，对应一条含多个 blocks 的 Message。用户编辑时可以在 blocks 间插入、删除。

这是应用层的选择，mutagent 的 blocks 模型两种方案都支持。

---

## LLM API 输入结构分析

### 一次 LLM 调用的组成

| 组成部分 | Anthropic Messages API | OpenAI Chat Completions API |
|---------|----------------------|---------------------------|
| **系统指令** | 顶层 `system` 字段（string 或 content block 数组） | messages 中 `role: "system"` 或 `"developer"` 消息 |
| **对话消息** | `messages` 数组（仅 user/assistant，严格交替） | `messages` 数组（多角色，无严格交替） |
| **工具定义** | `tools` 数组 | `tools` 数组（外包 `{type: "function", function: {...}}`） |
| **Thinking** | `thinking: {type, budget_tokens}` | `reasoning_effort: "low"/"medium"/"high"` |
| **缓存控制** | content block 上 `cache_control: {type: "ephemeral"}` | 自动缓存（无 API 控制） |

### 系统指令的差异

**Anthropic** — 顶层独立字段，支持 content block 数组（可缓存）：
```json
{
  "system": [
    {"type": "text", "text": "基础指令...", "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "动态上下文..."}
  ]
}
```

**OpenAI** — messages 中的消息（reasoning 模型用 `developer` 角色）：
```json
{"role": "system", "content": "指令文本"}
```

### 消息格式的差异

| 方面 | Anthropic | OpenAI |
|------|-----------|--------|
| **Tool 调用** | content 中 `tool_use` 块 | `tool_calls` 数组（与 content 平级） |
| **Tool 结果** | user 消息 content 中 `tool_result` 块（Provider 从 ToolUseBlock.result 生成） | 独立 `role: "tool"` 消息（Provider 从 ToolUseBlock.result 生成） |
| **Tool 参数** | 原生 JSON（`input: {...}`） | JSON 字符串（`arguments: "{...}"`） |
| **Thinking** | content 中 `thinking` + `signature` 块 | 不可见 |
| **图像** | `type: "image"` + `source` | `type: "image_url"` + data URI |
| **PDF** | `type: "document"`（原生） | 不支持 |
| **音频** | 不支持 | `type: "input_audio"` |

---

## 未来方向

### 多 Agent 协作

Sub-Agent 不是 mutagent 的核心概念，框架不强制特定的多 Agent 模式。当前的 `AgentToolkit`/`delegate` 暂时从核心 API 中移除，避免误导为核心设计。

多 Agent 场景中，每个 Agent 拥有独立的 AgentContext。上下文的继承（如父 Agent 的部分 messages 传递给子 Agent）是应用层的选择——直接操作子 Agent 的 `context.messages` 或 `context.prompts` 即可，框架不需要额外机制。

### Context 管理策略

消息过滤、token 预算、context 压缩、跨会话记忆等策略见 `feature-context-management.md`。

---

## 设计决策记录

- **规范整合**：本规范整合原 `feature-multimodal-thinking.md` 的 Message 模型设计，原文档后续弃用
- **规范拆分**：本规范聚焦 Message 模型 + AgentContext 接口。Context 管理策略独立为 `feature-context-management.md`
- **Agent 命名**：`client` → `llm: LLMClient`，`tool_set` → `tools: ToolSet`
- **Agent 极简**：移除 system_prompt、messages、max_tool_rounds
- **外部创建，显式组装**：AgentContext 与 llm、tools 风格一致
- **核心只定义规范**：策略通过不同实现提供，不用 strategy 字段切换
- **不内置 tokenizer**：token 估算用字符数近似
- **computed value 用 get 函数**
- **Message 纯 blocks 模型**：`blocks: list[ContentBlock]` 是唯一内容容器
- **ToolUseBlock 合并调用与结果**：工具调用是完整生命周期（请求→执行→结果），全部在同一 block 更新。Provider 负责拆分为 LLM API 所需的 tool_use + tool_result 两条消息
- **ToolUseBlock 增加 status 字段**：明确标识执行阶段（"" → "running" → "done"），消除 `result=""` 的歧义（未执行 vs 空结果）
- **Message 承载描述性元数据**：model、timestamp、duration、tokens、cacheable、priority 是直接成员
- **特殊值而非 None**：未设置的字段用 0/"" 表达，不用 Optional
- **priority 字段**：Prompt 排序通过显式 priority 控制，预定义段名预留间隔（100/80/60/20）。不是过度设计——排序是 prompt 管理的基本需求
- **无 meta 字段**：Message 不包含 `meta: dict` 或其他通用扩展机制。当前 mutbot 的所有需求已被 Message 成员字段和 ContentBlock 子类覆盖，没有具体的扩展需求。Message 是 dataclass（非 Declaration），mutobj.Extension 不直接适用。未来如有具体需求再决定方案
- **渐进式工具信息披露**：独立为 `feature-tool-progressive-disclosure.md`
- **AgentContext 直接暴露列表**：`prompts` 和 `messages` 简单明确
- **Prompt 统一为 Message**：废弃 PromptSegment，label/cacheable/priority 作为 Message 成员
- **两个列表**：prompts + messages 对应 LLM API 两个输入，更细分类通过 label 管理
- **id 是核心字段**：消息标识由应用层生成，用于消息管理、WebSocket 广播、序列化还原
- **sender 是核心字段**：与 model 正交——多 Agent 场景中不同 Agent 可能使用相同 model 但 sender 不同
- **label 是核心字段**：prompt 段标识直接放在 Message 上，不需要 AgentContext 辅助方法
- **Message 作为唯一存储**：上层应用直接使用 Message，消除双重格式和转换逻辑
- **Block 扩展通过子类**：应用层定义扩展块类型，Provider 忽略不认识的块类型
- **tool_exec_end 复用 tool_call 字段**：StreamEvent 移除 tool_result 字段后，tool_exec_end 事件通过 tool_call 字段传递已完成的 ToolUseBlock 引用，消费者从中读取结果
- **中断状态统一标记**：ToolUseBlock 非 "done" 状态一律标记为 `status="done"`, `result="[interrupted]"`, `is_error=True`。Provider 只为 "done" 状态生成 tool_result
- **delegate 暂时移除**：AgentToolkit/delegate 从核心 API 中移除，Sub-Agent 不是 mutagent 核心概念

---

## 关键参考

### 源码

- `mutagent/src/mutagent/agent.py` — Agent Declaration
- `mutagent/src/mutagent/messages.py` — 当前 Message/ToolCall/ToolResult/StreamEvent
- `mutagent/src/mutagent/client.py` — LLMClient（`context_window`）
- `mutagent/src/mutagent/provider.py` — LLMProvider.send()
- `mutagent/src/mutagent/builtins/agent_impl.py` — Agent.run()/step() 实现
- `mutagent/src/mutagent/builtins/anthropic_provider.py` — `_messages_to_claude()` 转换逻辑
- `mutagent/src/mutagent/builtins/openai_provider.py` — `_messages_to_openai()` 转换逻辑
- `mutbot/src/mutbot/session.py` — AgentSession
- `mutbot/src/mutbot/web/agent_bridge.py` — token 追踪、飞行中状态
- `mutbot/src/mutbot/runtime/session_impl.py:184` — `_rebuild_llm_messages()`

### 相关规范

- `mutagent/docs/specifications/feature-multimodal-thinking.md` — 原多模态设计（被本规范整合）
- `mutagent/docs/specifications/feature-multi-agent.md` — DelegateTool、Sub-Agent（delegate 暂时从核心 API 移除）
- `mutagent/docs/specifications/feature-context-management.md` — Context 管理策略
- `mutagent/docs/specifications/feature-tool-progressive-disclosure.md` — 工具结果渐进式披露机制
- `TASKS.md` — "自定义 Agent Session" 需求描述

---

## 实施步骤清单

### Phase 1: Message 数据模型 [核心基础] [✅ 已完成]

所有后续阶段依赖此阶段。重写 `messages.py`，替换旧的 Message/ToolCall/ToolResult。

- [x] **Task 1.1**: 重写 `src/mutagent/messages.py` — ContentBlock 体系 + 新 Message
  - [x] 定义 ContentBlock 基类（`type: str`）
  - [x] 定义 TextBlock、ImageBlock、DocumentBlock、ThinkingBlock、ToolUseBlock
  - [x] 定义新 Message（role, blocks, id, label, sender, model, timestamp, duration, input_tokens, output_tokens, cacheable, priority）
  - [x] 移除旧 ToolCall、ToolResult、Message
  - [x] 更新 Response — `message: Message` 使用新 Message
  - [x] 更新 StreamEvent — `tool_call` 字段类型改为 `ToolUseBlock | None`，移除 `tool_result` 字段（`tool_exec_end` 事件复用 `tool_call` 字段传递已完成的 ToolUseBlock）
  - [x] 保留 ToolSchema、InputEvent、Content 不变
  - 状态：✅ 已完成

### Phase 2: AgentContext Declaration [✅ 已完成]

- [x] **Task 2.1**: 创建 `src/mutagent/context.py` — AgentContext Declaration
  - [x] `context_window: int`（0 = 未知）
  - [x] `prompts: list[Message] = []`
  - [x] `messages: list[Message] = []`
  - [x] `prepare_prompts() -> list[Message]`
  - [x] `prepare_messages() -> list[Message]`
  - [x] `update_usage(usage: dict[str, int]) -> None`
  - [x] `get_context_used() -> int`
  - [x] `get_context_percent() -> float | None`
  - 状态：✅ 已完成

- [x] **Task 2.2**: 创建 `src/mutagent/builtins/context_impl.py` — 默认实现
  - [x] `prepare_prompts`: 按 priority 降序排列，过滤
  - [x] `prepare_messages`: 默认直接返回 messages
  - [x] `update_usage / get_context_used / get_context_percent`: token 用量追踪
  - [x] 在 context.py 底部注册 impl 模块
  - 状态：✅ 已完成

### Phase 3: LLMProvider & LLMClient 签名更新 [✅ 已完成]

Provider 和 Client 的 `system_prompt: str` 参数改为 `prompts: list[Message]`，由 Provider 负责转换。

- [x] **Task 3.1**: 更新 `src/mutagent/provider.py`
  - [x] `send()` 参数: `system_prompt: str` → `prompts: list[Message]`
  - 状态：✅ 已完成

- [x] **Task 3.2**: 更新 `src/mutagent/client.py`
  - [x] `send_message()` 参数: `system_prompt: str` → `prompts: list[Message]`
  - 状态：✅ 已完成

- [x] **Task 3.3**: 更新 `src/mutagent/builtins/client_impl.py`
  - [x] `send_message` 实现适配新签名
  - [x] API 录制逻辑适配新 Message 格式
  - 状态：✅ 已完成

### Phase 4: Provider 转换逻辑重写 [✅ 已完成]

两个 Provider 的核心转换逻辑全部重写，处理 blocks 模型。

- [x] **Task 4.1**: 重写 `src/mutagent/builtins/anthropic_provider.py`
  - [x] `_messages_to_claude`: blocks → content 数组；含已完成 ToolUseBlock 的 assistant 消息 → 拆分为 tool_use(assistant) + tool_result(user)；ThinkingBlock → thinking/redacted_thinking；ImageBlock/DocumentBlock 映射
  - [x] prompts → `system` 字段（content block 数组），`cacheable=True` → `cache_control`
  - [x] 流式解析: 处理 thinking/redacted_thinking content_block_start/delta
  - [x] `_response_from_claude`: 解析 thinking blocks，构建新 Message(blocks=[...])
  - 状态：✅ 已完成

- [x] **Task 4.2**: 重写 `src/mutagent/builtins/openai_provider.py`
  - [x] `_messages_to_openai`: blocks → content 数组；ToolUseBlock → tool_calls + role:"tool" 消息；忽略 ThinkingBlock
  - [x] prompts → `role: "system"` 消息
  - [x] `_response_from_openai`: 构建新 Message(blocks=[...])
  - 状态：✅ 已完成

### Phase 5: Agent Declaration & ToolSet 更新 [✅ 已完成]

- [x] **Task 5.1**: 更新 `src/mutagent/agent.py`
  - [x] `client` → `llm: LLMClient`
  - [x] `tool_set` → `tools: ToolSet`
  - [x] 新增 `context: AgentContext`
  - [x] 移除 `system_prompt`、`messages`、`max_tool_rounds`
  - [x] `handle_tool_calls` 签名: `(tool_calls: list[ToolUseBlock]) -> None`（原地更新 block，不返回 list）
  - 状态：✅ 已完成

- [x] **Task 5.2**: 更新 `src/mutagent/tools.py` + `builtins/tool_set_impl.py`
  - [x] `dispatch` 签名: 接收 `ToolUseBlock`，执行后直接更新 block 的 status/result/is_error/duration
  - [x] 不再返回 ToolResult（ToolResult 已移除）
  - 状态：✅ 已完成

### Phase 6: Agent 实现重写 [✅ 已完成]

- [x] **Task 6.1**: 重写 `src/mutagent/builtins/agent_impl.py`
  - [x] `run()`: 使用 `self.context.messages` 替代 `self.messages`；用户消息构建 `Message(role="user", blocks=[TextBlock(text=...)])`
  - [x] `step()`: 调用 `self.context.prepare_prompts()` + `self.context.prepare_messages()` → `self.llm.send_message(messages, tools, prompts=prompts)`；响应后调用 `self.context.update_usage()`
  - [x] `handle_tool_calls()`: 遍历 ToolUseBlock，设置 status="running" → 调用 `self.tools.dispatch(block)` → block 自动更新；不再拼装独立 tool_result 消息
  - [x] 工具结果不再以独立 user Message 添加（ToolUseBlock 在 assistant Message 上原地更新，Provider 发送时自动生成 tool_result）
  - 状态：✅ 已完成

### Phase 7: 消费者更新 [✅ 已完成]

更新 mutagent 内部所有引用旧类型的文件。

- [x] **Task 7.1**: 更新 import 站点
  - [x] `builtins/delegate_impl.py` — 适配新 Message + Agent 字段名
  - [x] `builtins/main_impl.py` — 适配新 Message + Agent 字段名
  - [x] `userio.py` / `builtins/userio_impl.py` / `extras/rich/userio_impl.py` — StreamEvent 字段变更
  - [x] `toolkits/web_toolkit.py` / `builtins/web_toolkit_impl.py` — ToolSchema 不变，无需改动（确认）
  - [x] `builtins/schema.py` — ToolSchema 不变（确认）
  - [x] `__init__.py` — 考虑是否导出 AgentContext
  - 状态：✅ 已完成

### Phase 8: 测试 [✅ 已完成]

- [x] **Task 8.1**: 重写 `tests/test_messages.py` — 新 Message/ContentBlock 测试
  - 状态：✅ 已完成

- [x] **Task 8.2**: 新增 `tests/test_context.py` — AgentContext 测试
  - 状态：✅ 已完成（AgentContext 测试覆盖在 test_agent.py 中）

- [x] **Task 8.3**: 重写 `tests/test_openai_provider.py` — 基于 blocks 的转换测试
  - 状态：✅ 已完成

- [x] **Task 8.4**: 更新 `tests/test_claude_impl.py` — 基于 blocks 的转换测试
  - 状态：✅ 已完成

- [x] **Task 8.5**: 更新 `tests/test_agent.py` — 新 Agent 字段 + 新消息流转
  - 状态：✅ 已完成

- [x] **Task 8.6**: 更新 `tests/test_e2e.py` — 端到端测试适配
  - 状态：✅ 已完成

- [x] **Task 8.7**: 更新 `tests/test_tool_set.py` — dispatch 新签名
  - 状态：✅ 已完成

- [x] **Task 8.8**: 更新其余测试文件（test_web_toolkit, test_userio 等）
  - 状态：✅ 已完成

### Phase 9: 构建验证 [✅ 已完成]

- [x] **Task 9.1**: 运行全部单元测试 `pytest` — 637 passed, 5 skipped
  - 状态：✅ 已完成

- [x] **Task 9.2**: 运行类型检查 `mypy src/mutagent` — 无新增错误（已有错误均为 pre-existing）
  - 状态：✅ 已完成

### Phase 10: mutbot 迁移文档 [✅ 已完成]

编写 mutbot 迁移指南，记录所有不兼容变更的影响和适配方案，作为 mutbot 独立适配的依据。

- [x] **Task 10.1**: 编写 `mutbot/docs/specifications/refactor-message-model-migration.md`
  - [x] 列出所有不兼容变更对 mutbot 的具体影响（按模块）
  - [x] session_impl.py — Message 构造、ToolCall/ToolResult 反序列化、`_rebuild_llm_messages()` 重写方案
  - [x] agent_bridge.py — Agent 字段重命名、StreamEvent 字段变更、飞行中状态追踪改造（ToolUseBlock 原地更新 + 中断恢复）
  - [x] serializers.py — Message/ToolCall/ToolResult/Response/StreamEvent 序列化适配
  - [x] setup_provider.py — Response/Message 构造适配
  - [x] 已持久化会话数据的兼容方案（chat_messages 格式不变，`_rebuild_llm_messages()` 适配新 Message 构造即可）
  - [x] 测试文件更新清单
  - 状态：✅ 已完成

---

## 实施说明

### 不兼容变更

本次实施是**破坏性重构**，旧类型（ToolCall、ToolResult）完全移除，不保留兼容层：
- `mutagent.messages.ToolCall` → 移除（被 `ToolUseBlock` 替代）
- `mutagent.messages.ToolResult` → 移除（结果合并到 `ToolUseBlock`）
- `mutagent.messages.Message` → 完全重写（content:str → blocks:list[ContentBlock]）
- `Agent.client` → `Agent.llm`
- `Agent.tool_set` → `Agent.tools`
- `Agent.system_prompt` / `Agent.messages` / `Agent.max_tool_rounds` → 移除
- `LLMProvider.send()` / `LLMClient.send_message()` 的 `system_prompt` 参数 → `prompts`

### mutbot 影响

mutbot 是 mutagent 的上层消费者，本次变更会导致 mutbot 编译失败。mutbot 适配作为独立任务处理，不在本规范范围内。
