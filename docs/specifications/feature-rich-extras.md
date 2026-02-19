# Rich 增强终端（extras.rich）设计规范

**状态**：✅ 已完成
**日期**：2026-02-20
**类型**：功能设计
**父规范**：`feature-ui-layer.md` 阶段六 Task 6.2

## 1. 背景

### 1.1 问题来源

UI Layer 的核心声明（UserIO、BlockHandler）和基础终端实现已完成（阶段一~三）。`extras/` 目录结构和 `pyproject.toml` 的 optional-dependencies 也已就位（阶段六 Task 6.1）。

现在需要实现第一个 extras 模块 `mutagent.extras.rich`，作为可选增强组件的示范：通过 `@impl` 覆盖基础终端的 UserIO 实现，提供 markdown 渲染、语法高亮、彩色标记等增强体验。

### 1.2 目标

- 创建 `mutagent.extras.rich` 模块，提供 rich 增强终端的 UserIO 实现
- 通过 `@impl` 机制覆盖基础终端的 `render_event` 和 `present`
- 提供 rich 版 BlockHandler（语法高亮、彩色任务列表、状态面板等）
- 验证 extras 模块的加载和覆盖机制正常工作
- 添加单元测试

### 1.3 已确认的设计约束

| 约束 | 说明 |
|------|------|
| 覆盖机制 | `@mutagent.impl()` 最后注册者胜出；extras 在 `load_config()` 中加载，早于 `setup_agent()` |
| BlockHandler 发现 | `discover_block_handlers()` 遍历 `_class_registry`，同名 `_BLOCK_TYPE` 后注册者覆盖先注册者 |
| rich 版本 | `rich>=13.0`（pyproject.toml 已配置），当前环境 v14.x |
| Console 管理 | 惰性创建，存储在 UserIO 实例上（通过 `object.__setattr__`） |
| 可选依赖 | `pip install mutagent[rich]`，模块缺失时 import 报明确错误 |
| extras 目录 | `src/mutagent/extras/` 为命名空间包（无 `__init__.py`），`extras/rich/` 为标准包 |
| 组件复用 | Rich 渲染组件（BlockHandler、渲染工具函数）须可被 TUI 等上层前端复用 |
| Console 抽象 | Console 是前端差异的抽象点：终端 Console 输出到 stdout，TUI Console 输出到 widget |

## 2. 设计方案

### 2.1 模块结构

```
src/mutagent/extras/rich/
├── __init__.py          — 导入守卫 + 子模块导入触发注册（standalone 入口）
├── userio_impl.py       — 【前端绑定层】@impl 覆盖 render_event / present
└── block_handlers.py    — 【渲染组件层】4 个 Rich BlockHandler（可被 TUI 复用）
```

**两层分离**：
- `block_handlers.py`（渲染组件层）：定义 Rich BlockHandler 类，仅依赖 rich 库和注入的 Console，不依赖 `@impl` 或 UserIO。TUI 可直接 `from mutagent.extras.rich.block_handlers import RichCodeHandler`。
- `userio_impl.py`（前端绑定层）：通过 `@impl` 将 rich 渲染绑定到 UserIO 方法，创建 stdout Console 并注入给 handler。仅用于独立 rich 终端模式。

### 2.2 模块加载流程

```
config.json: "modules": ["mutagent.extras.rich"]
    │
    ▼
main_impl.py load_config() → importlib.import_module("mutagent.extras.rich")
    │
    ▼
extras/rich/__init__.py
    ├── import rich（失败则 ImportError 提示 pip install mutagent[rich]）
    ├── import .userio_impl  → @impl(UserIO.render_event) 覆盖基础版
    │                        → @impl(UserIO.present) 覆盖基础版
    └── import .block_handlers → RichTasksHandler 等注册到 _class_registry
    │
    ▼
main_impl.py setup_agent() → discover_block_handlers()
    → 遍历 _class_registry，同名 _BLOCK_TYPE 后者覆盖前者
    → rich 版 handler 被选用
```

### 2.3 `__init__.py` 导入守卫

```python
"""mutagent.extras.rich -- Rich enhanced terminal for mutagent."""

try:
    import rich  # noqa: F401
except ImportError:
    raise ImportError(
        "mutagent.extras.rich requires the 'rich' package. "
        "Install it with: pip install mutagent[rich]"
    )

from . import userio_impl   # noqa: F401  -- register @impl overrides
from . import block_handlers  # noqa: F401  -- register rich BlockHandlers
```

### 2.4 Console 管理

所有 rich 组件共享同一个 Console 实例，惰性创建并存储在 UserIO 实例上：

```python
def _get_console(userio):
    """Get or lazily create the shared rich Console on a UserIO instance."""
    console = getattr(userio, '_console', None)
    if console is None:
        from rich.console import Console
        console = Console(highlight=False)
        object.__setattr__(userio, '_console', console)
    return console
```

BlockHandler 通过 `on_start()` 的 metadata 接收 Console 引用。`render_event` 在调用 `handler.on_start(metadata)` 前注入 `metadata['console']`。handler 保存该引用供后续 `on_line` / `on_end` 使用。

对于 `render()` 路径（`present()` 调用），handler 从 `content.metadata` 中取 `console`，若缺失则自行创建一个独立 Console 作为 fallback。

### 2.5 UserIO 覆盖：render_event

**核心策略**：复用与基础终端相同的流式块检测状态机结构，但将 `print()` 替换为 rich Console 输出，并增加 `text_buf` 字段用于累积普通文本以支持 Markdown 渲染。

#### 状态机扩展

在基础状态机的 `_parse_state` 基础上，增加 `text_buf` 字段：

```python
parse_state = {
    'state': 'NORMAL',     # NORMAL 或 IN_BLOCK
    'line_buf': '',        # 不完整行缓冲（与基础版相同）
    'handler': None,       # 当前 BlockHandler
    'block_type': '',      # 当前块类型
    'text_buf': '',        # 【新增】普通文本累积缓冲
}
```

#### 文本渲染流程

```
text_delta 到达
  ├── NORMAL 状态
  │   ├── 检测到 ```mutagent:type → flush text_buf 为 Markdown → 转入 IN_BLOCK
  │   ├── 包含 \n\n（段落边界）→ 按段落分割，flush 完成段落为 Markdown
  │   ├── 普通完整行 → 追加到 text_buf
  │   └── 不完整行 → 留在 line_buf（与基础版相同）
  │
  └── IN_BLOCK 状态（与基础版完全相同）
      ├── 检测到 ``` → handler.on_end() → 转回 NORMAL
      └── 其他行 → handler.on_line()

turn_done → flush text_buf 为 Markdown → 重置状态
```

`text_buf` 刷新时机：
1. 检测到 `mutagent:` 块开始时（块前的文本先渲染）
2. `text_buf` 中出现 `\n\n`（段落边界）时，刷新已完成的段落
3. `turn_done` 时（回合结束，渲染剩余文本）

刷新方式：`console.print(Markdown(text_buf))`（当 `text_buf` 非空时）

#### 事件渲染对比

| 事件类型 | 基础终端 | rich 终端 |
|---------|---------|----------|
| text_delta | `print(text, end="")` | 累积到 `text_buf`，段落边界刷新为 `Markdown` |
| tool_exec_start | `\n  [tool(args)]` | `console.print()` 带 `dim` 样式 |
| tool_exec_end (成功) | `  -> [done] result` | `console.print()` 带 `green` 样式 |
| tool_exec_end (失败) | `  -> [error] result` | `console.print()` 带 `red bold` 样式 |
| error | `print(..., file=stderr)` | `Console(stderr=True).print()` 带 `red bold` 样式 |
| turn_done | `_reset_parse_state` + `print()` | flush `text_buf` 为 Markdown + 重置状态 |

### 2.6 UserIO 覆盖：present

与基础版逻辑相同，但使用 rich Console 输出：

- 有 handler 时：在 `content.metadata` 中注入 `console` 引用，委托给 `handler.render(content)`
- 无 handler 时：`console.print()` 带 source 前缀的 `dim` 样式

### 2.7 Rich BlockHandler 实现

每个 rich handler 使用与内置 handler 相同的 `_BLOCK_TYPE`，在 `_class_registry` 中后注册，`discover_block_handlers()` 自动选用 rich 版本。

#### RichTasksHandler（`_BLOCK_TYPE = "tasks"`）

- **on_start**：从 metadata 中保存 console 引用
- **on_line**：逐行立即渲染，替换标记为彩色符号
  - `[x]` → 绿色 `[bold green]✅[/]`
  - `[~]` → 黄色 `[bold yellow]⏳[/]`
  - `[ ]` → 灰色 `[dim]◻[/]`
- **on_end**：无额外操作
- **render**：同 on_line 逻辑，逐行处理 `content.body`

#### RichStatusHandler（`_BLOCK_TYPE = "status"`）

- **on_start**：从 metadata 中保存 console 引用，初始化行缓冲区 `_buffer = []`
- **on_line**：追加到缓冲区
- **on_end**：用 `rich.panel.Panel` 渲染缓冲内容，标题 `"Status"`
- **render**：直接用 Panel 渲染 `content.body`

#### RichCodeHandler（`_BLOCK_TYPE = "code"`）

- **on_start**：从 metadata 中保存 console 引用，从 `metadata['raw']` 解析语言，初始化代码行缓冲 `_lines = []`
- **on_line**：追加到行缓冲（Syntax 需要完整代码才能正确高亮）
- **on_end**：用 `rich.syntax.Syntax` 渲染完整代码，支持语言检测和行号
- **render**：从 `content.metadata` 取语言，用 Syntax 渲染 `content.body`

#### RichThinkingHandler（`_BLOCK_TYPE = "thinking"`）

- **on_start**：从 metadata 中保存 console 引用
- **on_line**：逐行立即渲染，使用 `dim italic` 样式
- **on_end**：无额外操作
- **render**：整体渲染为 `dim italic`

#### Console 注入与 fallback

所有 handler 的 `on_start` 保存 `metadata.get('console')`，`render` 路径取 `content.metadata.get('console')`。

**正常路径**：Console 由 UserIO 的 `render_event` / `present` 实现注入，handler 不需要自己创建。这是 TUI 复用 rich handler 的关键——TUI 注入面向 widget 的 Console，handler 无感知。

**fallback 安全网**：若 Console 未注入（如 handler 被直接调用），创建一个 stdout Console。这仅作为防御性编程，正常流程不应触发：

```python
def _handler_console(self):
    """Get console from saved reference or create fallback."""
    console = getattr(self, '_console', None)
    if console is None:
        from rich.console import Console
        console = Console(highlight=False)
    return console
```

### 2.8 多前端复用架构

#### 2.8.1 问题：未来的多前端场景

mutagent 未来将支持多种前端同时连接同一 Agent：

| 场景 | 说明 |
|------|------|
| 仅 rich 终端 | 用户只在终端中使用增强输出（本 SDD 的核心场景） |
| TUI 模式 | textual 构建的终端界面，底层依赖 rich 库 |
| TUI + Web 同时 | 用户在 TUI 中交互，同时开放 Web 界面供远程查看 |
| 仅 Web | 纯 Web 界面，不使用 rich |

这要求 rich 模块不能是一个封闭的"终端方案"，而应该是一个**渲染组件库**，可被多种前端复用。

#### 2.8.2 分层：渲染组件 vs 前端绑定

将 `extras/rich/` 的内容分为两层：

```
extras/rich/
├── block_handlers.py    ← 渲染组件层：4 个 Rich BlockHandler
│                           - 只依赖 rich 库
│                           - 通过 Console 注入实现前端无关
│                           - 可被 TUI / 其他 rich-based 前端直接复用
│
└── userio_impl.py       ← 前端绑定层：@impl 覆盖 UserIO 方法
                            - 创建 stdout Console 并注入给 handler
                            - 实现 text_buf / Markdown 渲染等终端特有逻辑
                            - 仅在"独立 rich 终端"模式下使用
```

**关键设计原则**：BlockHandler 不创建自己的 Console，一切通过注入获得。这使得同一个 `RichCodeHandler` 在终端中输出到 stdout，在 TUI 中输出到 textual widget，仅凭传入不同的 Console 实例即可。

#### 2.8.3 TUI 复用 rich 渲染组件

TUI（`extras/tui/`）基于 textual，而 textual 本身基于 rich。TUI 对 rich 的复用路径：

```
extras/tui/ 加载
    │
    ├── import mutagent.extras.rich.block_handlers
    │   → RichCodeHandler, RichTasksHandler 等注册到 _class_registry
    │   → TUI 若需自定义某个类型，定义自己的子类覆盖 _BLOCK_TYPE
    │   → TUI 不自定义的类型，直接复用 rich 版 handler
    │
    ├── @impl(UserIO.render_event) → TUI 自己的实现
    │   → 覆盖 rich 的 @impl（最后注册者胜出）
    │   → 创建面向 textual widget 的 Console
    │   → 注入给 BlockHandler（复用 rich handler 的渲染逻辑）
    │
    └── @impl(UserIO.present) → TUI 自己的路由实现
```

**加载顺序保证**：config.json 的 `"modules"` 按列表顺序加载。TUI 导入 `extras.rich.block_handlers` 会触发 `extras/rich/__init__.py`，先注册 rich 的 @impl；随后 TUI 注册自己的 @impl 覆盖。最终 `@impl` 指向 TUI 实现，BlockHandler 注册表中 rich 版 handler 可用。

#### 2.8.4 多前端同时运行（未来架构演进）

当前 `@impl` 是类级别的全局覆盖，一个 UserIO 类只能有一套活跃实现。多前端同时运行需要的架构演进：

| 当前 | 未来方向 |
|------|----------|
| 单 UserIO 实例，`@impl` 全局覆盖 | 多 UserIO 实例，每个前端一个 |
| `App.userio` 单一引用 | `App.userio_list` 或前端注册表 |
| `render_event` 直接调用 | 事件广播到所有活跃前端 |

**本 SDD 不需要实现多前端同时运行**。但当前设计通过以下方式为其留出空间：

1. **Console 注入**：BlockHandler 不持有全局 Console，由调用方注入 → 同一 handler 类可被不同前端以不同 Console 调用
2. **BlockHandler 类独立于 @impl**：handler 类在 `_class_registry` 中注册，与 UserIO 的 @impl 绑定解耦
3. **无全局状态**：`_parse_state`、`text_buf` 存储在 UserIO 实例上（`object.__setattr__`），多实例互不干扰

**多前端同时运行是 UserIO 声明层的架构变更（`feature-ui-layer.md` 范畴），不属于本 extras 模块的职责。**

#### 2.8.5 对本 SDD 的设计影响

基于以上分析，当前实现需注意：

| 要求 | 做法 |
|------|------|
| BlockHandler 前端无关 | handler 的 on_line/on_end/render 只通过注入的 Console 输出，不直接 `print()` 或创建 stdout Console |
| Console fallback 明确 | `_handler_console(self)` 的 fallback 创建 `Console(highlight=False)` 仅作为安全网，正常路径必须注入 |
| 状态存储在实例上 | `_parse_state`、`text_buf`、`_console` 全部通过 `object.__setattr__` 存储在 UserIO 实例上 |
| 模块导入无副作用 | `block_handlers.py` 仅定义类，不创建全局 Console 或修改全局状态 |

## 3. 待定问题

无。所有问题已确认：

| 问题 | 决策 |
|------|------|
| BlockHandler 如何获取 Console 实例 | 通过 metadata 注入，handler 在 on_start 中保存引用；render 路径从 content.metadata 取；fallback 创建独立 Console 作为安全网 |
| 普通文本 Markdown 渲染粒度 | 段落边界（`\n\n`）和块边界时刷新，兼顾流式体验和渲染质量 |
| 多前端复用 | Rich 模块拆分为渲染组件层（BlockHandler）和前端绑定层（@impl）；TUI 复用组件层，覆盖绑定层 |
| 多前端同时运行 | 需要 UserIO 声明层架构变更（多实例），不属于本 SDD 范畴；当前设计通过 Console 注入和实例级状态存储预留空间 |

## 4. 实施步骤清单

### 阶段一：模块骨架与 Console 管理 [已完成]

- [x] **Task 1.1**: 创建 `extras/rich/__init__.py`
  - [x] import 守卫（rich 不存在时抛明确 ImportError）
  - [x] 导入 userio_impl 和 block_handlers 子模块
  - 状态：✅ 已完成

- [x] **Task 1.2**: 创建 `extras/rich/userio_impl.py`
  - [x] `_get_console(userio)` 帮助函数：惰性创建 Console，存储在 UserIO 实例上
  - [x] `_get_parse_state(userio)` / `_flush_text_buf(userio, ps)` / `_reset_parse_state(userio)` 状态机帮助函数
  - [x] `_process_text(userio, text)` / `_process_complete_line(userio, ps, line)` 行处理（含 text_buf 段落刷新）
  - [x] `@mutagent.impl(UserIO.render_event)` 完整实现（text_delta Markdown 渲染 + 工具事件 rich 样式 + turn_done flush）
  - [x] `@mutagent.impl(UserIO.present)` 完整实现（handler 委托 + fallback rich 输出）
  - 状态：✅ 已完成

### 阶段二：Rich BlockHandler 实现 [已完成]

- [x] **Task 2.1**: 创建 `extras/rich/block_handlers.py`
  - [x] `_handler_console(self)` 通用帮助函数
  - [x] RichTasksHandler（彩色任务标记：✅ ⏳ ◻）
  - [x] RichStatusHandler（`rich.panel.Panel` 面板渲染）
  - [x] RichCodeHandler（`rich.syntax.Syntax` 语法高亮 + 行号）
  - [x] RichThinkingHandler（`dim italic` 样式）
  - 状态：✅ 已完成

### 阶段三：测试 [已完成]

- [x] **Task 3.1**: 创建 `tests/test_rich_extras.py`
  - [x] `pytest.importorskip("rich")` 守卫
  - [x] 各 rich BlockHandler 的 on_start / on_line / on_end / render 测试
  - [x] render_event 覆盖测试（text_delta Markdown 渲染、tool events、errors、turn_done flush）
  - [x] present 覆盖测试（handler 委托 + fallback）
  - [x] text_buf 段落边界刷新测试
  - [x] 块检测与 rich handler 集成测试
  - [x] 模块加载覆盖机制测试（@impl 生效、discover_block_handlers 返回 rich 版）
  - 状态：✅ 已完成

### 阶段四：文档更新 [已完成]

- [x] **Task 4.1**: 更新 `feature-ui-layer.md` 中 Task 6.2 状态
  - 状态：✅ 已完成

## 5. 测试验证

### 单元测试

| 测试用例 | 场景 | 预期行为 |
|----------|------|----------|
| `test_rich_tasks_handler_on_line_checked` | `on_line("- [x] done")` | 输出包含绿色 ✅ 标记 |
| `test_rich_tasks_handler_on_line_pending` | `on_line("- [ ] todo")` | 输出包含灰色 ◻ 标记 |
| `test_rich_tasks_handler_on_line_in_progress` | `on_line("- [~] working")` | 输出包含黄色 ⏳ 标记 |
| `test_rich_tasks_handler_render` | `render(Content(type="tasks", body=...))` | 按行渲染彩色标记 |
| `test_rich_status_handler_buffers` | on_start + on_line*N | 缓冲期间无输出 |
| `test_rich_status_handler_on_end_panel` | on_end 后 | 输出包含 Panel 渲染内容 |
| `test_rich_status_handler_render` | `render(Content(type="status", ...))` | Panel 渲染 |
| `test_rich_code_handler_syntax` | on_start + on_line + on_end | Syntax 高亮渲染完整代码 |
| `test_rich_code_handler_render` | `render(Content(type="code", ...))` | Syntax 渲染 |
| `test_rich_thinking_handler_dim` | `on_line(text)` | dim italic 样式输出 |
| `test_rich_thinking_handler_render` | `render(Content(type="thinking", ...))` | dim italic 整体渲染 |
| `test_render_event_text_markdown` | text_delta + turn_done | Markdown 渲染输出 |
| `test_render_event_tool_exec_start` | tool_exec_start 事件 | dim 样式工具调用信息 |
| `test_render_event_tool_exec_end_ok` | tool_exec_end (成功) | green 样式结果 |
| `test_render_event_tool_exec_end_error` | tool_exec_end (失败) | red bold 样式错误 |
| `test_render_event_error` | error 事件 | stderr red bold 输出 |
| `test_render_event_turn_done_flush` | text_delta 累积后 turn_done | flush text_buf 为 Markdown |
| `test_text_buf_paragraph_flush` | text_delta 含 `\n\n` | 段落边界刷新 Markdown |
| `test_text_buf_flush_before_block` | text + block 开始 | 块前文本先 Markdown 渲染 |
| `test_present_with_handler` | present + registered handler | 委托给 handler.render |
| `test_present_fallback` | present + 无 handler | rich console 带 source 前缀 |
| `test_impl_overrides_basic` | 加载 extras.rich 后 | @impl 覆盖基础终端实现 |
| `test_discover_returns_rich_handlers` | 加载后 discover | 返回 Rich 版 handler |

### 集成测试

- [ ] 配置 `"modules": ["mutagent.extras.rich"]` 后运行完整测试套件
- [ ] 手动运行 `python -m mutagent` 验证终端渲染效果

### 测试方法

```bash
# 运行 rich extras 测试
pytest tests/test_rich_extras.py -v

# 运行全部测试（确认无回归）
pytest
```
