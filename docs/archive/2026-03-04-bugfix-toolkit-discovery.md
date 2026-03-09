# Toolkit 发现机制修复

**状态**：✅ 已完成
**日期**：2026-03-04
**类型**：Bug修复

## 背景

Toolkit auto-discover 机制存在三个问题，在 `UI-show` 工具测试中暴露（session b0e51eaf）：

1. **async 方法未 await**：`_make_late_bound()` 创建 sync wrapper，dispatch 检测不到 async，返回 `<coroutine object>`
2. **缺乏发现控制**：没有字段让 Toolkit 声明自己不应被 auto-discover（如基类 UIToolkitBase 不希望暴露为工具）
3. **缺乏方法级控制**（后续增强）：当前 auto-discover 暴露 `cls.__dict__` 中所有公开方法，无法控制哪些公开方法是工具、哪些只是 Python API

> 父类方法继承：`_get_public_methods` 只看 `cls.__dict__`（不含继承方法），子类不会重复暴露父类方法。此行为正确，无需修改。

## 设计方案

### Fix 1: async late-bound wrapper

`_make_late_bound()` 当前实现：

```python
def _make_late_bound(instance, method_name):
    def wrapper(**kwargs):
        return getattr(instance, method_name)(**kwargs)
    # ... copy metadata
    return wrapper
```

wrapper 始终是 sync 函数。当原方法是 async 时，`getattr(instance, method_name)(**kwargs)` 返回 coroutine，但 wrapper 不 await 它。dispatch 中 `inspect.iscoroutinefunction(fn)` 对 wrapper 返回 False，走 `asyncio.to_thread` 路径，coroutine 在线程中被创建但永远不被 await。

**修复**：检测原方法是否 async，生成对应类型的 wrapper：

```python
def _make_late_bound(instance: Any, method_name: str):
    actual = getattr(instance, method_name)
    if inspect.iscoroutinefunction(actual):
        async def wrapper(**kwargs):
            return await getattr(instance, method_name)(**kwargs)
    else:
        def wrapper(**kwargs):
            return getattr(instance, method_name)(**kwargs)
    wrapper.__name__ = method_name
    wrapper.__doc__ = actual.__doc__
    wrapper.__annotations__ = getattr(actual, '__annotations__', {})
    return wrapper
```

**注意**：late-bound 的核心意图是"调用时重新解析方法"（支持 define_module 热更新）。async 版本同样在调用时通过 `getattr` 解析，保留 late-bound 语义。检测时机是注册时（`actual` 是注册时的方法），如果热更新后 async 性质变了（极端情况），需要重新注册——但 `_refresh_discovered` 在 version 变更时已经会重建 entries，所以没有问题。

### Fix 2: `_discoverable` 类级控制

新增类属性 `_discoverable`，控制 Toolkit 是否被 auto-discover 发现：

```python
class Toolkit(mutagent.Declaration):
    _discoverable: ClassVar[bool] = True
```

- `_discoverable = True`（默认）：正常 auto-discover
- `_discoverable = False`：auto-discover 跳过此类，但仍可通过 `.add()` 手动注册

`_refresh_discovered` 中添加检查：

```python
for cls in current_toolkit_classes:
    if not getattr(cls, '_discoverable', True):
        continue
    # ... 现有逻辑
```

子类不设置 `_discoverable` 则继承默认值 `True`，正常被发现。

### 增强 3：`_tool_methods` 方法级白名单

当前 `_get_public_methods` 暴露 `cls.__dict__` 中所有不以 `_` 开头的 callable。有时 Toolkit 需要公开的 Python API 方法，但不希望它们成为 LLM 工具。

**方案**：Toolkit 可选声明 `_tool_methods` 白名单：

```python
class MyToolkit(Toolkit):
    _tool_methods = ["search", "fetch"]  # 只暴露这两个

    def search(self, query): ...    # ✅ 暴露为工具
    def fetch(self, url): ...       # ✅ 暴露为工具
    def parse(self, html): ...      # ❌ 公开 API 但不暴露
```

`_get_public_methods` 逻辑变为：

```python
def _get_public_methods(cls: type) -> list[str]:
    tool_methods = cls.__dict__.get('_tool_methods')
    if tool_methods is not None:
        return [m for m in tool_methods if m in cls.__dict__]
    # 回退：现有行为
    return [
        name for name, val in cls.__dict__.items()
        if not name.startswith("_") and callable(val)
    ]
```

- 有 `_tool_methods` → 只暴露白名单中的方法
- 无 `_tool_methods` → 保持现有行为（向后兼容）

## 实施步骤清单

### 阶段一：mutagent 框架改动 [✅ 已完成]

- [x] **Task 1**: `_make_late_bound()` 支持 async
  - [x] 1.1 修改 `_make_late_bound()`：检测原方法是否 async，生成对应 wrapper
  - 状态：✅ 已完成

- [x] **Task 2**: Toolkit 类新增 `_discoverable` 和 `_tool_methods`
  - [x] 2.1 `tools.py` Toolkit 类新增 `_discoverable: ClassVar[bool] = True`
  - [x] 2.2 `tools.py` Toolkit 类新增 `_tool_methods: ClassVar[list[str] | None] = None`
  - [x] 2.3 更新 Toolkit docstring 说明这两个属性
  - 状态：✅ 已完成

- [x] **Task 3**: `_refresh_discovered()` 增加 `_discoverable` 检查
  - [x] 3.1 在 `_refresh_discovered()` 遍历 `current_toolkit_classes` 时跳过 `_discoverable=False` 的类
  - 状态：✅ 已完成

- [x] **Task 4**: `_get_public_methods()` 支持 `_tool_methods` 白名单
  - [x] 4.1 修改 `_get_public_methods()`：有 `_tool_methods` 时用白名单，否则保持现有行为
  - [x] 4.2 修改 `add()` 中 `methods is None` 时也使用 `_get_public_methods()`（统一两条路径）
  - 状态：✅ 已完成

- [x] **Task 5**: 测试
  - [x] 5.1 测试 async auto-discover：async Toolkit 方法经 auto-discover 注册后能正确 await
  - [x] 5.2 测试 `_discoverable=False`：标记为不可发现的 Toolkit 不被 auto-discover
  - [x] 5.3 测试 `_discoverable=False` 子类继承行为 + opt-in
  - [x] 5.4 测试 `_discoverable=False` 仍可通过 `.add()` 手动注册
  - [x] 5.5 测试 `_tool_methods` 白名单（add + auto-discover）
  - [x] 5.6 测试 `_tool_methods` 未设置时的向后兼容
  - [x] 5.7 运行全量测试：694 passed, 5 skipped
  - 状态：✅ 已完成

## 关键参考

### 源码

- `mutagent/src/mutagent/builtins/tool_set_impl.py:59-73` — `_make_late_bound()`，当前 sync-only 实现
- `mutagent/src/mutagent/builtins/tool_set_impl.py:106-111` — `_get_public_methods()`，已正确只看 `cls.__dict__`
- `mutagent/src/mutagent/builtins/tool_set_impl.py:142-230` — `_refresh_discovered()`，auto-discover 主逻辑
- `mutagent/src/mutagent/builtins/tool_set_impl.py:345-379` — `dispatch()`，async 检测和执行
- `mutagent/src/mutagent/tools.py:31-50` — Toolkit / ToolSet 基类声明
- `mutagent/tests/test_tool_set.py` — 工具集测试

### 相关规范

- `mutbot/docs/specifications/feature-ui-show-tool.md` — UI-show 通用工具设计（触发此修复的场景）
- `mutbot/docs/specifications/feature-interactive-ui-tools.md` — 后端驱动 UI 框架总规范
