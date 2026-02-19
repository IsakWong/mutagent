# 源码目录整理重构 设计规范

**状态**：✅ 已完成
**日期**：2026-02-19
**类型**：重构

## 1. 背景

经过多轮迭代（MVP → 模块管理 → 日志系统 → 多Agent → 工具演化闭环），`src/mutagent/` 顶层目录积累了 16 个 `.py` 文件，存在以下问题：

1. **Toolkit 相关文件与核心声明混杂**：`toolkit.py`、`module_toolkit.py`、`log_toolkit.py`、`agent_toolkit.py`、`tool_set.py`、`schema.py` 共 6 个工具子系统文件与 `agent.py`、`client.py` 等核心声明平铺在同一层级
2. **已废弃模块占据主目录**：`essential_tools.py`、`delegate.py`、`selector.py` 及对应的 `builtins/selector_impl.py` 仍在主目录，增加认知负担
3. **`schema.py` 位置突兀**：作为 ToolSet 的内部工具（`parse_docstring`、`make_schema`、`get_declaration_method`），却与顶层核心声明平级

### 当前目录结构（顶层 .py 文件）

```
src/mutagent/
├── __init__.py            # 包导出
├── __main__.py            # 入口
├── agent.py               # 核心：Agent 声明
├── client.py              # 核心：LLMClient 声明
├── config.py              # 核心：Config 声明
├── main.py                # 核心：App 声明 + main() 引导
├── messages.py            # 核心：数据模型
├── schema.py              # 工具子系统：Schema 生成
├── toolkit.py             # 工具子系统：Toolkit 基类（仅 30 行类定义）
├── tool_set.py            # 工具子系统：ToolSet 声明
├── module_toolkit.py      # 工具子系统：模块工具
├── log_toolkit.py         # 工具子系统：日志工具
├── agent_toolkit.py       # 工具子系统：委派工具
├── essential_tools.py     # 已废弃：旧版合并工具类
├── delegate.py            # 已废弃：AgentToolkit 别名
├── selector.py            # 已废弃：旧版 ToolSelector
├── builtins/
├── runtime/
└── cli/
```

## 2. 设计方案

### 2.1 目标结构

```
src/mutagent/
├── __init__.py            # 包导出（更新 Toolkit import 来源）
├── __main__.py            # 入口（不变）
├── agent.py               # 核心：Agent 声明
├── client.py              # 核心：LLMClient 声明
├── config.py              # 核心：Config 声明
├── main.py                # 核心：App 声明 + main() 引导
├── messages.py            # 核心：数据模型
├── tools.py               # 核心：Toolkit 基类 + ToolSet + ToolEntry（合并自 toolkit.py + tool_set.py）
│
├── toolkits/              # 命名空间包（无 __init__.py，同 cli/ 模式）
│   ├── module_toolkit.py  # ModuleToolkit 声明
│   ├── log_toolkit.py     # LogToolkit 声明
│   └── agent_toolkit.py   # AgentToolkit 声明
│
├── builtins/              # 实现层（更新 import 路径）
│   ├── __init__.py
│   ├── schema.py          # Schema 生成（实现细节，从顶层移入）
│   ├── agent_impl.py
│   ├── claude_impl.py
│   ├── config_impl.py
│   ├── main_impl.py
│   ├── tool_set_impl.py
│   ├── inspect_module_impl.py
│   ├── view_source_impl.py
│   ├── define_module_impl.py
│   ├── save_module_impl.py
│   ├── query_logs_impl.py
│   └── delegate_impl.py
│
├── runtime/               # 运行时基础设施（不变）
│   ├── __init__.py
│   ├── module_manager.py
│   ├── log_store.py
│   ├── api_recorder.py
│   └── log_query.py
│
└── cli/                   # CLI 工具（命名空间包，不变）
    └── log_query.py
```

### 2.2 核心设计决策

**合并 `toolkit.py` + `tool_set.py` → `tools.py`**：
- `toolkit.py` 仅 30 行（一个空的 `Toolkit(Declaration)` 基类 + docstring），不值得单独一个文件
- 合并后 `tools.py` 包含：`Toolkit` 基类、`ToolEntry` 数据类、`ToolSet` 声明，约 138 行
- 导入路径：`from mutagent.tools import Toolkit, ToolSet, ToolEntry`

**`schema.py` 移入 `builtins/`**：
- 顶层只保留核心概念（Agent、Client、Config、App、Messages、Tools），实现细节下沉
- `schema.py` 的唯一消费者是 `builtins/tool_set_impl.py`，移入后变为包内引用
- `builtins/` 本身就是实现细节的存放地，schema 作为工具注册的辅助逻辑归入自然
- 顶层精简为 6 个 `.py` 文件，每个都是 mutagent 的核心概念
- 规矩：未来新增的实现细节工具模块统一放入 `builtins/`

**Toolkit 子类提取到 `toolkits/` 命名空间包**：
- `module_toolkit.py`、`log_toolkit.py`、`agent_toolkit.py` 移入 `toolkits/`
- 无 `__init__.py`，与 `cli/` 保持一致的命名空间包模式
- 导入路径：`from mutagent.toolkits.module_toolkit import ModuleToolkit`

**废弃模块直接删除**：
- `essential_tools.py`、`delegate.py`、`selector.py` 直接删除（内部项目，无外部依赖）
- `builtins/selector_impl.py` 同步删除
- `test_selector.py` 删除，`test_essential_tools.py` 更新为直接测试 `ModuleToolkit`

**不保留旧路径 shim**：
- 所有内部 import 一步到位更新为新路径
- `mutagent.__init__` 的 `__all__` 保持不变（`Toolkit` 改从 `mutagent.tools` 导入后 re-export）

### 2.3 Import 路径迁移

| 旧路径 | 新路径 |
|--------|--------|
| `mutagent.toolkit.Toolkit` | `mutagent.tools.Toolkit` |
| `mutagent.tool_set.ToolSet` | `mutagent.tools.ToolSet` |
| `mutagent.tool_set.ToolEntry` | `mutagent.tools.ToolEntry` |
| `mutagent.schema.*` | `mutagent.builtins.schema.*` |
| `mutagent.module_toolkit.ModuleToolkit` | `mutagent.toolkits.module_toolkit.ModuleToolkit` |
| `mutagent.log_toolkit.LogToolkit` | `mutagent.toolkits.log_toolkit.LogToolkit` |
| `mutagent.agent_toolkit.AgentToolkit` | `mutagent.toolkits.agent_toolkit.AgentToolkit` |
| `mutagent.essential_tools.*` | 删除 |
| `mutagent.delegate.*` | 删除 |
| `mutagent.selector.*` | 删除 |

### 2.4 `tools.py` 合并内容

```python
"""mutagent.tools -- Toolkit base class and ToolSet declaration."""

import mutagent
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from mutagent.messages import ToolCall, ToolResult, ToolSchema


@dataclass
class ToolEntry:
    """A registered tool entry. ..."""
    name: str
    callable: Callable
    schema: ToolSchema
    source: Any


class ToolSet(mutagent.Declaration):
    """Tool set manager for an Agent. ..."""
    auto_discover: bool

    def add(self, source, methods=None): ...
    def remove(self, tool_name): ...
    def query(self, tool_name): ...
    def get_tools(self): ...
    def dispatch(self, tool_call): ...


class Toolkit(mutagent.Declaration):
    """Base class for tool providers. ..."""
    pass


from .builtins import tool_set_impl
mutagent.register_module_impls(tool_set_impl)
```

## 3. 待定问题

无。所有问题已确认。

## 4. 实施步骤清单

### 阶段一：合并 + 创建 `tools.py`，移动 `schema.py` [✅ 已完成]
- [x] **Task 1.1**: 合并 `toolkit.py` + `tool_set.py` → `tools.py`
  - [x] 将 `Toolkit` 类定义合入
  - [x] 保留 `ToolEntry` + `ToolSet` 声明
  - [x] 保留尾部 `register_module_impls(tool_set_impl)` 注册
  - 状态：✅ 已完成
- [x] **Task 1.2**: 删除原 `toolkit.py` 和 `tool_set.py`
  - 状态：✅ 已完成
- [x] **Task 1.3**: 移动 `schema.py` → `builtins/schema.py`
  - 状态：✅ 已完成

### 阶段二：创建 `toolkits/` 命名空间包 [✅ 已完成]
- [x] **Task 2.1**: 创建 `src/mutagent/toolkits/` 目录（无 `__init__.py`）
  - 状态：✅ 已完成
- [x] **Task 2.2**: 移动 toolkit 声明文件
  - [x] `module_toolkit.py` → `toolkits/module_toolkit.py`
  - [x] `log_toolkit.py` → `toolkits/log_toolkit.py`
  - [x] `agent_toolkit.py` → `toolkits/agent_toolkit.py`
  - 状态：✅ 已完成

### 阶段三：删除废弃模块 [✅ 已完成]
- [x] **Task 3.1**: 删除废弃源码文件
  - [x] `essential_tools.py`
  - [x] `delegate.py`
  - [x] `selector.py`
  - [x] `builtins/selector_impl.py`
  - 状态：✅ 已完成
- [x] **Task 3.2**: 删除废弃测试文件
  - [x] `test_selector.py`
  - 状态：✅ 已完成

### 阶段四：更新所有 import [✅ 已完成]
- [x] **Task 4.1**: 更新 `__init__.py`
  - [x] `from mutagent.toolkit import Toolkit` → `from mutagent.tools import Toolkit`
  - 状态：✅ 已完成
- [x] **Task 4.2**: 更新 `toolkits/` 内文件的 import
  - [x] 各文件 `from mutagent.toolkit import Toolkit` → `from mutagent.tools import Toolkit`
  - 状态：✅ 已完成
- [x] **Task 4.3**: 更新 `builtins/` 下所有 impl 文件的 import
  - [x] `tool_set_impl.py` — schema 改为 `from mutagent.builtins.schema import ...`，Toolkit 改为 `from mutagent.tools import Toolkit`
  - [x] `main_impl.py` — ModuleToolkit/LogToolkit/AgentToolkit/ToolSet 改为新路径
  - [x] `inspect_module_impl.py`、`view_source_impl.py`、`define_module_impl.py`、`save_module_impl.py` — ModuleToolkit 改为 `mutagent.toolkits.module_toolkit`
  - [x] `query_logs_impl.py` — LogToolkit 改为 `mutagent.toolkits.log_toolkit`
  - [x] `delegate_impl.py` — AgentToolkit 改为 `mutagent.toolkits.agent_toolkit`
  - [x] `agent_impl.py` — ToolEntry 改为 `from mutagent.tools import ToolEntry`
  - 状态：✅ 已完成
- [x] **Task 4.4**: 更新核心声明文件中的 import
  - [x] `agent.py` — ToolSet 的 TYPE_CHECKING import
  - 状态：✅ 已完成
- [x] **Task 4.5**: 更新测试文件的 import
  - [x] `test_agent.py` — ModuleToolkit, LogToolkit, ToolSet
  - [x] `test_e2e.py` — ModuleToolkit, ToolSet，修复嵌入源码中的旧路径引用
  - [x] `test_essential_tools.py` — 改为直接测试 ModuleToolkit（更新 import + 测试内容）
  - [x] `test_tool_set.py` — AgentToolkit, ToolSet, ModuleToolkit
  - [x] `test_logging.py` — LogToolkit
  - [x] `test_schema.py` — schema import + EssentialTools/DelegateTool 改为新类
  - 状态：✅ 已完成

### 阶段五：更新文档 + 验证 [✅ 已完成]
- [x] **Task 5.1**: 更新 `CLAUDE.md` 源码布局描述
  - 状态：✅ 已完成
- [x] **Task 5.2**: 运行全部测试验证（326 passed, 2 skipped）
  - 状态：✅ 已完成

## 5. 测试验证

### 单元测试
- [x] 全部现有测试通过（`pytest`）— 326 passed, 2 skipped
- [x] import 路径正确（无 `ModuleNotFoundError`）

### 集成测试
- [x] ToolSet auto_discover 仍能找到 `toolkits/` 下的 Toolkit 子类（test_tool_set 验证通过）
