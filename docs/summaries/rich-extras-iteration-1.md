# Rich 增强终端迭代总结

**日期**：2026-02-20
**迭代范围**：`mutagent.extras.rich` 模块首次实现
**关联规范**：`docs/specifications/feature-rich-extras.md`

## 1. 完成内容

### 模块结构

```
src/mutagent/extras/rich/
├── __init__.py          — 导入守卫 + 子模块导入触发注册
├── userio_impl.py       — @impl 覆盖 render_event / present
└── block_handlers.py    — 4 个 Rich BlockHandler
```

### 实现要点

| 组件 | 说明 |
|------|------|
| `__init__.py` | `import rich` 守卫，缺失时抛明确 `ImportError` |
| `RichTasksHandler` | `[x]` → ✅、`[~]` → ⏳、`[ ]` → ◻ 彩色标记替换 |
| `RichStatusHandler` | 流式缓冲 + `rich.panel.Panel` 面板渲染 |
| `RichCodeHandler` | 流式缓冲 + `rich.syntax.Syntax` 语法高亮（行号、monokai 主题） |
| `RichThinkingHandler` | `dim italic` 样式逐行渲染 |
| `render_event` | 扩展状态机增加 `text_buf` 字段，段落边界 / 块边界 / 事件切换时刷新为 `Markdown` |
| `present` | Console 注入到 handler metadata，fallback 使用 rich 带 source 前缀输出 |

### 测试覆盖

`tests/test_rich_extras.py`：56 个测试用例，覆盖：
- 各 handler 的 on_start / on_line / on_end / render
- Console 注入与 fallback
- render_event 全事件类型
- text_buf 段落刷新、块边界刷新、工具事件前刷新
- present handler 委托与 fallback
- 块检测流式集成
- @impl 覆盖与 handler 发现
- 多实例状态隔离

## 2. 遇到的问题与解决

### 2.1 text_buf 在工具事件前未刷新（运行时发现）

**现象**：Agent 运行时，用户报告"让我看以下 rich 的 Markdown 渲染实现"这句文本在工具调用信息**之后**才显示。

**根因**：`render_event` 的 Markdown 累积机制（`text_buf`）只在段落边界（`\n\n`）、块边界和 `turn_done` 时刷新。当 LLM 输出文本后紧接工具调用时，`tool_exec_start` 事件直接输出到 Console，而累积的文本仍留在 `text_buf` 中，直到 `turn_done` 才渲染。

**修复**：在 `tool_exec_start`、`tool_exec_end`、`error` 三个分支的处理逻辑前，各加一行 `_flush_text_buf(self, ps)`。

**经验**：引入缓冲机制时，必须枚举所有"打断缓冲"的事件类型，确保缓冲内容在语义边界处及时刷新。流式渲染中，**任何非文本事件都是隐式的刷新点**。

**特殊之处**：这个 bug 是 Agent 自己在运行时发现并通过 `define_module` 热修复的。Agent 的诊断和修复过程：
1. 用 `view_source` 查看 `userio.py`、`agent_impl.py`、`userio_impl.py`，逐步定位到 `extras.rich.userio_impl`
2. 分析 `render_event` 的事件处理流程，识别出 `text_buf` 在工具事件前未刷新的问题
3. 先在临时模块 `rich_userio_fix` 中写修复原型验证思路
4. 确认后对 `mutagent.extras.rich.userio_impl` 做 `define_module` 热替换
5. 清理临时模块，用户验证修复生效

### 2.2 @impl 全局副作用导致测试互相污染

**现象**：`test_rich_extras.py` 中 `import mutagent.extras.rich` 后，rich 的 `@impl` 覆盖了基础终端实现，导致后续运行的 `test_userio.py` 全部失败（基础终端测试期望 `print()` 输出到 `capsys`，但 rich 版使用 `Console.print()`）。

同时 rich 的 BlockHandler 子类注册到 `_class_registry` 后，`discover_block_handlers()` 返回 rich 版 handler，导致 `test_handler_types` 断言失败。

**修复**：在 `test_rich_extras.py` 中添加 `module` 级 autouse fixture，在模块测试结束后：
1. 调用 `mutagent.unregister_module_impls("mutagent.extras.rich.userio_impl")` 卸载 rich 的 @impl，恢复基础终端实现为覆盖链顶部
2. 从 `_class_registry` 中删除 rich handler 类的条目

**经验**：
- `@impl` 是全局状态变更，`import` 即生效。跨测试模块的副作用必须显式清理。
- `register_module_impls` 不能用于"恢复"——它依据首次注册序号排序，重新调用不改变链顺序。正确的恢复方式是 `unregister_module_impls` 卸载后注册者。
- `_class_registry` 中的类条目由元类在类定义时注册，与 `@impl` 是独立的两套机制，需分别清理。

## 3. 架构决策记录

### Console 注入 vs 全局 Console

选择了 Console 注入模式：BlockHandler 不创建自己的 Console，一切通过 `on_start(metadata)` 或 `content.metadata` 注入。这使得同一个 `RichCodeHandler` 在终端中输出到 stdout，在未来 TUI 中输出到 textual widget，仅凭传入不同的 Console 实例即可。

Fallback 安全网（`_handler_console`）仅在 handler 被直接调用时创建 stdout Console，正常流程不应触发。

### 渲染组件层 vs 前端绑定层

`block_handlers.py`（渲染组件层）不依赖 `@impl` 或 UserIO，可被 TUI 直接 import 复用。
`userio_impl.py`（前端绑定层）通过 `@impl` 绑定到 UserIO，创建 stdout Console 并注入。

这种分离确保未来 `extras/tui/` 可以：
- 复用 rich handler 类（Syntax 高亮、Panel 面板等）
- 提供自己的 `@impl(UserIO.render_event)` 覆盖 rich 版
- 注入面向 textual widget 的 Console

### text_buf Markdown 段落累积

基础终端逐字输出（`print(text, end="")`），但 Markdown 渲染需要完整段落。引入 `text_buf` 字段在以下时机刷新：
- 段落边界（`\n\n`）
- mutagent: 块开始前
- 工具事件前（tool_exec_start / tool_exec_end / error）
- turn_done

这个设计在流式体验和渲染质量之间取得平衡，但代价是增加了刷新时机的维护成本。

## 4. 未来优化方向

### 4.1 text_buf 刷新策略优化

当前 `text_buf` 的刷新依赖手动枚举事件类型。更健壮的方案：

**方案 A**：将刷新逻辑改为"非 text_delta 事件一律先刷新"：
```python
if event.type != "text_delta":
    _flush_text_buf(self, ps)
```
这消除了遗漏新事件类型的风险，但对 `response_done` 等无需刷新的事件有少量开销。

**方案 B**：引入 `_ensure_flushed` 标志，在 text_delta 时置 False，非 text_delta 时检查并刷新。

### 4.2 Markdown 渲染粒度

当前按段落（`\n\n`）刷新。可以考虑：
- **行级刷新**：每行完成后立即渲染为 Markdown（更实时，但可能丢失跨行格式）
- **超时刷新**：累积一定时间后强制刷新（需要 async 支持）
- **语法感知刷新**：检测到完整的 Markdown 元素（标题、列表、代码块）后刷新

### 4.3 rich Console 样式主题化

当前样式硬编码（dim、green、red bold 等）。可以：
- 抽取到配置文件中（`config.json` 的 `rich.theme` 段）
- 支持 `rich.theme.Theme` 自定义

### 4.4 BlockHandler 流式渲染改进

- **RichCodeHandler**：当前缓冲所有代码行到 `on_end` 时一次性渲染 Syntax。对于长代码块，用户要等较久。可以考虑流式高亮（逐行输出带颜色但无行号，`on_end` 时用完整 Syntax 替换）。
- **RichStatusHandler**：Panel 宽度和样式可配置。

### 4.5 测试隔离改进

当前 `test_rich_extras.py` 通过 module fixture 清理 @impl 和 _class_registry。更好的方案：
- mutobj 提供 `@impl` 上下文管理器（`with impl_scope(): ...`），自动保存/恢复覆盖链
- 或 pytest plugin 提供 fixture 级的 @impl 隔离

### 4.6 多前端同时运行

当前 `@impl` 是类级别全局覆盖，一个 UserIO 类只能有一套活跃实现。未来多前端同时运行需要：
- 多 UserIO 实例，每个前端一个
- 事件广播到所有活跃前端
- 这属于 UserIO 声明层的架构变更，不在 extras 范畴

## 5. 文件变更清单

| 文件 | 变更类型 |
|------|----------|
| `src/mutagent/extras/rich/__init__.py` | 新增 |
| `src/mutagent/extras/rich/block_handlers.py` | 新增 |
| `src/mutagent/extras/rich/userio_impl.py` | 新增 |
| `tests/test_rich_extras.py` | 新增 |
| `docs/specifications/feature-rich-extras.md` | 更新（状态标记为已完成） |
