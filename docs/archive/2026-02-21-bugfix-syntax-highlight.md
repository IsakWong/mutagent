# 语法高亮 Bug 修复 设计规范

**状态**：✅ 已完成
**日期**：2026-02-21
**类型**：Bug修复
**关联**：`docs/archive/2026-02-21-feature-syntax-highlight.md`

## 1. 背景

### 1.1 问题来源

基于 `feature-syntax-highlight.md` 规范实现的语法高亮功能，在实际运行测试中发现两个 Bug：

1. **工具错误未被区分颜色**：`define_module` 返回 `"Error defining ..."` 错误字符串时，工具结果仍显示为 green（成功色），而非 bold_red（错误色）
2. **流式 Markdown 语法高亮不工作**：LLM 流式输出时，行首 Markdown 标记（标题 `##`、列表 `- ` 等）的 cyan 高亮完全不生效

以上问题来自会话 `20260221_082415` 的测试反馈。

### 1.2 问题分析

#### Bug 1：工具错误颜色不区分

**根因**：`define_module_impl.py` 在内部 try/except 中捕获异常并返回普通字符串，而非让异常传播。

```python
# define_module_impl.py line 22-31
try:
    self.module_manager.patch_module(module_path, source)
    return f"OK: {module_path} defined (v{version}){warning}"
except Exception as e:
    logger.error(...)
    return f"Error defining {module_path}: {type(e).__name__}: {e}"  # ← 普通返回
```

`tool_set_impl.py` 的 dispatch 仅在未捕获异常时设置 `is_error=True`：

```python
# tool_set_impl.py line 312-320
try:
    result = entry.callable(**tool_call.arguments)
    # result 是普通字符串 → is_error=False
except Exception as e:
    return ToolResult(..., is_error=True)  # ← 只有这里设 is_error
```

因此 `_format_tool_result(content, is_error=False)` 使用 `green` 着色，用户无法区分成功与失败。

**影响范围**：所有在工具函数内部 catch 异常并返回错误字符串的工具（目前只有 `define_module`）。

#### Bug 2：流式 Markdown 高亮不工作

**根因**：`_process_text()` 中的部分行刷新（partial line flush）过于激进，破坏了行级 Markdown 模式匹配。

流式场景分析：LLM 以 token 片段输出 `"## Design\n"`：

```
片段1: "## "  → line_buf = "## "
                → _could_be_block_start("## ") = False（"```mutagent:" 不以 "## " 开头）
                → 立即输出 "## "，清空 line_buf   ← 问题！标题标记已丢失
片段2: "Design\n" → line_buf = "Design\n"
                → 切分完整行 "Design"
                → highlight_markdown_line("Design") → 无标题前缀 → 无高亮
用户看到: "## Design"（无任何高亮）
```

`_process_complete_line()` 中的 `highlight_markdown_line()` 调用是正确的，但因为行被拆成了两段分别输出，行首模式匹配无法工作。

**影响范围**：所有行首 Markdown 模式（标题、列表、引用）。行内模式（加粗、行内代码）在完整行中工作正常，但在部分行刷新时也可能被拆断。

## 2. 设计方案

### 2.1 Bug 1 修复：工具错误着色

**方案**：让 `define_module` 在失败时 raise 异常，由 `tool_set_impl.py` 的 dispatch 层统一处理。

修改 `define_module_impl.py`：

```python
@mutagent.impl(ModuleToolkit.define_module)
def define_module(self, module_path, source):
    warning = ""
    if module_path.startswith("mutagent."):
        logger.warning(...)
        warning = "..."
    # 不再 try/except，让异常自然传播
    self.module_manager.patch_module(module_path, source)
    version = self.module_manager.get_version(module_path)
    logger.info(...)
    return f"OK: {module_path} defined (v{version}){warning}"
```

dispatch 层的 `except Exception` 会捕获异常并创建 `ToolResult(is_error=True)`，`_format_tool_result` 就能正确使用 `bold_red`。

**保留 logger.error**：日志记录应在 dispatch 层或 patch_module 内部完成，define_module 无需额外 logging。但 patch_module 本身可能已经有日志。检查后决定。

### 2.2 Bug 2 修复：流式 Markdown 高亮

**方案**：取消 NORMAL 状态下的部分行刷新，改为缓冲完整行后统一输出。

修改 `_process_text()`：

```python
def _process_text(userio, text):
    ps = _get_parse_state(userio)
    ps['line_buf'] += text

    while '\n' in ps['line_buf']:
        line, ps['line_buf'] = ps['line_buf'].split('\n', 1)
        _process_complete_line(userio, ps, line)

    # 删除原有的部分行刷新逻辑
    # 部分行保留在 line_buf 中，等待下一个 \n 到来
```

**影响分析**：

| 场景 | 原行为 | 新行为 |
|------|--------|--------|
| 普通文本 `"Hello world\n"` | 逐片段输出 | 整行输出（含高亮） |
| 标题 `"## Design\n"` | `"## "` 先出，`"Design"` 后出 | 整行 `"## Design"` 一次输出（cyan 高亮） |
| 长行无换行 | 实时逐片段 | 缓冲直到 `\n` 到来 |
| `turn_done` 事件 | `_reset_parse_state` 刷新剩余 | 同左（需加高亮） |
| 块检测前缀 `` ` `` | 已缓冲 | 已缓冲（不变） |

**流式体验影响**：

LLM 输出是 token 级流式，通常每行有 10-50 个 token，token 间延迟 20-50ms。取消部分行刷新后：
- 最坏情况：一行 50 个 token × 50ms = 2.5 秒延迟。但 LLM 实际 token 速度更快（20ms/token），且多数行远少于 50 个 token
- 典型情况：一行文本 20 个 token × 20ms = 400ms 延迟，用户几乎无感
- 代码块和 thinking 块通过 handler 处理，不受影响
- 这是 **正确性 vs 实时性** 的合理权衡：高亮正确比逐字符出现更有价值

**`_reset_parse_state` 修改**：在 turn_done 刷新剩余 line_buf 时，也需要应用高亮：

```python
if ps['line_buf']:
    print(highlight_markdown_line(ps['line_buf']), end="", flush=True)
```

## 3. 待定问题

无。设计方案明确，可直接实施。

## 4. 实施步骤清单

### 阶段一：修复工具错误着色 [✅ 已完成]

- [x] **Task 1.1**: 修改 `builtins/define_module_impl.py`
  - [x] 移除 try/except 包裹，让异常自然传播到 dispatch 层
  - [x] dispatch 层 `tool_set_impl.py:315-320` 已正确处理（`is_error=True`）
  - 状态：✅ 已完成

- [x] **Task 1.2**: 更新相关测试
  - [x] `test_define_syntax_error` 改为 `pytest.raises(SyntaxError)`
  - [x] dispatch 层 is_error 路径已有覆盖（`test_tool_set.py`）
  - 状态：✅ 已完成

### 阶段二：修复流式 Markdown 高亮 [✅ 已完成]

- [x] **Task 2.1**: 修改 `builtins/userio_impl.py` 的 `_process_text()`
  - [x] 移除 NORMAL 状态下的部分行刷新逻辑
  - 状态：✅ 已完成

- [x] **Task 2.2**: 修改 `_reset_parse_state()` 的缓冲区刷新
  - [x] 对剩余 `line_buf` 应用 `highlight_markdown_line()`
  - 状态：✅ 已完成

- [x] **Task 2.3**: 更新测试
  - [x] 新增 `TestStreamingMarkdownHighlight` 测试类（5 个测试）
  - [x] 更新 `test_normal_text_buffers_until_newline`（原 `test_normal_text_streams_immediately`）
  - [x] 更新 `test_backtick_prefix_buffers`
  - [x] 全量 572 测试通过
  - 状态：✅ 已完成

## 5. 测试验证

### 单元测试

| 测试用例 | 场景 | 预期行为 |
|----------|------|----------|
| `test_define_module_error_is_error` | define_module 语法错误 | `ToolResult.is_error=True` |
| `test_define_module_success_not_error` | define_module 正常 | `ToolResult.is_error=False` |
| `test_streaming_heading_highlight` | 分片输出 `"## "` + `"Design\n"` | 最终输出含 cyan |
| `test_streaming_list_highlight` | 分片输出 `"- "` + `"item\n"` | 最终输出含 cyan |
| `test_reset_flushes_with_highlight` | `turn_done` 时 line_buf 非空 | 输出含高亮 |
| `test_no_premature_partial_flush` | 输出 `"## "` 不立即刷新 | 缓冲在 line_buf 中 |
