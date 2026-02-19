# Agent 自我演化健壮性改进 设计规范

**状态**：✅ 已完成
**日期**：2026-02-17
**类型**：功能设计

## 1. 背景

在会话 `20260217_085924` 的实际测试中（模型 `ark-code-latest`），除已修复的 `stop_reason` 判断 bug 外，还暴露了以下问题：

### 问题清单

| # | 问题 | 优先级 | 类别 |
|---|------|--------|------|
| 1 | Agent 直接覆写核心框架模块（`mutagent.agent`、`mutagent.builtins.agent_impl`） | P1 | 安全 |
| 2 | 移除 `run_code` 后 Agent 无法验证代码正确性 | P1 | 功能缺失 |
| 3 | Agent 创建的临时/测试模块无法清理，污染模块空间 | P2 | 设计缺陷 |
| 4 | Agent 行为冗余，24 次 API 调用完成一个简单功能 | P2 | 提示词 |

### 日志证据

**问题 1** — Agent 重写了核心框架模块：
```
445 | 09:05:55 INFO agent_impl - Executing tool: define_module
446 | 09:05:55 DEBUG agent_impl - Tool args: {'module_path': 'mutagent.builtins.agent_impl', 'source': '...'}
605 | 09:05:55 INFO agent_impl - Tool define_module result: ok (45 chars)
```
Agent 对 `mutagent.agent` 和 `mutagent.builtins.agent_impl` 执行了 `define_module` + `save_module`，整模块替换了框架核心代码。

**问题 2** — Agent 创建了 4 个测试模块但从未执行：
```
API #13: define_module(test_markdown)    → OK
API #15: define_module(run_markdown_test) → OK
API #19: define_module(markdown_demo)    → OK
API #21: define_module(quick_test)       → OK
```
这些模块包含 `assert`、`print()` 测试代码，但 Agent 没有执行它们的手段。

**问题 3** — 上述 4 个临时模块留在内存中未清理。

**问题 4** — 24 次 API 调用，其中多次是重复的 `inspect_module` / `view_source` 查看相同内容。

## 2. 设计方案

### 2.1 `define_module` 增加框架模块保护

**现状**：`define_module_impl.py` 对 `module_path` 不做任何验证。系统提示中有"Do NOT place new modules under the mutagent namespace"的文字引导，但 Agent 可以无视。

**方案**：在 `define_module` 实现中增加对 `mutagent.*` 命名空间的保护机制。

当 `module_path` 以 `mutagent.` 开头时：
- 记录 WARNING 日志
- 在返回消息中追加警告提示，让 Agent 意识到这是非常规操作
- **不阻断执行**（保留自我演化能力，但明确提醒）

```python
@mutagent.impl(EssentialTools.define_module)
def define_module(self, module_path, source):
    warning = ""
    if module_path.startswith("mutagent."):
        logger.warning("Redefining framework module: %s", module_path)
        warning = (
            "\n⚠ Warning: You are redefining a framework module. "
            "This replaces the entire module including all existing implementations. "
            "Consider using @impl to override specific methods instead."
        )
    try:
        self.module_manager.patch_module(module_path, source)
        version = self.module_manager.get_version(module_path)
        return f"OK: {module_path} defined (v{version}){warning}"
    except Exception as e:
        return f"Error defining {module_path}: {type(e).__name__}: {e}"
```

### 2.2 系统提示优化

在 `SYSTEM_PROMPT` 的 Module Naming 和 Self-Evolution 部分强化引导：

**Module Naming 部分增加**：
```
- NEVER redefine existing mutagent.* modules with define_module — this replaces the entire module.
  To change a specific behavior, create a new _impl module and use @impl to override just that method.
```

**Self-Evolution 部分强化**：
```
- To override a method: create a NEW module (e.g. "my_agent_impl") with @impl(Agent.run),
  then define_module + save_module. Do NOT redefine mutagent.agent or mutagent.builtins.*.
```

**Workflow 部分增加验证步骤的引导**：
```
After define_module, verify your changes work:
- Use inspect_module to confirm the module structure is correct.
- Use view_source to verify the code was applied as expected.
```

### 2.3 临时模块处理

**决策**：不新增 `undefine_module` 工具。临时内存模块只要不覆盖现有模块就不影响运行。

通过系统提示引导 Agent 不创建不必要的临时模块：
```
- Do NOT create throwaway test modules. Validate changes by inspecting and viewing source.
```

## 3. 已确认决策

- **框架模块保护级别**：仅警告不阻断，保留演化核心功能的能力。
- **代码执行能力**：暂不恢复 `run_code`。Agent 定义模块自动识别为新工具、以及覆盖现有模块的优雅方式，超出本迭代范围，后续新开 feature 讨论。
- **`undefine_module` 工具**：暂不新增。临时内存模块只要不覆盖现有模块就不影响，通过提示词引导解决。如有需求可作为后续 feature 的一项。

## 4. 实施步骤清单

### 阶段一：`define_module` 增加框架模块保护 [✅ 已完成]
- [x] **Task 1.1**: 修改 `define_module_impl.py`
  - [x] 增加 `mutagent.*` 模块路径检测和 WARNING 日志
  - [x] 返回消息中追加警告文本
  - 状态：✅ 已完成

- [x] **Task 1.2**: 新增测试用例
  - [x] 测试 `define_module("mutagent.xxx", ...)` 返回包含警告的成功消息
  - [x] 测试非 mutagent 模块不触发警告（回归）
  - 状态：✅ 已完成

### 阶段二：系统提示优化 [✅ 已完成]
- [x] **Task 2.1**: 修改 `main_impl.py` 中的 `SYSTEM_PROMPT`
  - [x] Module Naming 增加 NEVER redefine mutagent.* 引导
  - [x] Self-Evolution 强化 @impl 方式的引导
  - [x] Workflow 增加变更后验证引导
  - [x] 增加不要创建临时测试模块的引导
  - 状态：✅ 已完成

### 阶段三：测试验证 [✅ 已完成]
- [x] **Task 3.1**: 运行全量测试
  - [x] `pytest` 273 passed, 2 skipped
  - 状态：✅ 已完成

## 5. 测试验证

### 单元测试

| 测试用例 | 场景 | 预期行为 |
|----------|------|----------|
| `test_define_mutagent_module_warns` | `define_module("mutagent.foo", ...)` | 返回 OK + 警告文本，日志有 WARNING |
| `test_define_normal_module_no_warning` | `define_module("my_tool", ...)` | 返回 OK，无警告（回归） |

### 回归测试
- 执行结果：273 passed, 2 skipped (0.95s)
