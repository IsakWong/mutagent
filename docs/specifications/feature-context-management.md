# Context 管理策略 设计规范

**状态**：📝 设计中
**日期**：2026-03-01
**类型**：功能设计

## 背景

本规范定义 AgentContext 的 **管理策略**——如何处理 context 溢出、消息过滤、token 预算、跨会话记忆等。

数据模型（Message、ContentBlock、AgentContext 接口声明）见 `feature-message-model.md`。

---

## Context 溢出处理

`AgentContext.is_context_available()` 是框架提供的 **架构处理点**——当 context 接近或超出窗口时返回 False。

### 框架行为

Agent.run() 在每次 step 前检查 `is_context_available()`，context 不可用时 **不发送请求**，而是通知调用方（通过 StreamEvent 或异常）。

### 用户保有管理权

框架不自动压缩或裁剪。用户可选择：

- **手动管理** — 删除不需要的消息、清理工具结果
- **切换模型** — 使用更大 context window 的模型继续对话
- **应用层策略** — 通过 AgentContext 子类实现自动压缩/摘要

### 默认实现

基于 `get_context_percent()` 判断（如 >95% 返回 False）。`context_window=0`（未知）时始终返回 True。

---

## 消息过滤策略

`prepare_messages()` 的默认实现直接返回 messages 列表。应用层可通过 `@impl` 覆盖实现高级策略：

```
context.messages
    │
    ▼
过滤（retained 状态过滤，跳过标记为不发送的消息）
    │
    ▼
有效消息列表（新列表，不修改原始 messages）
```

### 管理性元数据

消息管理策略不在 Message 上，由 AgentContext 的实现层管理：

- **retained**（是否发送给 LLM）— `prepare_messages()` 内部过滤，通过 Message.id 引用
- **pinned**（关键消息标记，自动简化时优先保留）— 高级策略

实现方式：可用并行 dict（`{message_id: {"retained": True}}`）或 `mutobj.Extension[AgentContext]` 维护。

ToolUseBlock 合并了调用和结果，**不存在跨消息的原子性问题**——过滤一条含 ToolUseBlock 的消息，调用和结果自然一起移除。

---

## Token 用量追踪

默认实现维护 `last_input_tokens`、`last_output_tokens`、`total_tokens`。

- `get_context_used()` → `last_input_tokens + last_output_tokens`
- `get_context_percent()` → `get_context_used() / context_window * 100`

---

## 未来方向

### Context 压缩策略

当前架构已提供必要的扩展点：

- `prepare_messages()` — 过滤、截断
- `is_context_available()` — 容量信号
- `get_context_percent()` — 用量感知

压缩策略通过 AgentContext 子类或 `@impl` 实现，如：
- 基于 token 预算的自动截断
- 语义摘要（将旧消息压缩为摘要 Message）
- 滑动窗口（保留最近 N 轮）

### 跨会话持久记忆

**Extract-Store-Inject 模式**（参考 Pi.ai）：

1. **Extract** — 对话结束后，从 messages 中提取关键事实（用户偏好、项目知识、决策记录）
2. **Store** — 写入持久化存储（文件、数据库，由应用层决定）
3. **Inject** — 新会话开始时，将相关记忆注入 `context.prompts`（`label="memory"`）

当前架构已支持：`prompts` 是公开列表，应用层可在任何时刻动态增删 prompt。跨会话记忆 = 应用层在会话初始化时将持久化的记忆写入 prompts 列表。

### 对话规划

Agent 可维护一个 "对话策略" 层——不只是被动响应，而是主动规划对话走向（如引导用户澄清需求、分步完成复杂任务）。这通过 AgentContext 子类或独立的规划组件实现，不需要修改核心接口。

---

## 关键参考

### 源码

- `mutagent/src/mutagent/builtins/agent_impl.py` — Agent.run()/step() 实现（context 检查点）
- `mutagent/src/mutagent/client.py` — LLMClient（`context_window`）
- `mutbot/src/mutbot/web/agent_bridge.py` — 当前 token 追踪实现

### 相关规范

- `mutagent/docs/specifications/feature-message-model.md` — Message 模型与 AgentContext 接口声明
