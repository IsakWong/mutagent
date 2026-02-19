# 迁移 forwardpy → mutobj 设计规范

**状态**：✅ 已完成
**日期**：2026-02-16
**类型**：重构

## 1. 背景

mutagent 当前依赖 `forwardpy` 库提供声明-实现分离功能，并在 `base.py` 中自定义了 `MutagentMeta` 元类来支持 in-place 类重定义。

`mutobj` 是 `forwardpy` 的后继库（同一作者），已内置 in-place 类重定义支持（通过 `DeclarationMeta` 和 `_class_registry`）。迁移后可以删除 `base.py` 中的全部自定义实现。

参考：`D:\ai\mutobj\docs\migration-guide.md`

## 2. 设计方案

### 2.1 依赖替换

- `pyproject.toml`: `forwardpy>=0.3.0` → `mutobj>=0.4.0`

### 2.2 删除 base.py

不再保留 `mutagent.Object` 包装类，直接在 `__init__.py` 中从 `mutobj` 导入 `Declaration`。`base.py` 整个文件删除。

删除的代码：
- `_update_class_inplace()` — mutobj 内置
- `_migrate_forwardpy_registries()` — mutobj 内置
- `MutagentMeta` — 不再需要自定义元类
- `Object` — 不再需要包装类

### 2.3 __init__.py 更新

```python
"""mutagent - A Python AI Agent framework for runtime self-iterating code."""

__version__ = "0.1.0"

from mutobj import Declaration, impl

__all__ = ["Declaration", "impl"]
```

### 2.4 源码中全局替换

**`mutagent.Object` → `mutagent.Declaration`**（所有声明文件）：

| 文件 | 变更 |
|------|------|
| `agent.py` | `class Agent(mutagent.Object)` → `class Agent(mutagent.Declaration)` |
| `client.py` | `class LLMClient(mutagent.Object)` → `class LLMClient(mutagent.Declaration)` |
| `config.py` | `class Config(mutagent.Object)` → `class Config(mutagent.Declaration)` |
| `essential_tools.py` | `class EssentialTools(mutagent.Object)` → `class EssentialTools(mutagent.Declaration)` |
| `selector.py` | `class ToolSelector(mutagent.Object)` → `class ToolSelector(mutagent.Declaration)` |
| `main.py` | `class App(mutagent.Object)` → `class App(mutagent.Declaration)` |

**forwardpy → mutobj**（运行时引用）：

| 文件 | 旧 | 新 |
|------|----|----|
| `runtime/module_manager.py:59` | `from forwardpy import unregister_module_impls` | `from mutobj import unregister_module_impls` |
| `builtins/selector_impl.py:110` | 注释中 `forwardpy stubs` | 注释中 `mutobj stubs` |
| `builtins/main_impl.py` | system prompt 中 forwardpy/MutagentMeta → mutobj/DeclarationMeta |

### 2.5 测试文件更新

| 文件 | 变更 |
|------|------|
| `test_basic.py` | `import forwardpy` → `import mutobj`，`mutagent.Object` → `mutagent.Declaration`，验证 `impl is mutobj.impl` |
| `test_base.py` | 重写：删除 `MutagentMeta` 测试，`mutagent.Object` → `mutagent.Declaration`，`forwardpy.Object` → `mutobj.Declaration`，导入从 `mutobj.core` |
| `test_inplace_update.py` | `mutagent.Object` → `mutagent.Declaration`，`forwardpy.core` → `mutobj.core`，`MutagentMeta._class_registry` → `mutobj.core._class_registry` |
| `test_client.py` | `mutagent.Object` → `mutagent.Declaration`，`MutagentMeta` → `DeclarationMeta`，`forwardpy.core` → `mutobj.core` |
| `test_agent.py` | 同上 |
| `test_selector.py` | 同上 |
| `test_config.py` | 同上 |
| `test_e2e.py` | `import forwardpy` → `import mutobj`，`forwardpy.unregister_module_impls` → `mutobj.unregister_module_impls`，`mutagent.Object` → `mutagent.Declaration` |
| `test_module_manager.py` | `mutagent.Object` → `mutagent.Declaration`，删除 `MutagentMeta` 导入 |

### 2.6 CLAUDE.md 更新

- 依赖描述：`forwardpy>=0.3.0` → `mutobj>=0.4.0`
- 架构描述：`forwardpy` → `mutobj`
- `MutagentMeta` 相关描述更新为 `DeclarationMeta`（由 mutobj 提供）
- `mutagent.Object` → `mutagent.Declaration`

## 3. 待定问题

无。迁移指南已明确所有映射关系。

## 4. 实施步骤清单

### 阶段一：核心迁移 [✅ 已完成]
- [x] **Task 1.1**: 更新 `pyproject.toml` 依赖
  - [x] `forwardpy>=0.3.0` → `mutobj>=0.4.0`
  - 状态：✅ 已完成

- [x] **Task 1.2**: 删除 `base.py`
  - 状态：✅ 已完成

- [x] **Task 1.3**: 更新 `__init__.py`
  - [x] `from mutobj import Declaration, impl`
  - [x] 移除 `MutagentMeta` 和 `Object` 导出
  - 状态：✅ 已完成

- [x] **Task 1.4**: 更新所有声明文件中的 `mutagent.Object` → `mutagent.Declaration`
  - [x] agent.py, client.py, config.py, essential_tools.py, selector.py, main.py
  - 状态：✅ 已完成

- [x] **Task 1.5**: 更新 `runtime/module_manager.py`
  - [x] `from forwardpy import unregister_module_impls` → `from mutobj import unregister_module_impls`
  - 状态：✅ 已完成

- [x] **Task 1.6**: 更新 `builtins/main_impl.py` 和 `builtins/selector_impl.py`
  - [x] 更新注释和 system prompt 中的 forwardpy/MutagentMeta/mutagent.Object 引用
  - 状态：✅ 已完成

### 阶段二：测试迁移 [✅ 已完成]
- [x] **Task 2.1**: 更新所有测试文件
  - [x] test_basic.py
  - [x] test_base.py（重写）
  - [x] test_inplace_update.py
  - [x] test_client.py
  - [x] test_agent.py
  - [x] test_selector.py
  - [x] test_config.py
  - [x] test_e2e.py
  - [x] test_module_manager.py
  - 状态：✅ 已完成

### 阶段三：文档更新 [✅ 已完成]
- [x] **Task 3.1**: 更新 CLAUDE.md
  - 状态：✅ 已完成

### 阶段四：验证 [✅ 已完成]
- [x] **Task 4.1**: 安装依赖并运行全部测试
  - [x] `pip install -e ".[dev]"`
  - [x] `pytest` — 181 passed, 2 skipped
  - 状态：✅ 已完成

## 5. 测试验证

### 单元测试
- [x] pytest 全部通过
- 执行结果：181 passed, 2 skipped (real API tests), 0 failures

## 6. 遗留问题

- `docs/specifications/feature-mutagent-mvp.md` 中有大量 forwardpy 引用，属于历史文档，不在本次迁移范围内
