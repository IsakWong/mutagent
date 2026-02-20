# 用户交互层抽象（UI Layer）设计规范

**状态**：✅ 已完成
**日期**：2026-02-19
**类型**：功能设计

## 1. 背景

### 1.1 问题来源

在一次会话中，Agent 尝试通过 `@impl(App.handle_stream_event)` 热替换核心输出处理器来支持 markdown 渲染，结果把自己搞瘫痪了。这暴露了两个架构问题：

1. **输出管线不可插拔**：想改变输出渲染方式，唯一途径是替换整个 `handle_stream_event`——一个关键控制路径方法
2. **缺少交互层抽象**：`App` 类同时承担了引导启动、组件装配、输入采集、输出渲染、会话循环等职责，没有独立的"用户交互"概念

### 1.2 愿景

mutagent 的 Agent 与用户可以通过多种方式交流：

| 模式 | 描述 | 场景 |
|------|------|------|
| 基础终端 | 当前实现，纯文本 print | 最简部署、脚本集成 |
| 增强终端 | markdown 渲染、语法高亮 | 日常开发使用 |
| TUI | 类似 claude code 的终端界面 | 复杂交互、多面板 |
| Web | 浏览器界面 | 丰富展示、远程访问 |
| 语音 | 语音输入/输出 | 多种模式的叠加层 |

按照 mutagent 的设计思路，这些能力应该是**可演化的**——Agent 可以在运行时创建新的 UI 模式或增强现有模式。当用户请求"以某种形式展示界面"时，Agent 应该能通过定义新模块来实现。

### 1.3 目标

- 在 mutagent 中定义"用户交互"这个一等概念
- 将输入采集和输出渲染从 `App` 中解耦，形成独立可替换的层
- 支持 Agent 通过 `define_module` 演化交互能力（如添加 markdown 渲染），而不需要替换核心控制路径
- 为未来的 TUI、Web、语音等模式建立扩展基础

### 1.4 已确认的设计决策

| 决策 | 结论 |
|------|------|
| 交互层命名 | `UserIO`，文件 `src/mutagent/userio.py` |
| Content 数据类位置 | `messages.py` |
| 兼容性策略 | 不需要兼容，直接迁移（正式发布前不需要兼容） |
| 扩展块命名空间 | 使用 `mutagent:` 前缀 |
| 块类型扩展性 | 内置类型 + 自定义类型（降级为代码块） |
| 可选组件分类 | `extras`，目录 `src/mutagent/extras/`，与 pip optional-dependencies 对齐 |

## 2. 设计方案

### 2.1 现状分析

当前 `App` 类中与用户交互相关的方法：

```
App
├── input_stream()           → 生成 InputEvent（stdin readline）
├── handle_stream_event()    → 消费 StreamEvent（print to stdout）
├── confirm_exit()           → 退出确认（input prompt）
└── run()                    → 会话主循环（绑定了输入→Agent→输出的完整流程）
```

问题：这些方法分散在 `App` 中，缺少统一抽象。Agent 想改变输出方式必须覆盖 `handle_stream_event`，而这个方法在 `run()` 的热路径上——替换它等于"给飞行中的飞机换引擎"。

### 2.2 架构层：UserIO 声明

引入 `UserIO` 声明类（`src/mutagent/userio.py`），负责 Agent 输出的渲染和用户输入的采集。`App` 持有 `UserIO` 实例并委托交互职责。

`App` 现有的 `handle_stream_event`、`input_stream`、`confirm_exit` 直接迁移到 `UserIO`，从 `App` 上移除。

### 2.3 交互协议层：Markdown 块扩展

**核心思路**：LLM 在文本输出中使用标准 markdown 围栏代码块（fenced code block），通过 `mutagent:` 前缀的语言标记标识扩展块。UserIO 解析这些块并渲染为对应的交互元素。

格式就是标准的 markdown 围栏代码块，不需要额外包装。例如 LLM 输出中直接写：

    ```mutagent:tasks
    - [x] 分析代码库结构
    - [~] 实现交互层
    - [ ] 编写测试
    ```

这在任何 markdown 渲染器中都会被渲染为一个带 `mutagent:tasks` 语言标签的代码块——内容可读，不会出错。支持扩展块的 UserIO 则会将其渲染为更丰富的交互组件。

这一方式的优势：
- LLM 天然擅长生成 markdown，不需要学习额外的工具 API
- 同一份输出在不同交互模式下自动适配（渐进增强）
- 不需要额外的工具调用开销
- 对话记录本身就是合法的 markdown 文件，可以被任何 markdown 渲染器查看
- 未来如果做专门的文件展示（如会话回放），这些扩展块同样可用

#### 扩展块范例

**单向展示型**

| 块类型 | 用途 | 基础终端 | 增强终端 | TUI/Web |
|--------|------|---------|---------|---------|
| `mutagent:tasks` | 任务列表 | 纯文本 | 彩色标记 ✅⏳◻ | 可折叠面板 |
| `mutagent:status` | 状态信息 | 纯文本 | 高亮状态栏 | 固定状态条/卡片 |
| `mutagent:code` | 代码（带元信息） | 普通代码块 | 语法高亮 + 文件标签 | 编辑器组件 |
| `mutagent:agents` | Agent 列表 | 纯文本表格 | 彩色表格 | 实时状态面板 |
| `mutagent:thinking` | 思考过程 | 原样输出 | 灰色折叠 | 可展开/折叠区域 |

**双向交互型**（见 2.4 节详细讨论）

| 块类型 | 用途 | 基础终端 | 增强终端 | TUI/Web |
|--------|------|---------|---------|---------|
| `mutagent:ask` | 提问/选择 | 打印问题，等待文本输入 | 带选项的提示 | 选择组件/表单 |
| `mutagent:confirm` | 确认操作 | y/n 提示 | 高亮确认框 | 对话框 |

### 2.4 双向交互：块驱动的用户输入

扩展块不仅可以展示内容，还可以**定义交互方式**。块格式本身描述了交互的结构，UserIO 根据自身能力选择最合适的交互形式，捕获用户响应后作为下一条输入消息发送给 Agent。

#### 工作流程

```
1. LLM 输出包含一个 mutagent:ask 块
2. LLM 回合结束（end_turn）
3. UserIO 渲染该块为交互组件
4. 用户通过交互组件提交响应
5. 响应作为新的 InputEvent 发送给 Agent
6. Agent 在下一回合收到用户响应，继续处理
```

这个流程完全在现有的回合制模型内工作，不需要新的协议。关键区别在于 UserIO 如何**渲染**问题和**采集**回答：

| 交互模式 | mutagent:ask 的渲染 | 用户如何回答 |
|---------|---------------------|-------------|
| 基础终端 | 打印问题和选项文本 | 用户在 `>` 提示符后输入文本 |
| 增强终端 | 带编号的彩色选项列表 | 用户输入编号或文本 |
| TUI | 可选择的列表组件 | 键盘上下选择、回车确认 |
| Web | 单选/多选表单组件 | 点击选择、提交按钮 |

#### 与当前 SDD 工作流的关系

当前的 SDD 迭代过程：LLM 在文档中写问题 → 用户编辑文档回答 → LLM 重新读取整个文档寻找修改。

如果有 `mutagent:ask` 块支持，这个过程可以简化为：LLM 输出包含问题的块 → 用户直接在交互界面中回答 → 答案作为输入消息传递给 LLM。无需文件中转，LLM 无需重新扫描文档找变更。

但两种模式不矛盾：markdown 块格式的优势在于它**同时适用于实时交互和文件场景**。同一个 `mutagent:ask` 块在实时会话中是交互组件，在保存的会话记录中仍然是可读的 Q&A 内容。

### 2.5 render_event 与 present 在新模型中的角色

#### render_event：LLM 文本流的解析-渲染管线

LLM 的所有文本输出通过 `text_delta → render_event` 路径。render_event 内部完成：
1. 普通文本：直接渲染（或 markdown 渲染）
2. 检测到 `mutagent:xxx` 块：委托给对应的块处理器（见 2.6）
3. 非文本事件（tool_exec_start/end、error 等）：按事件类型渲染

#### present：非 LLM 来源的旁路输出

有了 markdown 块扩展，LLM 不需要工具就能展示结构化内容。present() 的角色是**系统级旁路输出**——用于非 LLM 来源的内容：

| | render_event | present |
|--|-------------|---------|
| 调用者 | Agent 主循环（处理 StreamEvent） | 系统组件、工具实现、其他 Agent |
| 数据来源 | LLM 文本流 | 非 LLM 来源 |
| 内容 | 流式文本片段（需要解析） | 完整的结构化内容块（Content） |
| 典型场景 | 对话主流 | Agent 状态变化、工具副作用、Sub-Agent 输出 |

### 2.6 块处理器架构

每一种扩展块类型对应一个**块处理器**（BlockHandler），负责该类型块的解析、流式渲染和交互行为。

#### 职责划分

```
UserIO
├── render_event()       — 流式解析器：检测块边界，分发给对应 BlockHandler
├── present()            — 旁路输出：接收 Content，分发给对应 BlockHandler
├── read_input()         — 输入采集
└── block_handlers       — BlockHandler 注册表（type → handler）

BlockHandler
├── on_start(metadata)   — 块开始时调用（已知块类型和属性）
├── on_line(text)        — 块内每行内容到达时调用（流式渲染）
├── on_end()             — 块结束时调用（最终渲染）
└── render(content)      — 直接渲染完整 Content（present 路径）
```

UserIO 的 render_event 是流式解析器 + 路由器，它不需要了解每种块类型的具体渲染逻辑。具体的渲染、缓冲策略、交互行为都由 BlockHandler 决定。

#### 流式策略由 BlockHandler 自行控制

不同的 BlockHandler 自行决定流式行为：

| 块处理器 | on_line 行为 | on_end 行为 |
|---------|-------------|------------|
| TasksHandler | 每行立即渲染一个任务项 | 无额外操作 |
| StatusHandler | 缓冲 | 一次性渲染状态栏 |
| CodeHandler | 逐行渲染（增量高亮） | 无额外操作 |
| ThinkingHandler | 实时流式显示 | 无额外操作 |
| AskHandler | 缓冲 | 渲染交互组件，等待用户响应 |

#### 与 mutagent 演化机制的关系

BlockHandler 可以设计为 Declaration 子类。这样：
- 内置块处理器在 `builtins/` 中提供默认实现
- Agent 可以通过 `define_module` + `@impl` 覆盖某个块处理器的方法（如增强 CodeHandler 的渲染效果）
- Agent 也可以定义全新的 BlockHandler 子类，自动注册为新块类型
- 这与 Toolkit 的自动发现机制一致：定义了就能用，无需手动注册

#### 检测状态机

```
NORMAL ──检测到块开始标记──→ BLOCK_HEAD ──换行──→ IN_BLOCK ──检测到块结束──→ FLUSH
  │                           │                     │                        │
  └──直接渲染                 └──查找 handler        └──调用 handler.on_line  └──调用 handler.on_end
```

块类型在 BLOCK_HEAD 阶段就已知，UserIO 从注册表中查找对应的 BlockHandler。如果没有找到（不认识的类型），降级为默认处理器（直接输出文本）。

**基础终端的简化路径**：基础终端可以跳过整个解析逻辑，直接打印所有文本。块标记作为 markdown 代码块原样输出，可读性不受影响。

### 2.7 多 Agent 并发与多流模型

mutagent 支持多个 Agent 同时运行（主 Agent + Sub-Agents）。这意味着 UserIO 面对的不是单一 LLM 文本流，而是**多个并发流**。

#### 数据流模型

```
Agent-main  ──stream──┐
Agent-coder ──stream──┼──→ UserIO ──→ 渲染到对应区域
Agent-search──stream──┤
系统事件    ──present──┘
```

#### 各交互模式下的多 Agent 处理

| 交互模式 | 输出呈现 | 流式策略 |
|---------|---------|---------|
| 基础终端 | 每段输出带 Agent 名称前缀 | 主 Agent 流式输出；其他 Agent 缓冲后整段输出（或当前活跃 Agent 流式，其余缓冲） |
| 增强终端 | 同上，不同 Agent 用不同颜色区分 | 同基础终端 |
| TUI | 每个 Agent 一个面板/标签页 | 所有 Agent 各自独立流式输出到自己的面板 |
| Web | 多面板布局 | 所有 Agent 独立区域，实时更新 |

基础模式下的优先级：焦点 Agent（默认主 Agent）获得流式输出权，其他 Agent 的输出缓冲。当焦点 Agent 空闲而其他 Agent 正在工作时，焦点自动切换到活跃 Agent。

#### 多 Agent 的输入路由

类似多人聊天模型：
- **默认**：用户输入发送给主 Agent
- **@mention 语法**：用户输入 `@coder: 这个函数有 bug` 时，消息路由到 coder Agent
- **高级模式（TUI/Web）**：用户切换焦点面板即切换输入目标

#### 与 present() 的关系

多 Agent 场景增强了 present() 的重要性。Sub-Agent 的输出需要通过 present() 路由到正确的面板：

```
                      ┌─── 主面板：主 Agent 的 render_event()
                      │
UserIO ←── 数据源 ──┼─── Agent 面板：各 Sub-Agent 的输出（present）
                      │
                      ├─── 状态栏：系统状态 + mutagent:status 块
                      │
                      └─── 日志面板：LogStore 实时数据
```

### 2.8 Content 模型

**已确认**：放在 `messages.py` 中。

Content 作为 present() 和块解析的统一数据模型：

| 字段 | 类型 | 说明 |
|------|------|------|
| type | str | 块类型（tasks, status, code, ask, confirm, agents, thinking 等） |
| body | str | 内容主体（块内的原始文本） |
| target | str | 目标区域（空字符串 = 主面板），对应 `@target=xxx` |
| source | str | 来源标识（Agent 名称、系统组件名等） |
| metadata | dict | 附加属性（如 code 块的 `lang`, `file` 等） |

Content 的三个来源：
1. **render_event 解析**：从 LLM 文本流中检测到 `mutagent:xxx` 块 → 解析为 Content → 委托给 BlockHandler
2. **present() 直接创建**：系统组件或工具构造 Content → 委托给 BlockHandler
3. **Sub-Agent 输出**：Sub-Agent 的 StreamEvent 流 → 转换为 Content → 通过 present() 路由

### 2.9 核心与可选组件的分层

mutagent 提供增强终端、TUI、Web、语音等交互方式以及各种 BlockHandler，但这些不属于核心部件——它们有额外的外部依赖，可以不启动。需要为这类组件建立分类。

#### 当前源码结构

```
mutagent/
├── 核心声明层：agent.py, client.py, tools.py, config.py, main.py, messages.py
├── 核心实现层：builtins/       ← 始终加载
├── 核心工具集：toolkits/       ← 始终加载
├── 核心运行时：runtime/        ← 始终加载
└── CLI 工具  ：cli/
```

所有核心组件只依赖 `mutobj` 和 `requests`，这是 mutagent 的最小安装。

#### 新增 UserIO 后的分层

引入 UserIO 后，组件自然分为两层：

**核心层**（始终可用，零额外依赖）：
- `UserIO` 声明 + 基础终端实现（纯 print/input）
- 基础 BlockHandler 声明（on_start/on_line/on_end 接口）
- Content 数据模型

**可选层**（额外依赖，按需加载）：
- 增强终端 UserIO 实现（依赖 `rich`）
- TUI UserIO 实现（依赖 `textual` 或类似库）
- Web UserIO 实现（依赖 `fastapi`/`flask` 等）
- 语音集成（依赖语音库）
- 增强版 BlockHandler（如支持语法高亮的 CodeHandler）

#### 可选组件的分类：extras

**已确认**：使用 `extras` 作为可选组件的分类名。理由：与 Python 打包生态术语一致（pip optional-dependencies），语义清晰，简短好记，不暗示外部/第三方。

#### 可选组件的加载机制

无论叫什么名字，加载机制可以复用现有的 config `modules` 字段：

```json
{
  "modules": ["mutagent.extras.rich"]
}
```

或通过 pip extras 自动关联：
```
pip install mutagent[rich]    → 安装 rich 依赖，模块可用
pip install mutagent[tui]     → 安装 textual 依赖，模块可用
pip install mutagent[web]     → 安装 fastapi 依赖，模块可用
```

每个可选模块内部通过 `@impl` 覆盖核心声明的方法（如用 rich 版本替换基础终端的 UserIO 实现），或注册新的 BlockHandler。这与 mutagent 现有的演化机制完全一致。

#### 可选组件的目录结构

```
mutagent/
├── userio.py                        — UserIO 声明
├── builtins/                        — 核心实现（始终加载）
│   ├── userio_impl.py               — 基础终端实现
│   └── block_handlers.py            — 基础 BlockHandler
│
└── extras/                          — 可选实现（按需加载）
    ├── rich/                        — pip install mutagent[rich]
    │   ├── __init__.py              — 加载时自动注册 @impl
    │   ├── userio_impl.py           — rich 终端的 UserIO 实现
    │   └── block_handlers.py        — rich 版本的 BlockHandler
    ├── tui/                         — pip install mutagent[tui]
    └── web/                         — pip install mutagent[web]
```

## 3. 实施步骤清单

本设计涉及面较广，拆分为以下阶段逐步实施。每个阶段可作为独立子任务执行。

### 阶段一：核心声明与数据模型 [已完成]

- [x] **Task 1.1**: 创建 Content 数据类
  - [x] 在 `messages.py` 中添加 `Content` dataclass（type, body, target, source, metadata）
  - [x] 添加单元测试
  - 状态：✅ 已完成

- [x] **Task 1.2**: 创建 UserIO 声明
  - [x] 创建 `src/mutagent/userio.py`，定义 `UserIO(Declaration)` 类
  - [x] 声明方法：`render_event(event)`、`present(content)`、`read_input()`、`confirm_exit()`、`input_stream()`
  - [x] 声明属性：`block_handlers`（BlockHandler 注册表）
  - [x] 添加单元测试
  - 状态：✅ 已完成

- [x] **Task 1.3**: 创建 BlockHandler 声明
  - [x] 在 `userio.py` 中定义 `BlockHandler(Declaration)` 基类
  - [x] 声明方法：`on_start(metadata)`、`on_line(text)`、`on_end()`、`render(content)`
  - 状态：✅ 已完成

### 阶段二：基础终端实现与迁移 [已完成]

- [x] **Task 2.1**: 实现基础终端 UserIO
  - [x] 创建 `builtins/userio_impl.py`
  - [x] 迁移 `main_impl.py` 中的 `handle_stream_event` → `UserIO.render_event`
  - [x] 迁移 `input_stream` → `UserIO.input_stream`
  - [x] 迁移 `confirm_exit` → `UserIO.confirm_exit`
  - [x] 实现基础的流式文本直出（含块检测状态机）
  - 状态：✅ 已完成

- [x] **Task 2.2**: 修改 App 集成 UserIO
  - [x] `App` 添加 `userio: UserIO` 属性
  - [x] `setup_agent` 中创建 UserIO 实例（含自动发现 BlockHandler）
  - [x] `run` 中通过 `self.userio` 委托交互
  - [x] 从 `App` 上移除 `handle_stream_event`、`input_stream`、`confirm_exit`
  - [x] 确保现有功能不受影响（394 个测试全部通过）
  - 状态：✅ 已完成

### 阶段三：块解析与 BlockHandler 机制 [已完成]

- [x] **Task 3.1**: 实现流式块检测状态机
  - [x] 在 `render_event` 中实现 NORMAL → IN_BLOCK 状态机（含行缓冲和流式输出）
  - [x] 解析 `mutagent:` 前缀的围栏代码块
  - [x] 未知块类型降级为直接输出
  - [x] 添加单元测试（分片传输、空块、块间文本、turn_done 重置等边界情况）
  - 状态：✅ 已完成

- [x] **Task 3.2**: 实现 BlockHandler 自动发现机制
  - [x] 通过 `discover_block_handlers()` 扫描 mutobj 类注册表
  - [x] BlockHandler 子类通过 `_BLOCK_TYPE` 类常量标识块类型
  - [x] UserIO 从注册表中按 type 查找 handler
  - [x] 添加单元测试
  - 状态：✅ 已完成

- [x] **Task 3.3**: 实现内置 BlockHandler
  - [x] DefaultHandler（代码块格式输出，用于 fallback）
  - [x] TasksHandler（基础终端：逐行输出）
  - [x] StatusHandler（基础终端：缓冲后一次性输出）
  - [x] CodeHandler（基础终端：标准代码块输出）
  - [x] ThinkingHandler（基础终端：流式输出）
  - 状态：✅ 已完成

### 阶段四：双向交互块 [✅ 已完成]

- [x] **Task 4.1**: 实现 AskHandler
  - [x] 基础终端：缓冲块内容，on_end 时打印问题和选项，等待用户文本输入
  - [x] 用户响应作为 InputEvent 返回
  - [x] 添加单元测试
  - 状态：✅ 已完成（详见 `feature-interactive-blocks.md`）

- [x] **Task 4.2**: 实现 ConfirmHandler
  - [x] 基础终端：y/n 提示
  - [x] 用户响应作为 InputEvent 返回
  - [x] 添加单元测试
  - 状态：✅ 已完成（详见 `feature-interactive-blocks.md`）

### 阶段五：多 Agent 支持 [暂不开始]

- [ ] **Task 5.1**: 实现 present() 路由
  - [ ] 接收 Content 并根据 source/target 路由到正确的 BlockHandler
  - [ ] 基础终端：带 Agent 名称前缀输出
  - [ ] 添加单元测试
  - 状态：⏸️ 待开始

- [ ] **Task 5.2**: 实现多 Agent 输入路由
  - [ ] 基础终端：解析 `@agent_name:` 前缀进行消息路由
  - [ ] 默认路由到主 Agent
  - [ ] 添加单元测试
  - 状态：⏸️ 待开始

- [ ] **Task 5.3**: 实现焦点 Agent 流式策略
  - [ ] 焦点 Agent 流式输出，其他 Agent 缓冲
  - [ ] 焦点 Agent 空闲时自动切换到活跃 Agent
  - 状态：⏸️ 待开始

### 阶段六：extras 基础设施 [✅ 已完成]

- [x] **Task 6.1**: 创建 extras 目录结构
  - [x] 创建 `src/mutagent/extras/` 作为命名空间包
  - [x] 创建 `src/mutagent/extras/rich/` 预留目录
  - [x] 更新 `pyproject.toml` 添加 `[project.optional-dependencies]`（rich, tui, web）
  - 状态：✅ 已完成

- [x] **Task 6.2**: 实现 rich 增强终端（示例 extras 模块）
  - [x] 创建 `extras/rich/` 模块
  - [x] 通过 `@impl` 覆盖 UserIO 的 render_event（添加 markdown 渲染）
  - [x] 实现 rich 版 BlockHandler（语法高亮、彩色标记等）
  - [x] 添加单元测试
  - 状态：✅ 已完成
