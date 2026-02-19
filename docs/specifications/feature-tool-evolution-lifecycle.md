# 工具演化闭环系统 设计规范

**状态**：🔄 进行中
**日期**：2026-02-19
**类型**：功能设计

## 1. 背景

### 1.1 现状

Multi-Agent 架构（ToolSet + DelegateTool）已实现。但 Agent 仍无法**自主开发、测试和迭代新工具**：

- `define_module` 可以创建代码模块，但新模块的方法无法成为可调用的工具
- 移除 `run_code` 后，Agent 无法验证自己写的代码是否正确
- 工具列表在初始化时固定，运行时无法扩展

### 1.2 核心洞察

**Agent 不需要 `run_code`。它把代码定义为工具，通过正常工具调用来测试。**

- 所有代码必须组织为有明确接口（参数 + 返回值 + docstring）的工具方法
- 所有执行都通过 `ToolSet.dispatch()`，有完整的日志、审计、异常捕获
- Agent 被约束在工具框架内，而非自由执行任意代码

### 1.3 目标

建立完整的工具演化闭环，无需手动注册步骤：

```
define_module(Toolkit 子类) → 自动发现 → 调用工具测试 → 修改代码 → 再次调用 → ... → save_module
```

### 1.4 关联文档

| 文档 | 关系 |
|------|------|
| feature-tool-loop-control.md | Phase 1a，设计已确认，待实施后删除 |
| feature-tool-schema-annotation.md | Phase 1b，设计已确认，待实施后删除 |
| feature-dynamic-tool-registration.md | 被本文档替代，待删除 |
| feature-multi-agent.md | 前置依赖（ToolSet/DelegateTool 已完成） |
| feature-agent-evolution-robustness.md | 前置依赖（已完成） |

## 2. 设计方案

### 2.1 整体路线图

```
Phase 1: 基础设施稳固（安全前置）
  ├─ 1a. max_tool_rounds           ← 防止失控行为
  └─ 1b. Schema 生成重构           ← annotation + docstring 替代 AST

Phase 2: Toolkit 与自动发现（闭环能力）
  ├─ 2a. Toolkit 基类              ← 工具提供者的统一基类
  ├─ 2b. 工具分类重组              ← EssentialTools → ModuleToolkit + LogToolkit + AgentToolkit
  ├─ 2c. ToolSet 自动发现          ← 扫描 Toolkit 子类，自动注册
  ├─ 2d. 延迟绑定                  ← define_module 后工具自动反映新代码
  └─ 2e. 系统提示更新              ← 引导 Agent 使用工具迭代流程

Phase 3: 持久化（跨会话）
  └─ 3a. 工具持久化                ← 已保存的工具重启后自动加载
```

**依赖关系**：Phase 1a → Phase 2（先有循环控制再赋能）。Phase 1b → Phase 2（Schema 重构为自动发现提供基础）。Phase 3 依赖 Phase 2。

### 2.2 Toolkit 基类

引入 `Toolkit` 类作为**所有工具提供者的统一基类**。继承 Toolkit 的类，其公开方法（不以 `_` 开头）自动被识别为工具，无需手动注册。

```python
# src/mutagent/toolkit.py

class Toolkit(mutagent.Declaration):
    """Base class for tool providers.

    All public methods (not starting with _) defined on subclasses
    are automatically discovered as tools by ToolSet.

    Example:
        class MyTools(Toolkit):
            def greet(self, name: str) -> str:
                '''Say hello.

                Args:
                    name: The person to greet.

                Returns:
                    A greeting message.
                '''
                return f"Hello, {name}!"
    """
    pass
```

导出：在 `mutagent/__init__.py` 中增加 `Toolkit`，Agent 可通过 `mutagent.Toolkit` 使用。

### 2.3 工具分类重组

将现有的 `EssentialTools`（单一大类）和 `DelegateTool` 按职责拆分为三个 Toolkit 子类：

```
Toolkit (Declaration)
├── ModuleToolkit   ← 模块管理：inspect_module, view_source, define_module, save_module
├── LogToolkit      ← 日志诊断：query_logs
├── AgentToolkit    ← Agent 协作：delegate
└── (agent-created) ← 动态定义的工具
```

#### ModuleToolkit — 模块管理

```python
class ModuleToolkit(Toolkit):
    """Tools for inspecting, modifying, and persisting Python modules.

    Attributes:
        module_manager: The ModuleManager instance for runtime patching.
    """
    module_manager: ModuleManager

    def inspect_module(self, module_path: str = "", depth: int = 2) -> str: ...
    def view_source(self, target: str) -> str: ...
    def define_module(self, module_path: str, source: str) -> str: ...
    def save_module(self, module_path: str, level: str = "project") -> str: ...
```

#### LogToolkit — 日志诊断

```python
class LogToolkit(Toolkit):
    """Tools for querying logs and configuring log capture.

    Attributes:
        log_store: The LogStore instance for in-memory log storage.
    """
    log_store: LogStore

    def query_logs(self, pattern: str = "", level: str = "DEBUG",
                   limit: int = 50, tool_capture: str = "") -> str: ...
```

#### AgentToolkit — Agent 协作

```python
class AgentToolkit(Toolkit):
    """Tools for multi-agent delegation.

    Attributes:
        agents: Pre-created sub-agent instances by name.
    """
    agents: dict

    def delegate(self, agent_name: str, task: str) -> str: ...
```

#### 迁移路径

| 旧 | 新 | 变更 |
|----|-----|------|
| `EssentialTools` | `ModuleToolkit` + `LogToolkit` | 拆分，各自持有所需依赖 |
| `DelegateTool` | `AgentToolkit` | 重命名，继承 Toolkit |
| `essential_tools.py` | `module_toolkit.py` + `log_toolkit.py` | 声明文件拆分 |
| `delegate.py` | `agent_toolkit.py` | 声明文件重命名 |
| 各 `_impl.py` | `@impl` 目标类名更新 | 如 `@impl(ModuleToolkit.define_module)` |

初始化代码变更（main_impl.py）：

```python
# 之前
tools = EssentialTools(module_manager=mm, log_store=ls)
tool_set.add(tools)
delegate_tool = DelegateTool(agents=sub_agents)
tool_set.add(delegate_tool, methods=["delegate"])

# 之后
module_tools = ModuleToolkit(module_manager=mm)
log_tools = LogToolkit(log_store=ls)
tool_set.add(module_tools)
tool_set.add(log_tools)

agent_toolkit = AgentToolkit(agents=sub_agents)
tool_set.add(agent_toolkit)
```

### 2.4 ToolSet 自动发现

#### 发现机制

ToolSet 增加 `auto_discover` 标志。当为 `True` 时，`get_tools()` 调用前自动扫描 mutobj 的 `_class_registry`，找到所有 Toolkit 子类，为其公开方法生成工具条目。

```python
from mutobj.core import _class_registry

def _discover_toolkit_classes():
    """扫描 _class_registry，返回所有 Toolkit 子类（不含 Toolkit 自身）。"""
    from mutagent.toolkit import Toolkit
    return [
        cls for (module_name, qualname), cls in _class_registry.items()
        if cls is not Toolkit and issubclass(cls, Toolkit)
    ]
```

#### 实例管理

ToolSet 维护实例缓存 `_toolkit_instances: dict[type, object]`：

- **预注册**（通过 `add(instance)`）：实例放入缓存，方法注册到 `_entries`。ToolSet 记录该类为"已手动添加"（`_added_classes` 集合），自动发现时跳过。
- **自动发现**：对未在缓存中的 Toolkit 子类，尝试 `cls()` 无参实例化。失败则跳过（需要构造参数的类必须预注册）。

#### 刷新逻辑

`get_tools()` 调用时，如果 `auto_discover` 为 True：

1. 扫描 `_class_registry` 中所有 Toolkit 子类
2. `_added_classes` 中的类 → 跳过
3. 已缓存的类 → 检查模块版本（`ModuleManager.get_version()`），版本变化则重新生成 schema
4. 新发现的类 → 无参实例化，缓存，注册方法
5. 不再存在的类 → 清理条目

名称冲突：自动发现的工具与预注册工具同名时跳过，记录 WARNING。

#### ToolSet 声明变更

```python
class ToolSet(mutagent.Declaration):
    auto_discover: bool  # 是否启用 Toolkit 自动发现，默认 False

    # 现有接口不变
    def add(self, source, methods=None) -> None: ...
    def remove(self, tool_name) -> bool: ...
    def query(self, tool_name) -> ToolSchema | None: ...
    def get_tools(self) -> list[ToolSchema]: ...
    def dispatch(self, tool_call) -> ToolResult: ...
```

System Agent 设置 `auto_discover = True`。Sub-Agent 保持 `False`。

### 2.5 延迟绑定

**问题**：`ToolEntry.callable` 存储绑定方法引用。`define_module` 更新类后，已有 ToolEntry 仍调用旧代码。虽然 DeclarationMeta 原地更新类对象，但 Python 的 bound method 是快照——其 `__func__` 仍指向旧函数。

**方案**：自动发现的工具使用延迟绑定包装器，通过 `getattr(instance, method_name)` 在调用时从更新后的类上解析方法。

```python
def _make_late_bound(instance, method_name):
    """创建延迟绑定包装器。"""
    def wrapper(**kwargs):
        return getattr(instance, method_name)(**kwargs)
    actual = getattr(instance, method_name)
    wrapper.__name__ = method_name
    wrapper.__doc__ = actual.__doc__
    wrapper.__annotations__ = getattr(actual, '__annotations__', {})
    return wrapper
```

- 实现变更（签名不变）→ 下次调用自动使用新代码
- 签名变更 → 下次 `get_tools()` 刷新时重新生成 schema
- 预注册的静态工具保持直接绑定，不受影响

### 2.6 Phase 1a: max_tool_rounds

详见 `feature-tool-loop-control.md`：

- `Agent` 声明增加 `max_tool_rounds: int`（默认 25）
- `agent_impl.py` 内层 while 循环增加计数器
- 达到上限时注入 `[System] Tool call limit reached. Summarize your progress.`，做最后一次 LLM 调用获取总结，break
- 通过 `config.json` 的 `agent.max_tool_rounds` 可覆盖

### 2.7 Phase 1b: Schema 生成重构

详见 `feature-tool-schema-annotation.md`。用 `inspect.signature()` + docstring 解析替代 AST。

#### Docstring Args 解析器

```python
def parse_docstring(docstring: str) -> tuple[str, dict[str, str]]:
    """解析 Google style docstring。
    Returns: (description, {param_name: param_description})
    """
```

#### Schema 生成函数

```python
def make_schema(func, name: str | None = None) -> ToolSchema:
    """从函数的签名和 docstring 生成 ToolSchema。
    1. inspect.signature(func) → 参数名、类型注解、默认值
    2. parse_docstring(func.__doc__) → 描述、参数描述
    3. 组装为 ToolSchema
    """
```

#### @impl 替换的处理

被 `@impl` 替换的方法通过 mutobj `_impl_chain` 获取原始声明方法：

```python
from mutobj.core import _impl_chain

def get_declaration_method(cls, method_name):
    """获取声明中定义的原始方法。"""
    chain = _impl_chain.get((cls, method_name), [])
    for func, source_module, seq in chain:
        if source_module == "__default__":
            return func
    return getattr(cls, method_name)
```

Agent 动态创建的 Toolkit 子类（无 @impl）直接 `inspect.signature()` 即可。

新的 `make_schema()` 替换现有的 `selector_impl.make_schema_from_method` 和 `tool_set_impl._make_schema_from_function`。

### 2.8 完整工具迭代流程

#### 基本流程

```
User: "我需要一个能统计文本中词频的工具"

Agent:

1. define_module("text_stats", """
   import mutagent

   class TextStats(mutagent.Toolkit):
       def word_frequency(self, text: str, top_n: int = 10) -> str:
           '''Count word frequencies in text.

           Args:
               text: Input text to analyze.
               top_n: Number of top words to return. Default 10.

           Returns:
               Formatted word frequency table.
           '''
           from collections import Counter
           words = text.lower().split()
           counts = Counter(words).most_common(top_n)
           return '\\n'.join(f'{w}: {c}' for w, c in counts)
   """)
   → "OK: text_stats defined (v1)"
   → ToolSet 自动发现 TextStats，word_frequency 成为可用工具

2. word_frequency(text="the cat sat on the mat the cat", top_n=3)
   → "the: 3\ncat: 2\nsat: 1"

3. save_module("text_stats")
   → "OK: text_stats saved to .mutagent/text_stats.py"
```

#### 错误修复流程

```
2. word_frequency(text="hello world hello", top_n=2)
   → "Exception: TypeError: ..."

3. define_module("text_stats", """... fixed code ...""")
   → "OK: text_stats defined (v2)"

4. word_frequency(text="hello world hello", top_n=2)
   → "hello: 2\nworld: 1"  ← 延迟绑定自动使用新代码

5. save_module("text_stats")
```

#### 测试函数模式

```
1. define_module("text_stats_test", """
   import mutagent
   from text_stats import TextStats

   class TextStatsTest(mutagent.Toolkit):
       def test_word_frequency(self) -> str:
           '''Run tests for word_frequency.'''
           tools = TextStats()
           result = tools.word_frequency(text="a b a", top_n=2)
           assert "a: 2" in result, f"Basic failed: {result}"
           result = tools.word_frequency(text="", top_n=5)
           assert result == "", f"Empty failed: {result}"
           return "All tests passed"
   """)

2. test_word_frequency()
   → "All tests passed" 或 "AssertionError: ..."

3. save_module("text_stats")
```

### 2.9 系统提示更新

在 SYSTEM_PROMPT 中更新工具说明和新增工具开发指引：

```
## Core Tools
- **inspect_module(module_path, depth)** — Browse module structure
- **view_source(target)** — Read source code
- **define_module(module_path, source)** — Define/redefine a module in memory
- **save_module(module_path, level)** — Persist module to disk
- **query_logs(pattern, level, limit, tool_capture)** — Search logs
- **delegate(agent_name, task)** — Delegate task to a sub-agent

## Tool Development
To create a new tool, define a Toolkit subclass:

    define_module("my_tools", """
    import mutagent

    class MyTools(mutagent.Toolkit):
        def my_tool(self, arg: str) -> str:
            '''Tool description.

            Args:
                arg: Argument description.

            Returns:
                Result description.
            '''
            return ...
    """)

The tool is automatically available after define_module. Test it by calling it directly.
If the result is wrong, redefine the module — changes take effect immediately.
Once validated, save_module to persist.

Rules:
- Every tool method MUST have type annotations and a Google-style docstring with Args section
- The docstring is shown to you as the tool description — write it clearly
- Test with diverse inputs before saving
- Keep one Toolkit class per module for clarity
```

### 2.10 Phase 3: 持久化

`save_module` 保存包含 Toolkit 子类的模块时，自动将模块名追加到项目级 config 的 `modules` 列表。启动时 `main.py` 引导流程已会加载 `modules` 中的模块，Toolkit 子类自动进入 `_class_registry` 并被发现。

复用现有 config modules 机制，无需额外 `tool_registry.json`。

## 3. 设计决策记录

| # | 决策 | 理由 |
|---|------|------|
| 1 | 基类命名 `Toolkit` | 与 `ToolSet` 形成职责分离：Toolkit = 提供工具，ToolSet = 管理分发 |
| 2 | EssentialTools 拆分为 ModuleToolkit + LogToolkit | 按职责分类，各自持有所需依赖 |
| 3 | DelegateTool 重命名为 AgentToolkit | 统一继承 Toolkit，纳入工具分类体系 |
| 4 | Schema 缓存 + 版本检查 | 首次生成并缓存，模块版本变化时重新生成 |
| 5 | feature-dynamic-tool-registration.md 删除 | run_function 被 Toolkit 自测模式替代，ToolCatalog 被自动发现替代 |
| 6 | Phase 1b 先于 Phase 2 | Schema 重构为自动发现提供基础，顺序完成效率更高 |

## 4. 实施步骤清单

### Phase 1a: max_tool_rounds [✅ 已完成]

- [x] **Task 1a.1**: `agent.py` 增加 `max_tool_rounds` 声明属性（默认 25）
  - 状态：✅ 已完成

- [x] **Task 1a.2**: `agent_impl.py` 内层循环增加计数和上限检查
  - [x] 增加 `tool_round` 计数器
  - [x] 达到上限时注入提示消息并做最后一次 LLM 调用
  - [x] 记录 WARNING 日志
  - 状态：✅ 已完成

- [x] **Task 1a.3**: 测试用例（5 个测试通过）
  - [x] 达到上限时循环中断并返回总结
  - [x] 未达上限时行为不变（回归）
  - [x] 自定义 max_tool_rounds 生效
  - 状态：✅ 已完成

- [x] **Task 1a.4**: SYSTEM_PROMPT 增加 Task Discipline 部分
  - [x] 任务收敛约束
  - [x] define_module 仅用于代码
  - [x] 代码质量检查提醒
  - 状态：✅ 已完成

### Phase 1b: Schema 生成重构 [✅ 已完成]

- [x] **Task 1b.1**: 实现 `parse_docstring()` — Google style docstring 解析器
  - [x] 解析 description 和 Args 段落
  - [x] 支持多行参数描述（缩进续行）
  - [x] 测试用例（8 个测试通过）
  - 状态：✅ 已完成

- [x] **Task 1b.2**: 实现 `make_schema(func)` — 基于 signature + docstring 的 Schema 生成
  - [x] `inspect.signature()` 提取参数名、类型注解、默认值
  - [x] `parse_docstring()` 提取描述和参数描述
  - [x] 组装为 ToolSchema
  - [x] 处理 `from __future__ import annotations`（字符串注解）
  - 状态：✅ 已完成

- [x] **Task 1b.3**: 实现 `get_declaration_method()` — 从 `_impl_chain` 获取原始声明方法
  - 状态：✅ 已完成

- [x] **Task 1b.4**: 迁移 ToolSet.add() 使用新的 `make_schema()`
  - [x] 替换 `selector_impl.make_schema_from_method`
  - [x] 替换 `tool_set_impl._make_schema_from_function`
  - [x] 确保现有测试全部通过（331 passed, 2 skipped）
  - 状态：✅ 已完成

### Phase 2: Toolkit 与自动发现 [待开始]

- [ ] **Task 2.1**: 定义 Toolkit 基类
  - [ ] 创建 `src/mutagent/toolkit.py`
  - [ ] 在 `mutagent/__init__.py` 中导出 `Toolkit`
  - 状态：⏸️ 待开始

- [ ] **Task 2.2**: 工具分类重组
  - [ ] 创建 `module_toolkit.py` — ModuleToolkit 声明（inspect_module, view_source, define_module, save_module）
  - [ ] 创建 `log_toolkit.py` — LogToolkit 声明（query_logs）
  - [ ] 重命名 `delegate.py` → `agent_toolkit.py` — AgentToolkit 声明（delegate）
  - [ ] 更新各 `_impl.py` 的 `@impl` 目标（如 `@impl(ModuleToolkit.define_module)`）
  - [ ] 删除 `essential_tools.py` 和 `delegate.py`
  - [ ] 更新 `builtins/__init__.py` 导入
  - [ ] 确保现有测试通过（回归）
  - 状态：⏸️ 待开始

- [ ] **Task 2.3**: ToolSet 自动发现实现
  - [ ] `auto_discover` 声明属性（默认 False）
  - [ ] `_toolkit_instances` 实例缓存
  - [ ] `_added_classes` 手动添加类集合
  - [ ] `_discover_toolkit_classes()` 扫描 `_class_registry`
  - [ ] `_refresh_toolkit_entries()` 刷新逻辑（含版本检查）
  - [ ] `get_tools()` 调用前触发刷新
  - [ ] `dispatch()` 查找所有工具（静态 + 自动发现）
  - 状态：⏸️ 待开始

- [ ] **Task 2.4**: 延迟绑定
  - [ ] `_make_late_bound(instance, method_name)` 包装器
  - [ ] 自动发现的工具使用延迟绑定
  - 状态：⏸️ 待开始

- [ ] **Task 2.5**: main_impl.py 集成
  - [ ] 初始化代码迁移：ModuleToolkit + LogToolkit + AgentToolkit
  - [ ] System Agent 的 ToolSet 设置 `auto_discover = True`
  - [ ] Sub-Agent 的 ToolSet 保持 `auto_discover = False`
  - [ ] 更新 SYSTEM_PROMPT
  - 状态：⏸️ 待开始

- [ ] **Task 2.6**: 测试用例
  - [ ] Toolkit 子类的公开方法自动发现为工具
  - [ ] 以 `_` 开头的方法不被发现
  - [ ] 预注册工具不被自动发现重复注册
  - [ ] 无参数构造的 Toolkit 子类自动实例化
  - [ ] 需要参数的 Toolkit 子类自动发现时跳过
  - [ ] 延迟绑定：define_module 后调用反映新代码
  - [ ] define_module 增删方法后刷新正确
  - [ ] 名称冲突：自动发现的工具不覆盖预注册工具
  - [ ] auto_discover=False 时不进行自动发现
  - [ ] 完整迭代流程：define → auto-discover → call → redefine → call → save
  - 状态：⏸️ 待开始

- [ ] **Task 2.7**: 清理旧文档
  - [ ] 删除 `feature-dynamic-tool-registration.md`
  - [ ] 删除 `feature-tool-loop-control.md`（内容已合并）
  - [ ] 删除 `feature-tool-schema-annotation.md`（内容已合并）
  - 状态：⏸️ 待开始

### Phase 3: 持久化 [待开始]

- [ ] **Task 3.1**: save_module 联动 config modules
  - [ ] save_module 检测被保存模块是否包含 Toolkit 子类
  - [ ] 如包含，自动将模块名追加到项目级 config 的 `modules` 列表
  - [ ] 重启后自动加载并被 auto-discover 发现
  - [ ] 测试用例
  - 状态：⏸️ 待开始

## 5. 测试验证

### 单元测试

| 测试用例 | 场景 | 预期行为 |
|----------|------|----------|
| `test_toolkit_auto_discover` | define_module 创建 Toolkit 子类 | 公开方法自动出现在 get_tools() |
| `test_toolkit_private_excluded` | Toolkit 子类有 `_helper` 方法 | 不出现在工具列表 |
| `test_toolkit_no_arg_instantiation` | 无构造参数的 Toolkit 子类 | 自动实例化并注册 |
| `test_toolkit_skip_complex_ctor` | 有构造参数的 Toolkit 子类 | 跳过，不报错 |
| `test_pre_registered_not_duplicated` | ModuleToolkit 已 add() | auto-discover 跳过 |
| `test_late_binding_update` | define_module 更新实现 | 调用反映新代码 |
| `test_late_binding_add_method` | define_module 增加新方法 | 刷新后新方法可用 |
| `test_late_binding_remove_method` | define_module 删除方法 | 刷新后旧方法不可用 |
| `test_name_conflict_protection` | 自动发现工具与预注册工具同名 | 跳过，保留预注册 |
| `test_auto_discover_flag_off` | auto_discover = False | 不扫描 |
| `test_schema_from_docstring` | 方法有完整 docstring | Schema 包含参数描述 |
| `test_module_toolkit_tools` | ModuleToolkit 注册 | 4 个模块管理工具可用 |
| `test_log_toolkit_tools` | LogToolkit 注册 | query_logs 可用 |
| `test_agent_toolkit_tools` | AgentToolkit 注册 | delegate 可用 |

### 集成测试

| 测试场景 | 描述 |
|----------|------|
| 完整迭代流程 | define(Toolkit 子类) → auto-discover → call → redefine → call → save |
| 测试函数模式 | 创建测试 Toolkit → 运行测试方法 → 验证结果 |
| Sub-Agent 隔离 | system agent 的动态工具不泄漏到 sub-agent |
| Schema 刷新 | define_module 改变签名后 get_tools() 返回更新的 schema |
| 工具分类回归 | 拆分后所有原有工具功能不变 |
