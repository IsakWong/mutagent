# 模块管理与工具改进 设计规范

**状态**：✅ 已完成
**日期**：2026-02-17
**类型**：功能设计

## 1. 背景

当前 mutagent 的核心工具集（EssentialTools）存在以下问题：

1. **`run_code` 工具被滥用**：AI 倾向于频繁使用 `run_code` 执行任意代码，而非通过 `patch_module` 进行结构化的模块定义。这破坏了"模块化自我演化"的设计理念。

2. **模块保存位置不合理**：`save_module` 默认保存到当前工作目录（`"."`），缺乏引导 AI 将新模块放在 `.mutagent/` 目录下的机制。

3. **工具命名空间混乱**：新建的工具模块容易被放在 `mutagent` 命名空间下，应按功能独立命名。

4. **子模块不能跨目录**：当前的 `save_module` 将 `a.b.c` 固定映射为 `directory/a/b/c.py`，不支持同一个包的子模块分布在不同目录（如 `~/.mutagent/utils/` 和 `./.mutagent/utils/`）。

5. **新建与重定义接口不统一**：`patch_module` 用于创建和修改，但命名未反映"定义"语义，且缺少未持久化模块的追踪能力。

## 2. 设计方案

### 2.1 移除 `run_code` 工具

从 EssentialTools 中移除 `run_code` 方法及其实现，同时更新：

- `essential_tools.py`：删除 `run_code` 方法声明
- `builtins/run_code_impl.py`：删除实现文件
- `builtins/selector_impl.py`：从 `_TOOL_METHODS` 列表中移除 `"run_code"`
- `main_impl.py`：更新 SYSTEM_PROMPT，移除对 `run_code` 的引用
- 测试文件：移除 `TestRunCode` 测试类

### 2.2 `define_module` 替代 `patch_module`（仅内存）

`define_module` 统一"新建模块"和"重定义模块"两种操作，**默认只在内存中生效**，不自动持久化到磁盘。模块存在于一个"内存虚拟文件系统"中，直到显式调用 `save_module` 才写入磁盘。

```python
def define_module(self, module_path: str, source: str) -> str:
    """定义或重新定义一个 Python 模块（仅内存）。

    在运行时注入模块代码。模块立即在内存中生效，但不会自动
    持久化到磁盘。使用 save_module 将验证通过的模块持久化。

    Args:
        module_path: 模块的点分路径（如 "utils.helpers"）。
            新模块使用功能性命名，不要放在 mutagent 命名空间下。
        source: 模块的 Python 源代码。

    Returns:
        状态消息，包含模块路径和版本号。
    """
```

**实现**：底层调用 `ModuleManager.patch_module()`，与当前 `patch_module` 工具行为一致。改名是为了统一"新建"和"重定义"的语义。

### 2.3 `save_module` 增加 `level` 参数

`save_module` 保留为独立工具，增加 `level` 参数控制持久化的目标层级：

```python
def save_module(self, module_path: str, level: str = "project") -> str:
    """将内存中的模块持久化到磁盘。

    Args:
        module_path: 要保存的模块的点分路径。
        level: 保存层级。
            - "project"（默认）：保存到项目级 ./.mutagent/ 目录
            - "user"：保存到用户级 ~/.mutagent/ 目录

    Returns:
        状态消息，包含写入的文件路径。
    """
```

**路径映射**：

| level | 根目录 | 示例（`utils.helpers`） |
|-------|--------|------------------------|
| `"project"` | `./.mutagent/` | `./.mutagent/utils/helpers.py` |
| `"user"` | `~/.mutagent/` | `~/.mutagent/utils/helpers.py` |

- 不创建 `__init__.py`，保持命名空间包语义
- 自动创建所需的父目录（`mkdir -p` 语义）

### 2.4 未持久化模块列表

在 `ModuleManager` 中追踪模块的持久化状态，提供查询 API：

**ModuleManager 新增**：

```python
class ModuleManager:
    def __init__(self) -> None:
        ...
        self._saved_paths: dict[str, Path] = {}  # module_path → 已保存的文件路径

    def get_unsaved_modules(self) -> list[str]:
        """返回所有已 patch 但尚未 save 的模块路径列表。"""
        return [m for m in self._patched_modules if m not in self._saved_paths]

    def save_module(self, module_path: str, directory: str | Path) -> Path:
        ...
        # 保存成功后记录
        self._saved_paths[module_path] = file_path
        return file_path
```

**工具层暴露**：将未持久化列表整合到 `inspect_module` 的输出中。当 `inspect_module()` 无参调用时，在输出顶部展示未保存模块列表：

```
[Unsaved modules]
  utils.helpers (v2)
  web_search.google (v1)

mutagent/
  agent.py — Agent class
  ...
```

### 2.5 工具集总览

| 旧工具 | 新工具 | 说明 |
|--------|--------|------|
| `inspect_module` | `inspect_module` | 增强：无参调用时展示未保存模块列表 |
| `view_source` | `view_source` | 保留不变 |
| `patch_module` | `define_module` | 改名 + 统一语义（仅内存） |
| `save_module` | `save_module` | 增强：新增 `level` 参数，默认保存到 `.mutagent/` |
| `run_code` | ~~移除~~ | 去掉 |

工具总数：4 个（从 5 个减少到 4 个）。

### 2.6 `.mutagent/` 目录自动加入 `sys.path`

在 `App.load_config()` 阶段，自动将以下目录加入 `sys.path`：

- `~/.mutagent/`（用户级，低优先级）
- `./.mutagent/`（项目级，高优先级）

顺序很重要：项目级在 `sys.path` 中排在用户级前面，使项目级模块在 `import` 时优先于用户级。

### 2.7 跨目录子模块支持（命名空间包）

利用 Python 的**隐式命名空间包**（PEP 420）机制。

**策略**：

- `.mutagent/` 下的子目录**不创建** `__init__.py`，不支持包级 `__init__` 代码，保持纯粹的命名空间包设计
- 依赖 Python 的命名空间包自动发现机制
- 例如 `~/.mutagent/utils/helpers.py` 和 `./.mutagent/utils/formatters.py` 都可以作为 `utils` 包的子模块被导入

**对 `ModuleManager._ensure_parent_packages()` 的影响**：

创建虚拟父包时，扫描所有 `.mutagent/` 目录（用户级 + 项目级）中是否存在同名目录，将它们都加入 `__path__`。如果 `sys.path` 中其他路径下也存在同名的真实包，也应尊重其 `__path__`。

### 2.8 `save_module` 路径解析（ModuleManager 层）

`ModuleManager.save_module()` 的路径解析逻辑：

- **始终由 `directory` 参数决定根目录**：`module_path` 映射为 `directory/<parts>.py`
- 不检查模块是否已有 `__file__`——保存层级完全由调用方（工具层的 `level` 参数）决定
- 保存后不创建 `__init__.py`

工具层（`save_module_impl.py`）负责将 `level` 翻译为具体目录：
- `"project"` → `Path.cwd() / ".mutagent"`
- `"user"` → `Path.home() / ".mutagent"`

### 2.9 System Prompt 更新

更新 `main_impl.py` 中的 `SYSTEM_PROMPT`，引导 AI：

- 使用 `define_module` 定义/修改模块（仅内存生效）
- 使用 `save_module` 显式持久化（默认项目级，可选用户级）
- 新建模块使用功能性的模块名（如 `web_search`、`file_utils`），而非 `mutagent.xxx`
- 不使用 `run_code`（工具已移除）
- 工作流：`inspect_module` → `view_source` → `define_module` → `inspect_module`（查看未保存列表）→ `save_module`

## 3. 已确认决策

- **Q2 `.mutagent/` 目录自动创建**：✅ 确认。`save_module` 首次保存时自动创建目录。
- **Q3 命名空间包策略**：✅ 确认。不支持 `__init__.py`，保持纯粹的命名空间包设计。
- **Q4 虚拟包 `__path__` 多目录**：✅ 确认。扫描所有 `.mutagent/` 目录设置 `__path__`。
- **Q5 新模块默认保存到项目级**：✅ 确认。`save_module` 默认 `level="project"`。
- **Q6 重定义已有模块保存到 `.mutagent/`**：✅ 确认。不覆盖安装包原始代码，利用 `sys.path` 优先级使新版本生效。
- **Q7 未持久化列表整合到 `inspect_module`**：✅ 确认。无参调用时在输出顶部展示 `[Unsaved modules]`。
- **Q8 重定义已加载真实模块的行为**：✅ 确认。暂不特殊处理，依赖 `sys.path` 优先级和 `modules` 配置。

## 4. 待定问题

（无）

## 5. 实施步骤清单

### 阶段一：移除 `run_code` [✅ 已完成]

- [x] **Task 1.1**: 移除 `run_code` 声明和实现
  - [x] 从 `essential_tools.py` 中删除 `run_code` 方法
  - [x] 删除 `builtins/run_code_impl.py`
  - [x] 更新 `essential_tools.py` 末尾的 import 和 `register_module_impls` 调用
  - 状态：✅ 已完成

- [x] **Task 1.2**: 更新工具注册
  - [x] 从 `selector_impl.py` 的 `_TOOL_METHODS` 中移除 `"run_code"`
  - 状态：✅ 已完成

- [x] **Task 1.3**: 更新 System Prompt
  - [x] 修改 `main_impl.py` 中的 `SYSTEM_PROMPT`，移除 `run_code` 引用
  - 状态：✅ 已完成

- [x] **Task 1.4**: 更新测试
  - [x] 从 `tests/test_essential_tools.py` 中移除 `TestRunCode` 类
  - [x] 修复所有引用 `run_code` 的测试（test_agent.py, test_e2e.py, test_selector.py）
  - [x] 确保所有测试通过
  - 状态：✅ 已完成

### 阶段二：`define_module` 替代 `patch_module` [✅ 已完成]

- [x] **Task 2.1**: 重命名工具声明
  - [x] 在 `essential_tools.py` 中将 `patch_module` 改为 `define_module`
  - [x] 更新 import 和 `register_module_impls`
  - 状态：✅ 已完成

- [x] **Task 2.2**: 实现 `define_module`
  - [x] 创建 `builtins/define_module_impl.py`（从 `patch_module_impl.py` 重命名并更新）
  - [x] 删除 `builtins/patch_module_impl.py`
  - 状态：✅ 已完成

- [x] **Task 2.3**: 更新工具注册
  - [x] 更新 `selector_impl.py` 的 `_TOOL_METHODS`：`"patch_module"` → `"define_module"`
  - 状态：✅ 已完成

### 阶段三：增强 `save_module` [✅ 已完成]

- [x] **Task 3.1**: 修改 `save_module` 工具声明
  - [x] 在 `essential_tools.py` 中更新签名：`save_module(module_path, level="project")`
  - [x] 移除旧的 `file_path` 参数
  - 状态：✅ 已完成

- [x] **Task 3.2**: 实现 `level` 参数路径映射
  - [x] 更新 `builtins/save_module_impl.py`：将 `level` 翻译为 `.mutagent/` 目录
  - [x] `"project"` → `Path.cwd() / ".mutagent"`，`"user"` → `Path.home() / ".mutagent"`
  - [x] 自动创建目标目录（`mkdir -p`）
  - 状态：✅ 已完成

- [x] **Task 3.3**: `ModuleManager` 追踪持久化状态
  - [x] 新增 `_saved_paths` 字典
  - [x] `save_module()` 成功后记录路径
  - [x] 新增 `get_unsaved_modules()` 方法
  - [x] `cleanup()` 时清理 `_saved_paths`
  - 状态：✅ 已完成

- [x] **Task 3.4**: `ModuleManager.save_module()` 不创建 `__init__.py`
  - [x] 确认当前实现不创建 `__init__.py`（仅 `mkdir`）
  - [x] 保持命名空间包语义
  - 状态：✅ 已完成

### 阶段四：`inspect_module` 展示未保存模块 [✅ 已完成]

- [x] **Task 4.1**: 增强 `inspect_module` 输出
  - [x] 无参调用时，在输出顶部展示未保存模块列表（含版本号）
  - [x] 修改 `builtins/inspect_module_impl.py`
  - 状态：✅ 已完成

### 阶段五：`.mutagent/` 路径自动注册与命名空间包 [✅ 已完成]

- [x] **Task 5.1**: 自动注册 `.mutagent/` 到 `sys.path`
  - [x] 修改 `main_impl.py` 的 `load_config()`
  - [x] 用户级 `~/.mutagent/` 和项目级 `./.mutagent/` 都自动加入
  - [x] 项目级优先（在 `sys.path` 中排在前面）
  - 状态：✅ 已完成

- [x] **Task 5.2**: 改进 `ModuleManager._ensure_parent_packages()` 支持多路径
  - [x] `ModuleManager` 新增 `search_dirs` 构造参数
  - [x] 新增 `_build_package_path()` 方法扫描目录设置 `__path__`
  - [x] `setup_agent()` 传入 `.mutagent/` 目录列表
  - 状态：✅ 已完成

### 阶段六：System Prompt 更新 [✅ 已完成]

- [x] **Task 6.1**: 更新 `SYSTEM_PROMPT`
  - [x] 反映新工具集（4 个工具）
  - [x] 引导功能性命名而非 `mutagent.xxx`
  - [x] 描述新的工作流（define → inspect unsaved → save）
  - [x] 添加模块命名指南和命名空间包说明
  - 状态：✅ 已完成

### 阶段七：测试 [✅ 已完成]

- [x] **Task 7.1**: 编写 `define_module` 工具测试
  - [x] 新建模块（内存生效，不自动保存）
  - [x] 重定义已有模块
  - [x] 版本号递增
  - 状态：✅ 已完成

- [x] **Task 7.2**: 编写 `save_module` level 参数测试
  - [x] `level="project"` 保存到 `./.mutagent/`
  - [x] `level="user"` 保存到 `~/.mutagent/`
  - [x] 自动创建目录
  - [x] 保存后不创建 `__init__.py`
  - [x] 非法 level 返回错误
  - 状态：✅ 已完成

- [x] **Task 7.3**: 编写未持久化模块追踪测试
  - [x] `get_unsaved_modules()` 返回正确列表
  - [x] `save_module` 后模块从列表中移除
  - [x] `inspect_module` 无参调用展示未保存列表（含版本号）
  - [x] 全部已保存时不显示 `[Unsaved modules]`
  - 状态：✅ 已完成

- [x] **Task 7.4**: 编写命名空间包跨目录测试
  - [x] `_build_package_path` 扫描多个 search_dirs
  - [x] 不存在的目录被跳过
  - [x] 虚拟包的 `__path__` 包含正确的目录
  - [x] 无 search_dirs 时 `__path__` 为空列表
  - [x] 嵌套包路径正确
  - 状态：✅ 已完成

- [x] **Task 7.5**: 更新现有测试
  - [x] 适配 `patch_module` → `define_module` 改名
  - [x] 适配 `save_module` 签名变更
  - [x] 适配 e2e 测试（移除 run_code 步骤、更新工具名和参数）
  - [x] 确保所有 194 个测试通过
  - 状态：✅ 已完成

---

### 实施进度总结
- ✅ **阶段一：移除 run_code** - 100% 完成
- ✅ **阶段二：define_module** - 100% 完成
- ✅ **阶段三：增强 save_module** - 100% 完成
- ✅ **阶段四：inspect_module 未保存列表** - 100% 完成
- ✅ **阶段五：路径注册与命名空间包** - 100% 完成
- ✅ **阶段六：System Prompt** - 100% 完成
- ✅ **阶段七：测试** - 100% 完成

**全部测试：194 通过，2 跳过（Claude API 真实调用测试）**

## 6. 测试验证

### 单元测试
- [x] `define_module` 仅内存生效，模块可用但未持久化
- [x] `define_module` 重定义已有模块，版本递增
- [x] `save_module(level="project")` 保存到正确路径
- [x] `save_module(level="user")` 保存到正确路径
- [x] `save_module` 自动创建 `.mutagent/` 和子目录
- [x] `save_module` 不创建 `__init__.py`
- [x] `get_unsaved_modules()` 正确追踪状态
- [x] `inspect_module()` 展示未保存模块列表
- [x] 命名空间包 `_build_package_path` 扫描多目录
- [x] `_ensure_parent_packages` 多路径 `__path__`
- [x] 移除 `run_code` 后工具列表正确（4 个工具）
- 执行结果：194/194 通过

### 集成测试
- [x] 完整的 define → inspect（查看未保存）→ save 流程
- [x] e2e 测试适配新工具集
- 执行结果：全部通过
