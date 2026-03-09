# mutagent pyright 类型错误修复

**状态**：✅ 已完成
**日期**：2026-03-09
**类型**：Bug修复

## 背景

mutagent 当前有 21 个 pyright 错误。其中 9 个来自即将移除的 `extras/rich` 模块，剩余 12 个分布在 4 个文件中。

上次修复（commit `0497652`）引入了 `if False: yield` 模式来处理 async generator 声明桩，这个方案需要讨论。

## 现状分析

### 错误分类与统计

| 类别 | 错误数 | 文件 | 说明 |
|------|--------|------|------|
| rich 缺失导入 | 9 | `extras/rich/*.py` | rich 未安装时报 `reportMissingImports` |
| Usage 类型赋值 | 3 | `anthropic_provider.py` | `Usage.__setitem__` 类型不匹配 |
| list 协变性 | 1 | `main_impl.py` | `list[Path]` vs `list[str \| Path]` 不变性 |
| Future 泛型 | 1 | `main_impl.py` | `Future[None]` vs `Future[Unknown]` |
| Optional 参数 | 1 | `openai_provider.py` | `str \| None` 传给 `str` |
| 函数属性 | 1 | `tool_set_impl.py` | `_customize_schema` 自定义属性 |
| 函数重声明 | 1 | `tool_set_impl.py` | `wrapper` 同名重声明 |
| loader 协议 | 1 | `module_manager.py` | `__loader__` 赋值类型不符 |
| 属性缺失 | 2 | `block_handlers.py` | `_buffer`/`_lines` 属性 |

### rich 移除（9 个错误）— 已确认

移除 `extras/rich/` 目录将直接消除 9 个错误。已确认 Markdown 语法高亮由 `runtime/ansi.py`（`highlight_markdown_line`）自行实现，不依赖 rich。

移除范围：
- 删除 `src/mutagent/extras/rich/` 整个目录
- 删除 `pyproject.toml` 中的 `rich` optional dependency

### async generator 桩问题（核心讨论点）

**当前方案（`if False: yield`）**：
```python
async def send(self, ...) -> AsyncGenerator[StreamEvent, None]:
    """..."""
    ...
    if False:
        yield  # 让 pyright 识别为 async generator
```

**问题**：
1. **语义误导** — `...` 是 Declaration 的桩标记，意味着"由 @impl 提供实现"。但 `if False: yield` 破坏了这个约定，读者会困惑为什么桩方法里有代码
2. **维护负担** — 每个 async generator 声明都需要手动加这两行
3. **运行时无影响** — mutobj 的 `@impl` 机制会完全替换方法体，所以 `if False: yield` 永远不会执行
4. **pyright 的限制** — pyright 通过函数体中是否有 `yield` 来判断是否为 generator，这与 mutobj 的声明-实现分离模式冲突

**决策**：采用 `# type: ignore` 方案（`if False: yield` 影响桩的美观性）。桩保持纯 `...`，在 `...` 行加 ignore 注释：
```python
async def send(self, ...) -> AsyncGenerator[StreamEvent, None]:
    ...  # type: ignore[reportReturnType]
```

涉及 4 处：`agent.py`(2)、`client.py`(1)、`provider.py`(1)。

### 其他错误详情

**anthropic_provider.py — Usage 类型赋值（3 个错误）**
```python
# line 485: usage["input_tokens"] = msg.usage.input_tokens  # int | float vs int
# line 493/495: usage["cache_creation_input_tokens"] = {...}  # dict vs int
```
Usage 是 `TypedDict`，但实际赋值的类型与声明不符。

**main_impl.py — list 不变性（1 个错误）**
```python
# line 341: search_dirs 参数声明为 list[str | Path]，传入 list[Path]
# 修复：参数类型改为 Sequence[str | Path]
```

**openai_provider.py — Optional 参数（1 个错误）**
```python
# line 249: stop_reason 参数声明为 str，传入 str | None
# 修复：参数声明改为 str | None，或传入时提供默认值
```

## 设计方案

### 移除 rich

直接删除 `extras/rich/` 目录及 `pyproject.toml` 中的 rich dependency。无外部引用，安全移除。

**决策**：`usage` 类型从 `dict[str, int]` 改为 `dict[str, Any]`（Anthropic API 的 usage 值实际可能是 int、float 或嵌套 dict）。

## 关键参考

### 源码
- `mutagent/src/mutagent/extras/rich/` — 待移除的 rich 模块
- `mutagent/src/mutagent/builtins/anthropic_provider.py:485-495` — Usage 类型问题
- `mutagent/src/mutagent/builtins/main_impl.py:341,480` — list 不变性 + Future 泛型
- `mutagent/src/mutagent/builtins/openai_provider.py:249` — Optional 参数
- `mutagent/src/mutagent/builtins/tool_set_impl.py:70,316` — 函数重声明 + 属性
- `mutagent/src/mutagent/runtime/module_manager.py:90` — loader 协议
- `mutagent/src/mutagent/provider.py:64-66` — async generator 桩
- `mutagent/src/mutagent/agent.py:55-63` — async generator 桩
- `mutagent/src/mutagent/client.py:97-99` — async generator 桩

### 相关规范
- `mutobj/docs/specifications/bugfix-pyright-type-errors.md` — mutobj 的 pyright 修复

## 实施步骤清单

### Phase 1: 移除 rich [待开始]

- [ ] 删除 `extras/rich/` 目录及 `pyproject.toml` 中 rich 依赖
  - 状态：⏸️ 待开始

### Phase 2: async generator 桩 [待开始]

- [ ] 4 处桩方法去掉 `if False: yield`，改为 `...  # type: ignore[reportReturnType]`（`agent.py`×2, `client.py`×1, `provider.py`×1）
  - 状态：⏸️ 待开始

### Phase 3: 类型修复 [待开始]

- [ ] 修复 `anthropic_provider.py` 的 Usage 类型问题（`dict[str, int]` → `dict[str, Any]`）
- [ ] 修复 `main_impl.py` 的 2 个类型错误（list 不变性 + Future 泛型）
- [ ] 修复 `openai_provider.py` 的 Optional 参数问题
- [ ] 修复 `tool_set_impl.py` 的 2 个错误（函数重声明 + 函数属性）
- [ ] 修复 `module_manager.py` 的 loader 协议问题
  - 状态：⏸️ 待开始

### Phase 4: 验证 [待开始]

- [ ] 运行 `npx pyright src/mutagent/` 验证 0 errors
  - 状态：⏸️ 待开始
