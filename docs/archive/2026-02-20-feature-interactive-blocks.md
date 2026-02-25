# 双向交互块（Interactive Blocks）设计规范

**状态**：✅ 已完成
**日期**：2026-02-20
**类型**：功能设计
**上游文档**：`docs/specifications/feature-ui-layer.md` 阶段四

## 1. 背景

### 1.1 问题来源

`feature-ui-layer.md` 阶段一至三已完成：UserIO 声明、基础终端实现、块解析状态机、BlockHandler 自动发现、5 个内置 Handler（tasks/status/code/thinking/default）、extras/rich 增强终端。

当前所有扩展块都是**单向展示型**——LLM 输出结构化内容，UserIO 渲染它，流程结束。缺少**双向交互**能力：LLM 向用户提问、用户回答、答案返回 LLM 的闭环。

### 1.2 设计原则

mutagent 的交互分层理念：

| 层级 | 输入能力 | 输出能力 | 典型场景 |
|------|---------|---------|---------|
| **纯文本流** | 文本输入（stdin / 语音转文本） | 文本输出（尽量不修改格式直出） | 最简部署、脚本集成、语音交互 |
| **增强终端** | 同上 + 语法高亮提示等 | markdown 渲染、语法高亮 | 日常开发 |
| **TUI** | 同上 + 组件交互（选择、确认、点击） | 可交互面板、可展开区域 | 复杂交互 |
| **Web** | 同上 + 富交互（表单、拖拽） | 完整 GUI | 远程访问 |

**关键约束**：纯文本流模式下不依赖任何终端扩展功能。用户唯一能做的就是输入文本。这确保了语音交互场景（语音转文本 → 纯文本输入）的可用性。

### 1.3 目标

- 实现 `AskHandler` 和 `ConfirmHandler` 两个交互块处理器
- 定义**被动交互点**（Passive Interaction Point）模型
- 扩展 `InputEvent` 增加 `data: dict` 字段
- 基础终端：交互块渲染为纯文本，不改变输入行为
- 识别"交互数据如何到达 LLM"这一扩展点，为 TUI/Web 预留

### 1.4 已确认的设计决策

| 决策 | 结论 | 来源 |
|------|------|------|
| InputEvent 扩展方式 | 增加 `data: dict` 字段，保持灵活 | D1 |
| 基础终端渲染风格 | 纯文本原样输出，不做任何修改 | D3 |
| PendingInteraction 存储 | 列表存储，ID 作为元素内的字段 | D4 |
| 文档拆分 | 不拆分，内置实现简单 | D5 |
| format_input | 不引入。InputEvent 是原始信息，不在 UserIO 层格式化 | D6/D7 |
| confirm result 类型 | 不限定，由 UserIO 实现决定 | D8 |
| 扩展点③时机 | 本次只实现 ①②，③ 推迟到 TUI 阶段 | D9 |
| Handler 输入产生能力 | TUI 阶段设计问题，本次记录备忘 | D10 |

## 2. 设计方案

### 2.1 架构分层：三个独立关注点

```
┌─────────────────────────────────────────────────────────┐
│ ① 输出：交互块的渲染                     【本次实施】       │
│    render_event → BlockHandler 渲染 + 注册 PendingInteraction │
├─────────────────────────────────────────────────────────┤
│ ② 输入：InputEvent 的生产                【本次实施】       │
│    input_stream → 收集用户文本 + pending interactions → yield │
│    InputEvent 是原始信息，text + data                       │
├─────────────────────────────────────────────────────────┤
│ ③ 转换：InputEvent → LLM Message         【TUI 阶段】      │
│    Agent.run 当前只使用 event.text                          │
│    未来需要扩展点处理 data 中的交互结果、附件等                  │
└─────────────────────────────────────────────────────────┘
```

### 2.2 核心模型：被动交互点

```
LLM 输出 mutagent:ask 块
    ↓
render_event → Handler 渲染（纯文本逐行直出）
             → Handler 注册 PendingInteraction 到 UserIO 列表
    ↓
turn_done
    ↓
用户输入文本
    ↓
input_stream：
    1. read_input() → 用户文本
    2. 取走 _pending_interactions 列表（清空）
    3. 分配 id（列表 index）
    4. yield InputEvent(text=用户文本, data={'interactions': [...]})
    ↓
Agent.run → self.messages.append(Message(role="user", content=event.text))
```

### 2.3 InputEvent 扩展

增加 `data` 字段：

```python
@dataclass
class InputEvent:
    type: str
    text: str = ""
    data: dict = field(default_factory=dict)
```

**语义**：`text` 始终是用户原始文本输入。`data` 是结构化数据，不被任何层修改。

`data` 中的约定 key：

| key | 类型 | 含义 |
|-----|------|------|
| `interactions` | `list[dict]` | 交互块结果列表 |
| `attachments` | `list[dict]` | 附件（未来扩展） |

每个 interaction dict：
```python
{
    'id': 0,                     # 交互点序号（收集时分配）
    'type': 'ask',               # 'ask' 或 'confirm'
    'question': '问题文本',
    'options': ['A', 'B', 'C'],  # 选项列表（confirm 为空）
    'result': None,              # 用户操作结果（None = 未交互）
}
```

### 2.4 PendingInteraction 生命周期

```
创建：Handler.on_end()
       → 设置 self._pending_interaction = {type, question, options, result: None}
       状态机 → _transfer_pending_interaction() 追加到 userio._pending_interactions

存续：等待用户输入期间
       纯文本：result 始终为 None
       TUI/Web（未来）：用户可交互更新 result

收集：input_stream 取走列表
       → 为每个 interaction 分配 id
       → 放入 InputEvent.data['interactions']
       → 清空 userio._pending_interactions

销毁：InputEvent yield 后，数据在 InputEvent.data 中，userio 上已清空
```

### 2.5 mutagent:ask 块格式

````
```mutagent:ask
问题文本（一行或多行）

- 选项 A 描述
- 选项 B 描述
- 选项 C 描述
```
````

**解析规则**：
- 以 `- ` 开头的行为选项行
- 选项行之前的非空行组成问题文本
- 空行忽略
- 选项列表可以为空（开放式问题）

**基础终端渲染**：逐行原样输出。

### 2.6 mutagent:confirm 块格式

````
```mutagent:confirm
确认消息文本（一行或多行）
```
````

**基础终端渲染**：逐行原样输出。

### 2.7 Handler 基础终端实现

AskHandler 和 ConfirmHandler 的基础终端行为：

- **on_start**：初始化 `_buffer = []`
- **on_line**：`print(text)` 直出 + 追加到 `_buffer`（渲染与缓冲并行）
- **on_end**：解析 `_buffer` 为结构化数据，设置 `self._pending_interaction`，清空 `_buffer`
- **render**：print body + 设置 `self._pending_interaction`（present 路径）

AskHandler.on_end 解析逻辑：
- 遍历缓冲行：`- ` 开头的行 → options，之前的非空行 → question
- 构建 `{type: 'ask', question, options, result: None}`

ConfirmHandler.on_end 解析逻辑：
- 所有非空行合并为 question
- 构建 `{type: 'confirm', question, options: [], result: None}`

### 2.8 Handler-to-UserIO 通信

**转移机制**：Handler 在自身设置 `_pending_interaction`，状态机代码转移到 UserIO 列表。

辅助函数 `_transfer_pending_interaction(userio, handler)`：
1. 检查 `getattr(handler, '_pending_interaction', None)`
2. 如果存在，追加到 `userio._pending_interactions` 列表
3. 清除 handler 上的 `_pending_interaction`

**调用位置**（3 处）：
1. `_process_complete_line`：`on_end()` 之后
2. `_reset_parse_state`：未关闭块的 `on_end()` 之后
3. `present`：`render()` 之后

### 2.9 input_stream 增强

在用户输入采集完成后、yield 之前，收集 pending interactions：

1. `read_input()` 获取用户文本（现有逻辑不变）
2. 取走 `_pending_interactions` 列表（如果存在且非空）
3. 为每个 interaction 分配 `id`（列表 index）
4. 构建 `InputEvent(type="user_message", text=text, data={'interactions': list})`
5. 如果无 pending interactions，`data` 保持空 dict（不放空列表）
6. yield InputEvent

### 2.10 扩展点③备忘：InputEvent → LLM Message

当前 Agent.run 只使用 `event.text`，不感知 `event.data`。

未来 TUI/Web 模式需要一个扩展点将交互结果传递给 LLM。已识别的设计约束：

1. InputEvent 不应被修改——它是原始信息
2. 转换逻辑应可扩展
3. 交互块 Handler 应有参与机会（Handler 了解交互语义）
4. 多模态未来（图片等需要非纯文本 Message）

可能的方向（TUI 阶段设计）：
- Handler 新增方法参与结果翻译
- Agent 新增 `prepare_message` 方法
- 两者结合

同时，TUI/Web 模式下 Handler 可能不仅是被动的交互点注册者，还可以是输入的主动产生者（如用户点击确认按钮但未打字）。这影响 input_stream 的模型演化，从"等待文本 → 附加交互结果"变为"等待任意输入事件"。

## 3. 实施步骤清单

### 阶段一：数据模型与基础设施 [✅ 已完成]

- [x] **Task 1.1**: InputEvent 增加 data 字段
  - [x] 在 `messages.py` 的 `InputEvent` dataclass 中添加 `data: dict = field(default_factory=dict)`
  - [x] 确认现有测试中 InputEvent 的构造兼容（data 有默认值，不影响已有调用）
  - 状态：✅ 已完成

- [x] **Task 1.2**: pending interaction 基础设施
  - [x] 在 `userio_impl.py` 中添加 `_transfer_pending_interaction(userio, handler)` 辅助函数
  - [x] 修改 `_process_complete_line`：`on_end()` 之后调用 `_transfer_pending_interaction`
  - [x] 修改 `_reset_parse_state`：未关闭块的 `on_end()` 之后调用 `_transfer_pending_interaction`
  - [x] 修改 `present`：`render()` 之后调用 `_transfer_pending_interaction`
  - 状态：✅ 已完成

### 阶段二：AskHandler 与 ConfirmHandler [✅ 已完成]

- [x] **Task 2.1**: 实现 AskHandler
  - [x] 在 `block_handlers.py` 中添加 `AskHandler(BlockHandler)` （`_BLOCK_TYPE = "ask"`）
  - [x] 实现 on_start（初始化 buffer）、on_line（print + buffer）、on_end（解析 + pending）、render（print + pending）
  - [x] 添加 `_parse_ask_block(lines)` 辅助函数（返回 question, options）
  - 状态：✅ 已完成

- [x] **Task 2.2**: 实现 ConfirmHandler
  - [x] 在 `block_handlers.py` 中添加 `ConfirmHandler(BlockHandler)` （`_BLOCK_TYPE = "confirm"`）
  - [x] 实现 on_start（初始化 buffer）、on_line（print + buffer）、on_end（解析 + pending）、render（print + pending）
  - 状态：✅ 已完成

### 阶段三：input_stream 增强 [✅ 已完成]

- [x] **Task 3.1**: input_stream 收集 pending interactions
  - [x] 在 `input_stream` 的 yield 之前，取走 `_pending_interactions` 列表
  - [x] 为每个 interaction 分配 `id`
  - [x] 构建 InputEvent 时传入 `data={'interactions': [...]}`（无 pending 时 data 保持空 dict）
  - [x] 确保 KeyboardInterrupt/EOFError 处理不受影响
  - 状态：✅ 已完成

### 阶段四：测试 [✅ 已完成]

- [x] **Task 4.1**: InputEvent.data 测试
  - [x] 默认 data 为空 dict
  - [x] 带 data 的 InputEvent 构造与相等性
  - [x] 现有 InputEvent 测试兼容性
  - 状态：✅ 已完成

- [x] **Task 4.2**: AskHandler 测试
  - [x] `_parse_ask_block`：标准格式、无选项、空块、多行问题、选项后有非选项行
  - [x] 流式渲染：on_line 逐行 print（捕获 stdout 验证）
  - [x] on_end 后 handler._pending_interaction 结构正确
  - [x] render() 路径：print body + 设置 pending
  - 状态：✅ 已完成

- [x] **Task 4.3**: ConfirmHandler 测试
  - [x] 标准格式、多行消息、空块
  - [x] on_end 后 handler._pending_interaction 结构正确
  - [x] render() 路径
  - 状态：✅ 已完成

- [x] **Task 4.4**: pending interaction 转移测试
  - [x] `_transfer_pending_interaction`：有 pending → 追加到 userio 列表
  - [x] `_transfer_pending_interaction`：无 pending → 不影响列表
  - [x] 多个 handler 多次转移 → 全部追加
  - [x] `_process_complete_line` 路径集成
  - [x] `_reset_parse_state` 路径集成（未关闭块）
  - [x] `present()` 路径集成
  - 状态：✅ 已完成

- [x] **Task 4.5**: input_stream 集成测试
  - [x] 有 pending interactions → InputEvent.data['interactions'] 包含所有交互
  - [x] 无 pending interactions → InputEvent.data 为空 dict
  - [x] 多个 pending → 全部收集，id 正确分配
  - [x] 收集后 userio._pending_interactions 已清空
  - [x] 正常输入行为不变（无 pending 时与原有行为一致）
  - 状态：✅ 已完成

- [x] **Task 4.6**: 端到端集成测试
  - [x] 完整流：text_delta(ask 块) → handler → pending → input_stream → InputEvent.data
  - [x] 与现有 5 个 Handler 兼容（tasks/status/code/thinking/default 不受影响）
  - [x] 现有全部测试通过
  - 状态：✅ 已完成

---

### 实施进度总结
- ✅ **阶段一：数据模型与基础设施** - 100% 完成 (2/2任务)
- ✅ **阶段二：AskHandler 与 ConfirmHandler** - 100% 完成 (2/2任务)
- ✅ **阶段三：input_stream 增强** - 100% 完成 (1/1任务)
- ✅ **阶段四：测试** - 100% 完成 (6/6任务)

**核心功能完成度：100%** (11/11任务)
**测试覆盖：105个 userio 测试全部通过（68 existing + 37 new），全套 487 测试通过**

## 4. 测试验证

### 单元测试
- [x] InputEvent.data 字段（6 个测试）
- [x] `_parse_ask_block` 辅助函数（6 个测试）
- [x] `_transfer_pending_interaction` 辅助函数（6 个测试）
- [x] AskHandler（on_start/on_line/on_end/render + pending 设置，6 个测试）
- [x] ConfirmHandler（on_start/on_line/on_end/render + pending 设置，6 个测试）
- 执行结果：37/37 通过

### 集成测试
- [x] 状态机 → Handler → pending 转移 → input_stream → InputEvent 完整链路
- [x] 与现有 Handler 兼容性
- [x] 现有测试全部通过（487 passed, 2 skipped）
- 执行结果：全部通过
