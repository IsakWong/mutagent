# 基础交互模式语法高亮支持 设计规范

**状态**：✅ 已完成
**日期**：2026-02-20
**类型**：功能设计

## 1. 背景

### 1.1 问题来源

当前 mutagent 的基础交互模式（`builtins/userio_impl.py`）输出纯文本，无任何颜色区分：
- 工具调用（tool_exec_start/end）仅截断到 100 字符并以 `[name(args)]` + `-> [done] summary` 形式输出
- LLM 文本、工具结果、错误信息全部是同一颜色
- 长结果被硬截断为 `content[:100] + "..."`，丢失了"还有多少"的信息

`extras.rich` 提供了完整的 rich 增强终端（Markdown 渲染、Syntax 高亮、Panel 面板），但它引入了 `rich` 重依赖，并且改变了输出形式（如将普通文本转为 Markdown 段落渲染）。

需求是在基础模式下提供一种**轻量语法高亮**，仅使用 ANSI 转义码着色，不改变输出内容的文本结构，不引入额外依赖。

### 1.2 目标

1. 基础交互模式中，利用 ANSI 转义码为不同类型信息着色
2. 优化工具调用/结果的显示格式（Python 风格函数调用 + 多行预览）
3. LLM 输出的 Markdown 结构符号（标题、加粗、列表）轻量高亮
4. 不引入新的外部依赖，仅使用 Python 标准库
5. 保持输出内容的文本可读性（纯文本管道仍可用）

## 2. 设计方案

### 2.1 方案选型 ✅ 已确认

**方案 A（直接内置）**。理由：
1. ANSI 转义码是 Python 标准能力，不引入依赖，属于基础终端的合理功能
2. 开箱即用的体验更好——大多数终端都支持 ANSI 颜色
3. 遵循 `NO_COLOR` 约定（https://no-color.org/）即可满足纯文本需求
4. 减少一层 @impl 覆盖的复杂度
5. 当 `extras.rich` 被加载时，其 @impl 自然覆盖内置实现，ANSI 着色不会残留

### 2.2 ANSI 颜色工具模块

新建 `src/mutagent/runtime/ansi.py`：

```python
import os
import sys

def _color_supported() -> bool:
    """Check if the terminal supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        return _enable_windows_ansi()
    return True

# ANSI SGR codes
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_ITALIC = "\033[3m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"

def dim(text: str) -> str: ...
def bold(text: str) -> str: ...
def green(text: str) -> str: ...
def red(text: str) -> str: ...
def bold_red(text: str) -> str: ...
def yellow(text: str) -> str: ...
def cyan(text: str) -> str: ...
```

- 每个函数在 `_color_supported()` 为 False 时返回原文本
- 缓存颜色支持结果避免重复检测

**ANSI 颜色适配原则**：全部使用标准 ANSI 8 色索引（30-37），不使用 256 色或 24-bit RGB。标准索引色的实际 RGB 值由用户的终端配色方案决定——暗色主题的 "cyan" 和亮色主题的 "cyan" 是不同的颜色值，但都是该主题下可读的色调。这意味着只要使用标准索引色，就自动适配所有终端配色。

Markdown 结构符号使用 **cyan**（36）而非 blue（34）：cyan 在主流终端配色中普遍可读性更好，blue 在部分暗色主题下过暗。

### 2.3 工具调用显示优化

#### 当前格式

```
  [inspect_module(path=mutagent.tools)]
  -> [done] Toolkit (class)\n  Methods: __init__(self...
```

#### 优化后格式：Python 风格

工具调用显示为标准 Python 函数调用形式，字符串参数带引号，未来兼容 `Toolkit.func` 命名：

```
  inspect_module(path="mutagent.tools")              ← dim 色
  → Toolkit (class)                                   ← green 色（成功）
    Methods: __init__(self, ...)
    ... +15 lines                                     ← dim 色

  define_module(                                      ← dim 色，参数多时折行
      path="mutagent.my_tool",
      source="class MyTool(Toolkit):\n    _BLOCK...",
  )
  → Module defined: mutagent.my_tool                  ← green 色

  inspect_module(path="bad.module")                   ← dim 色
  → Error: Module not found: bad.module               ← bold red 色
```

#### 2.3.1 `_format_tool_call()` 格式化函数

将 `_summarize_args` 替换为完整的 Python 风格格式化：

```python
_MAX_VALUE_LEN = 60       # 单个参数值的最大显示长度
_MAX_SINGLE_LINE = 80     # 单行显示的最大总长度
_INDENT = "  "            # 基础缩进
_PARAM_INDENT = "      "  # 参数折行缩进（6 spaces）

def _format_tool_call(name: str, args: dict) -> str:
    """Format a tool call as a Python-style function call string."""
```

**格式化规则**：

1. **参数值引号**：
   - `str` 类型的值用双引号包裹：`path="mutagent.tools"`
   - `int`/`float`/`bool`/`None` 使用 Python repr：`count=5`、`verbose=True`
   - `list`/`dict` 使用 repr 并截断

2. **值截断**：超过 `_MAX_VALUE_LEN` 字符时截断为 `"value..."`（引号内截断）

3. **折行策略**：
   - 单行形式 `name(k1="v1", k2="v2")` 总长度 ≤ `_MAX_SINGLE_LINE` → 单行
   - 否则多行形式，每个参数独占一行，末尾逗号：
     ```
     name(
         k1="v1",
         k2="very long value...",
     )
     ```

4. **未来兼容**：`name` 直接使用 `ToolCall.name`，当工具名改为 `ModuleToolkit.inspect_module` 后自动适配

#### 2.3.2 `_format_tool_result()` 结果显示

```python
_PREVIEW_LINES = 4        # 默认预览行数
_RESULT_INDENT = "    "   # 结果缩进（4 spaces）

def _format_tool_result(content: str, is_error: bool) -> str:
    """Format a tool result with preview and line count."""
```

**格式化规则**：

1. **返回箭头**：使用 `→`（U+2192）作为返回指示符
   - 对 Python 用户来说，`→` 是类型注解文档中常见的"返回"符号（`f(x) → y`）
   - 比 `⎿`（Claude Code 风格的树形连接符）对 Python 用户更自然
   - 单行结果：`→ result text`
   - 多行结果第一行：`→ first line`

2. **多行预览**（默认 4 行）：
   - 结果按 `\n` 分割为行
   - 显示前 4 行，每行缩进 `_RESULT_INDENT`
   - 超出部分：`... +N lines`（dim 色）

3. **颜色**：
   - 成功：`→` 和结果内容 green 色
   - 失败：`→` 和结果内容 bold red 色

4. **示例效果**：

```
  inspect_module(path="mutagent.tools")           ← dim
  → Toolkit (class)                               ← green（箭头 + 首行）
    Methods:                                      ← green（续行）
      __init__(self, name, ...)
      inspect(self, path)
    ... +12 lines                                 ← dim
```

### 2.4 LLM 文本 Markdown 轻量高亮

借鉴 VS Code 的 Markdown 源码着色策略：对 Markdown 结构符号着色为 **cyan**，利用标准 ANSI 索引色自动适配终端配色。

#### 高亮规则（最终版）

在 `_process_complete_line()` 的 NORMAL 状态中，对完整行应用以下正则替换：

| 模式 | 匹配 | 高亮范围 | 颜色 | 示例 |
|------|------|----------|------|------|
| `^#{1,6}\s` | 标题 | **整行** | cyan | `## 设计方案` → <cyan>`## 设计方案`</cyan> |
| `^>\s?` | 引用 | **整行** | cyan | `> quote` → <cyan>`> quote`</cyan> |
| `^(\s*[-*+]\s)` | 无序列表标记 | **仅标记** | cyan | `- item` → <cyan>`- `</cyan>`item` |
| `^(\s*\d+\.\s)` | 有序列表标记 | **仅标记** | cyan | `1. first` → <cyan>`1. `</cyan>`first` |
| `\*\*[^*]+\*\*` / `__[^_]+__` | 加粗 | **整个 span** | cyan | `**bold**` → <cyan>`**bold**`</cyan> |
| `` `[^`]+` `` | 行内代码 | **整个 span** | yellow | `` `code` `` → <yellow>`` `code` ``</yellow> |

**设计要点**：
- 标题、引用整行着色，提供强视觉锚点
- 列表仅标记着色，内容保持默认色，避免大段文本变色
- 行内代码使用 yellow（区别于 cyan 的标题/引用），作为独立视觉单元突出
- 加粗整个 span（含内容）着色，与仅标记着色相比信息传达更直观

#### 实现策略

```python
import re

# 行首结构标记（互斥，一行只能匹配一个）
_MD_LINE_PATTERNS = [
    (re.compile(r'^(#{1,6}\s)(.*)$'), r'{cyan}\1{reset}\2'),
    (re.compile(r'^(\s*[-*+]\s)(.*)$'), r'{cyan}\1{reset}\2'),
    (re.compile(r'^(\s*\d+\.\s)(.*)$'), r'{cyan}\1{reset}\2'),
    (re.compile(r'^(>\s?)(.*)$'), r'{cyan}\1{reset}\2'),
]

# 行内标记（可多次匹配）
_MD_INLINE_PATTERNS = [
    (re.compile(r'(\*\*|__)'), r'{cyan}\1{reset}'),          # bold markers
    (re.compile(r'(`[^`]+`)'), r'{cyan}\1{reset}'),          # inline code (entire span)
]

def _highlight_markdown_line(line: str) -> str:
    """Apply lightweight Markdown syntax highlighting to a line."""
    if not _color_enabled:
        return line
    # Try line-start patterns first (mutually exclusive)
    for pattern, repl in _MD_LINE_PATTERNS:
        m = pattern.match(line)
        if m:
            line = pattern.sub(repl.format(cyan=_CYAN, reset=_RESET), line)
            break
    # Apply inline patterns
    for pattern, repl in _MD_INLINE_PATTERNS:
        line = pattern.sub(repl.format(cyan=_CYAN, reset=_RESET), line)
    return line
```

#### 集成点

在 `builtins/userio_impl.py` 的 `_process_complete_line()` 函数中，NORMAL 状态输出行前调用：

```python
# 当前代码（line 146）:
print(line, flush=True)

# 改为:
print(_highlight_markdown_line(line), flush=True)
```

**不影响流式体验**：高亮仅在完整行输出时应用，不改变分块逻辑。不完整行（`line_buf`）仍然原样输出（因为无法判断是否是 Markdown 结构）。

### 2.5 BlockHandler 着色

基础 BlockHandler 的轻量颜色增强：

| Handler | 增强 |
|---------|------|
| CodeHandler | 围栏标记 `` ``` `` 用 dim 色，语言名用 cyan 色 |
| ThinkingHandler | 内容用 dim 色（降低视觉权重） |
| TasksHandler | `[x]` green、`[~]` yellow、`[ ]` dim |
| StatusHandler | 保持不变（基础模式无边框渲染能力） |

### 2.6 终端能力检测

```python
def _color_supported() -> bool:
    # 1. NO_COLOR 环境变量 (https://no-color.org/)
    if os.environ.get("NO_COLOR"):
        return False
    # 2. FORCE_COLOR 环境变量
    if os.environ.get("FORCE_COLOR"):
        return True
    # 3. stdout 不是 tty
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    # 4. Windows 支持（Windows 10+ 原生支持 ANSI）
    if sys.platform == "win32":
        return _enable_windows_ansi()
    return True
```

Windows 上通过 `ctypes` 启用 VT 处理：

```python
def _enable_windows_ansi() -> bool:
    """Enable ANSI processing on Windows 10+."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return True
    except Exception:
        return False
```

### 2.7 输入提示符优化

当前：`> `（纯文本）

优化：使用 bold cyan 色 `> ` 提示符，提升用户输入区域辨识度。

### 2.8 代码变更范围

| 文件 | 变更内容 |
|------|---------|
| `runtime/ansi.py` (新建) | ANSI 颜色辅助函数 + 终端检测 + Markdown 行高亮（cyan） |
| `builtins/userio_impl.py` | render_event 着色 + 工具 Python 格式化 + Markdown 高亮集成 |
| `builtins/block_handlers.py` | BlockHandler 轻量着色 |

### 2.9 与 extras.rich 的兼容性

- `extras.rich` 加载后通过 @impl 完全覆盖 `render_event` 和 `present`，内置着色自然失效
- `extras.rich` 的 BlockHandler 通过 `discover_block_handlers()` 覆盖内置版本
- Markdown 高亮函数在 NORMAL 状态的 `_process_complete_line` 中调用，rich 版有自己的 `_process_complete_line`（累积到 `text_buf` 走 Markdown 渲染），不会冲突
- 无需特殊兼容处理

### 2.10 完整视觉效果示例

```
> 请检查 mutagent.tools 模块                      ← bold cyan 提示符

好的，我来检查这个模块。                             ← 默认色（LLM 文本）

## 模块结构                                        ← "## " cyan，"模块结构" 默认色

以下是 `mutagent.tools` 的内容：                    ← "`mutagent.tools`" 整体 cyan

- **Toolkit** 基类                                 ← "- " cyan，"**" cyan
- **ToolSet** 工具管理

  inspect_module(path="mutagent.tools")            ← dim 色
  → Toolkit (class)                                ← green 色
    ToolSet (class)
    ToolEntry (dataclass)
    ... +8 lines                                   ← dim 色

  view_source(                                     ← dim 色（多参数折行）
      path="mutagent.tools",
      name="Toolkit",
  )
  → class Toolkit(mutagent.Declaration):           ← green 色
        """Base class for tool providers."""
        name: str
        ...
    ... +25 lines                                  ← dim 色

1. Toolkit 提供工具声明                             ← "1. " cyan
2. ToolSet 管理工具注册和分发

> _                                                ← bold cyan 提示符
```

## 3. 待定问题

无。所有问题已确认：

| 问题 | 决策 |
|------|------|
| 内置 vs 扩展 | ✅ 方案 A（直接内置），开箱即用 |
| 工具结果预览行数 | ✅ 默认 4 行，超出显示 `... +N lines` |
| 工具调用风格 | ✅ Python 函数调用形式，字符串带引号，多参数折行 |
| 返回指示符 | ✅ `→`（U+2192），Python 类型注解风格，比 `⎿` 更自然 |
| Markdown 高亮 | ✅ VS Code 风格，结构符号 cyan（标准 ANSI 索引色，自动适配终端配色），行内代码整体着色 |
| 输入提示符 | ✅ bold cyan 色 `> ` |

## 4. 实施步骤清单

### 阶段一：ANSI 颜色基础设施 [已完成]

- [x] **Task 1.1**: 创建 `src/mutagent/runtime/ansi.py`
  - [x] `_color_supported()` 终端检测（NO_COLOR、FORCE_COLOR、isatty、Windows VT）
  - [x] `_enable_windows_ansi()` Windows VT 启用
  - [x] ANSI SGR 常量定义（RESET、DIM、BOLD、ITALIC、RED、GREEN、YELLOW、CYAN）
  - [x] 颜色包装函数（dim、bold、green、red、bold_red、yellow、cyan）
  - [x] 颜色支持结果缓存
  - 状态：✅ 已完成

### 阶段二：工具调用 Python 格式化 [已完成]

- [x] **Task 2.1**: 在 `builtins/userio_impl.py` 中实现 `_format_tool_call()`
  - [x] 字符串参数带引号，其他类型用 repr
  - [x] 值截断（`_MAX_VALUE_LEN=60`）
  - [x] 单行/多行折行策略（`_MAX_SINGLE_LINE=80`）
  - [x] 整体 dim 色
  - 状态：✅ 已完成

- [x] **Task 2.2**: 在 `builtins/userio_impl.py` 中实现 `_format_tool_result()`
  - [x] `→` 返回指示符
  - [x] 多行预览（前 4 行 + `... +N lines`）
  - [x] 成功 green / 失败 bold_red
  - 状态：✅ 已完成

- [x] **Task 2.3**: 修改 `render_event` 中 tool_exec_start/end/error 事件处理
  - [x] 集成 `_format_tool_call` 和 `_format_tool_result`
  - [x] error 事件 bold_red 着色
  - 状态：✅ 已完成

### 阶段三：Markdown 轻量高亮 [已完成]

- [x] **Task 3.1**: 在 `runtime/ansi.py` 中实现 `highlight_markdown_line()`
  - [x] 行首模式：标题 `#`、列表 `-`/`*`/`+`/`1.`、引用 `>`
  - [x] 行内模式：加粗 `**`/`__`、行内代码 `` `text` ``（整体着色）
  - [x] cyan 着色（标准 ANSI 索引色）
  - 状态：✅ 已完成

- [x] **Task 3.2**: 在 `builtins/userio_impl.py` 的 `_process_complete_line()` 中集成
  - [x] NORMAL 状态输出行时调用 `highlight_markdown_line()`
  - 状态：✅ 已完成

### 阶段四：BlockHandler 着色 [已完成]

- [x] **Task 4.1**: 修改 `builtins/block_handlers.py`
  - [x] CodeHandler：围栏 dim + 语言名 cyan
  - [x] ThinkingHandler：内容 dim
  - [x] TasksHandler：`[x]` green、`[~]` yellow、`[ ]` dim
  - 状态：✅ 已完成

### 阶段五：输入提示符 [已完成]

- [x] **Task 5.1**: 修改 `read_input` 实现
  - [x] bold cyan 色 `> ` 提示符
  - 状态：✅ 已完成

### 阶段六：测试 [已完成]

- [x] **Task 6.1**: 创建 `tests/test_ansi.py`
  - [x] 颜色支持检测测试（NO_COLOR、FORCE_COLOR、非 tty）
  - [x] 颜色函数测试（启用/禁用时的输出）
  - [x] Markdown 行高亮测试
  - 状态：✅ 已完成

- [x] **Task 6.2**: 更新或创建工具格式化测试
  - [x] `_format_tool_call` 单行/多行/截断测试
  - [x] `_format_tool_result` 多行预览/短结果/错误结果测试
  - [x] BlockHandler 着色测试
  - 状态：⏸️ 待开始

## 5. 测试验证

### 单元测试

| 测试用例 | 场景 | 预期行为 |
|----------|------|----------|
| `test_color_supported_no_color` | `NO_COLOR=1` | 返回 False |
| `test_color_supported_force_color` | `FORCE_COLOR=1` | 返回 True |
| `test_color_supported_not_tty` | stdout 非 tty | 返回 False |
| `test_dim_enabled` | 颜色启用 | 返回带 ANSI 码的文本 |
| `test_dim_disabled` | 颜色禁用 | 返回原始文本 |
| `test_format_tool_call_single_line` | 短参数 | `name(k="v")` 单行 |
| `test_format_tool_call_multi_line` | 长参数 | 折行 + 缩进 |
| `test_format_tool_call_value_types` | str/int/bool | 字符串带引号，其他 repr |
| `test_format_tool_call_truncate` | 超长值 | 截断为 `"value..."` |
| `test_format_tool_result_short` | 2 行结果 | 完整显示 |
| `test_format_tool_result_multiline` | 10 行结果 | 前 4 行 + `... +6 lines` |
| `test_format_tool_result_error` | 错误结果 | bold red |
| `test_md_highlight_heading` | `## Title` | `##` cyan |
| `test_md_highlight_list` | `- item` | `- ` cyan |
| `test_md_highlight_bold` | `**bold**` | `**` cyan |
| `test_md_highlight_inline_code` | `` `code` `` | 整个 `` `code` `` cyan |
| `test_md_highlight_no_color` | 颜色禁用 | 返回原文 |
| `test_block_handler_code_color` | CodeHandler 围栏 | dim 围栏 + cyan 语言名 |
| `test_block_handler_thinking_dim` | ThinkingHandler | dim 输出 |
| `test_block_handler_tasks_color` | TasksHandler `[x]` | green 色 |
