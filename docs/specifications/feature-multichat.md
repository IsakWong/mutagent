# 多人聊天设计

**状态**：📝 设计中
**日期**：2026-03-01
**类型**：功能设计

## 背景

mutagent Message 模型重构（`feature-message-model.md`）引入了 blocks 模型和 AgentContext。在讨论 mutbot 迁移时，Turn 边界的设计引出了更深层的架构问题：多人聊天、InputEvent 与 Message 的割裂、上下文选择策略。这些问题超出当前重构范围，单独记录以便后续展开。

### 当前状态

- mutagent agent.run() 已有隐式 turn 概念：收到 user_message → 处理 → yield `StreamEvent(type="turn_done")`
- mutbot 当前是单人模式，每条用户消息自动触发一轮 agent 处理
- 本次重构暂用最简方案（agent.run() 自动插入 TurnBlock），后续再重新设计

### 需要解决的问题

1. **Turn 边界标记**：Begin/End 如何设计、放在哪里、谁负责插入
2. **多人聊天**：不是每条用户消息都触发 agent，谁决定激活、Turn 从哪里开始
3. ~~**InputEvent 不是 Message**~~：已解决 — Message 替代 InputEvent（见 `refactor-agent-run.md` "一、输入统一"）
4. **上下文选择**：聊了很久才 @agent，不能把所有历史都给 LLM

---

## 核心设计分析

### 正交分解

Turn 相关问题可分解为五个正交关注点：

| 关注点 | 本质 | 归属 |
|--------|------|------|
| **消息存储** | 用户消息进入 context.messages | 立即，不延迟 |
| **Agent 激活** | 什么时候开始处理 | 应用层决定 |
| **Turn 标记** | 标记处理周期的起止 | 纯元数据 |
| **上下文选择** | LLM 该看哪些消息 | prepare_messages() |
| **消息合并** | 连续 user 消息 → 合并为一条 | Provider |

当前设计把这些混在一起。理想设计应各自独立。

### TurnStart 不含用户消息

关键洞察：**TurnStart 是位置标记，不是用户消息的一部分**。

- TurnStart 标记的是"从这个时间点开始，agent 在处理"
- 它不属于任何特定用户消息
- 它可以由消息触发（@mention），也可以由非消息触发（UI 按钮、定时器）

这意味着 TurnStart 应该是一个**独立的 marker Message**，而非嵌入某条用户消息的 blocks 中。

### 消息存储与激活的分离

设计原则：**消息立即存储，不延迟 yield**。激活通过 TurnStartBlock 表达。

```python
# 消息随时进入，立即存储（无 TurnStartBlock，只存不处理）
Message(role="user", blocks=[TextBlock("大家好")], sender="Alice")
Message(role="user", blocks=[TextBlock("@bot 总结")], sender="Carol")

# 激活 = 消息中包含 TurnStartBlock
Message(role="user", blocks=[TurnStartBlock(turn_id="t1"), TextBlock("@bot 总结")], sender="Carol")
# 或纯激活（UI 按钮触发）
Message(role="user", blocks=[TurnStartBlock(turn_id="t1")])
```

agent.run() 统一处理：
- 所有输入 Message → 存入 context.messages（立即）
- 包含 TurnStartBlock → 额外触发处理循环

---

## 多人聊天场景

### 场景 1：@mention 触发

```
Alice: "大家好"              → Message 存入 context.messages
Bob:   "今天开会吗"          → Message 存入 context.messages
Carol: "@bot 总结今天任务"    → Message 含 TurnStartBlock，存入 + 触发处理

context.messages:
  user(Alice): [TextBlock("大家好")]
  user(Bob):   [TextBlock("今天开会吗")]
  user(Carol): [TurnStartBlock(t1), TextBlock("@bot 总结今天任务")]
  assistant:   [TextBlock("好的..."), TurnEndBlock(t1)]
```

### 场景 2：非消息触发（UI 按钮）

```
Alice: "数据准备好了"    → Message 存入
Bob:   "我也传了"        → Message 存入
[用户点击 "让 bot 分析"]  → Message(blocks=[TurnStartBlock(t2)]) 存入 + 触发

context.messages:
  user(Alice): [TextBlock("数据准备好了")]
  user(Bob):   [TextBlock("我也传了")]
  user:        [TurnStartBlock(t2)]              ← 纯激活，无文本内容
  assistant:   [TextBlock("分析结果..."), TurnEndBlock(t2)]
```

### 场景 3：长对话后才触发

```
context.messages:
  user: [TextBlock("...")]     ← 第 1 条
  ...
  user: [TextBlock("...")]     ← 第 97 条
  user: [TextBlock("@bot 帮忙")]
  user: [TurnStartBlock(t1)]

→ prepare_messages() 不发全部 97 条
→ 以 TurnStartBlock 为参考，往前取 N 条（或按 token 预算）
→ LLM 看到适量的上下文
```

---

## 设计提案：Message 驱动的激活

### 激活机制

InputEvent 已被 Message 替代（见 `refactor-agent-run.md`）。激活通过 TurnStartBlock 表达：

```python
# 纯消息（只存储）
Message(role="user", blocks=[TextBlock("hello")], sender="Alice")

# 激活（存储 + 触发处理）
Message(role="user", blocks=[TurnStartBlock(turn_id="t1"), TextBlock("@bot 帮忙")], sender="Carol")

# 纯激活（UI 按钮，无文本）
Message(role="user", blocks=[TurnStartBlock(turn_id="t1")])
```

### agent.run() 行为

```python
async for msg in input_stream:  # AsyncIterator[Message]
    self.context.messages.append(msg)

    if any(isinstance(b, TurnStartBlock) for b in msg.blocks):
        turn_id = next(b.turn_id for b in msg.blocks if isinstance(b, TurnStartBlock))
        # 开始处理
        t0 = time.time()
        async for se in self._process_turn():
            yield se
        # 结束
        self.context.messages[-1].blocks.append(
            TurnEndBlock(turn_id=turn_id, duration=time.time() - t0)
        )
        yield StreamEvent(type="turn_done")
```

### 单人模式兼容

bridge 每条消息自动附带 TurnStartBlock，行为和现在一样：

```python
# Bridge 自动生成
msg = Message(role="user", blocks=[
    TurnStartBlock(turn_id=uuid4().hex),
    TextBlock(text=text),
], sender="user")
await input_queue.put(msg)
```

---

## TurnStartBlock 作为 marker Message

### Provider 处理

TurnStartBlock 的 marker Message 是 `role="user"`，只含 TurnStartBlock 无文本。Provider 处理链：

1. prepare_messages() 返回消息列表
2. Provider 转换时忽略 TurnStartBlock（不认识的 block type）
3. 忽略后为空的 message 被跳过或与相邻 user message 合并
4. 连续 user messages 由 Provider 合并（`_merge_consecutive_openai` 已有此逻辑）
5. LLM 看到干净的 user/assistant 交替

```
context.messages:                      LLM 看到：
  user(Carol): "总结"                    user: "总结"
  user: [TurnStartBlock(t1)]      →     assistant: "好的..."
  assistant: "好的..."
```

### 需要验证

- Provider 忽略 TurnBlock 后，空 message 的合并行为是否正确
- Anthropic 要求非空 content，空 user message 需被跳过

---

## InputEvent → Message 统一（已决策）

### 决策

**方案 C：Message 替代 InputEvent**。详细方案选型和理由见 `refactor-agent-run.md` "一、输入统一"。

### 方案分析记录

比较了四种方案：

| 方案 | 思路 | 结论 |
|------|------|------|
| A: InputEvent 携带 blocks | `data.blocks = [...]` | 排除：绕开 ContentBlock 体系搞平行序列化 |
| D: data 扩展 | `data.images = [...]` | 排除：每种模态一套约定，不可持续 |
| B: InputEvent 携带 Message | `InputEvent.message = Message(...)` | 可行，信息完整性与 C 等效 |
| **C: Message 替代 InputEvent** | 输入流直接是 Message | **采用** |

**B vs C 的关键取舍**：

- B 保留 InputEvent 作为事件信封，显式 `type` 字段做事件分发，更传统（事件驱动模式）
- C 删除 InputEvent，事件类型由 block 组合表达（有 TurnStartBlock = 激活），概念更少
- 两者在信息完整性和类型安全上等效
- 选 C 因为：概念数最少（一个类 vs 两个类），且激活信号通过 ContentBlock 子类表达，契合 mutobj "能力 = Declaration 子类" 的理念

### 原始问题

~~当前 InputEvent 定义只支持纯文本（`text: str`），多模态消息（图片、PDF）无法通过 InputEvent 传入。~~ → 已解决：输入流直接是 Message，天然支持多模态 blocks。

---

## 上下文选择策略

### 与 Turn 的关系

TurnStartBlock 为 prepare_messages() 提供了一个天然的参考点：

```
"给 LLM 的上下文 = TurnStartBlock 之后的消息 + 之前 N 条（或按 token 预算）"
```

这避免了"聊了 100 条才 @agent，全部发给 LLM"的问题。

### 策略示例

| 策略 | 描述 |
|------|------|
| 全量 | 发送所有消息（当前默认，单人模式够用） |
| Turn 窗口 | TurnStart 前 N 条 + Turn 内全部 |
| Token 预算 | 从 TurnStart 往前回溯，直到填满 token 预算 |
| 语义过滤 | 只包含与触发消息相关的历史 |

这些策略在 `feature-context-management.md` 中进一步设计。

---

## 整体架构

```
                 应用层（mutbot）                mutagent
                 ─────────────                  ─────────
用户消息 ──→ Message(blocks=[TextBlock(...)]) ──→ 存入 context.messages（立即）
    │
    ├─ @mention / UI / 意图分类
    │
    └──→ Message(blocks=[TurnStartBlock(...)])  ──→ 存入 + 触发处理
                                      │
                                      ├─ prepare_messages() 选择上下文
                                      ├─ step() → LLM → tool → 循环
                                      └─ 完成 → TurnEndBlock
                                                │
                                                ↓
                                     Provider 合并消息 + 忽略 TurnBlock
                                     LLM 看到干净的 user/assistant 输入
```

---

## 关键参考

### 源码

- `mutagent/src/mutagent/messages.py` — Message/ContentBlock 定义（TurnBlock 待新增）
- `mutagent/src/mutagent/builtins/agent_impl.py:43-153` — agent.run() 主循环（turn 边界）
- `mutagent/src/mutagent/builtins/openai_provider.py:165` — `_merge_consecutive_openai()`（消息合并）
- `mutagent/src/mutagent/context.py` — AgentContext.prepare_messages()（上下文选择）

### 相关规范

- `mutagent/docs/specifications/feature-message-model.md` — Message 模型（ContentBlock 扩展机制、TurnBlock 示例）
- `mutagent/docs/specifications/feature-context-management.md` — Context 管理策略（上下文选择）
- `mutbot/docs/specifications/refactor-message-model-migration.md` — 当前重构（暂用简化 Turn 方案）
