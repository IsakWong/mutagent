# 配置系统迭代 设计规范

**状态**：✅ 已完成
**日期**：2026-02-15
**类型**：功能设计

## 1. 背景

当前配置系统存在以下局限：

- **单一配置源**：仅支持 `mutagent.json`，无法区分项目级和用户级配置
- **环境变量与模型配置耦合**：`env` 字段既用于环境变量，又传递模型参数
- **单模型限制**：只能配置一组 LLM 参数
- **无模块扩展/路径扩展**
- **入口分散**：REPL 在 `__main__.py`，Agent 创建在 `main.py`，均不可覆盖

目标：建立分级、可扩展的配置系统 + 可覆盖的入口，为项目级扩展（TUI、自定义工具等）奠定基础。

## 2. 设计方案

### 2.1 配置文件位置与优先级

| 优先级 | 路径 | 作用域 |
|--------|------|--------|
| 1（最高） | `./.mutagent/config.json` | 项目级（CWD） |
| 2 | `~/.mutagent/config.json` | 用户级 |
| 3（最低） | `<mutagent-package>/config.json` | 包内置默认值 |

三级均可选，缺失跳过。

### 2.2 配置文件格式

```json
{
  "env": { "HTTP_PROXY": "http://proxy:8080" },
  "models": {
    "claude": { "base_url": "https://api.anthropic.com", "auth_token": "sk-...", "model_id": "claude-sonnet-4-20250514" },
    "glm": { "base_url": "https://ark.cn-beijing.volces.com/api/coding", "auth_token": "...", "model_id": "glm-4.7" }
  },
  "default_model": "claude",
  "modules": ["extensions.tui"],
  "path": [".", "extensions"],
  "tui": { "theme": "dark", "layout": "split" }
}
```

**允许任意自定义字段**，框架不做校验。扩展通过 `config.get("tui")` 获取自定义配置段。

### 2.3 内置字段定义

| 字段 | 类型 | 说明 |
|------|------|------|
| `env` | `dict[str, str]` | 启动后设置的环境变量，与模型配置解耦 |
| `models` | `dict[str, dict]` | 多模型配置表。键为自定义名称，值为 `{base_url, auth_token, model_id, ...}` 的 dict，可扩展字段 |
| `default_model` | `str` | 默认模型名。未配置时：单模型自动选择 → 包默认值 |
| `modules` | `list[str]` | 启动时导入的模块名列表，builtins 之后加载，可覆盖内置实现 |
| `path` | `list[str]` | 额外 sys.path，相对路径基于**所属配置文件目录**解析 |

模型配置使用 **plain dict** 而非 dataclass，通过约定键名（`base_url`、`auth_token`、`model_id`）访问。这样不同 API 类型（OpenAI、Claude、本地模型等）可以自由扩展字段，LLMClient 实现各取所需。

### 2.4 Config 类（声明-实现分离）

`Config` 使用 `mutagent.Object`，保留每一级配置的原始数据，在访问时按合并策略组装结果。

```python
# src/mutagent/config.py
class Config(mutagent.Object):
    """可扩展配置对象。保留分层原始数据，访问时组装。"""

    _layers: list   # [(config_dir: Path, raw_data: dict), ...] 低→高优先级

    @classmethod
    def load(cls) -> Config:
        """扫描三级配置文件，构造 Config 对象。非 @impl，引导阶段直接调用。"""
        ...

    def get(self, path: str, default=None, *, merge: bool = True):
        """获取配置值。支持点号路径。merge 控制跨层合并行为。"""
        ...

    def get_model(self, name: str | None = None) -> dict:
        """获取模型配置 dict。name=None 时使用默认模型。"""
        ...
```

**Config.load()** 实现思路：
- 依次扫描包默认 → `~/.mutagent/` → `./.mutagent/` 三级 config.json
- 每级成功解析后作为 `(config_dir, raw_data)` 元组追加到 `_layers`
- 原始数据**不做预合并**，保持每级独立。path 字段中的相对路径在此阶段解析为绝对路径（因为需要 config_dir 上下文）
- `load()` 是普通 classmethod（非 @impl 桩方法），因为它在引导阶段 builtins 加载前调用

**Config.get(path, default, *, merge=True)** 实现思路（@impl，可覆盖）：
- 支持点号路径：`"models.glm.base_url"` → 先解析顶层键 `"models"`，再逐级深入
- **`merge=True`（默认）**：顶层合并策略根据值类型自动推断：
  - 所有层该键均为 **dict** → 字典合并（低优先级在先，高优先级覆盖同名键）
  - 所有层该键均为 **list** → 列表拼接，去重
  - 其他 / 类型不一致 → 最高优先级覆盖
- **`merge=False`**：直接返回最高优先级层中该键的值，不做跨层合并。适用于需要完整覆盖而非合并的场景（如项目想要完全替换 models 列表而非追加）
- 顶层合并/选择后，沿路径后续部分逐级 dict 取值
- `"path"` 键特殊处理：list 拼接后返回已解析的绝对路径

使用示例：
```python
config.get("models")                # 合并所有层的 models dict
config.get("models", merge=False)   # 仅取最高优先级层的 models（完整替换）
config.get("tui.theme", "dark")     # 点号路径，theme 是标量，merge 行为一致
```

**Config.get_model(name)** 实现思路（@impl，可覆盖）：
- `name=None` 时解析默认模型名：`default_model` 配置值 → 单模型自动选 → 报错
- 从 `get("models")` 的合并结果中取出指定模型的 dict
- 验证 `auth_token` 非空，否则 `SystemExit`
- 返回 plain dict（如 `{"base_url": "...", "auth_token": "...", "model_id": "..."}`）

### 2.5 子配置访问 API

除了 `config.get("tui.theme", "dark")` 的点号路径访问外，提供 `section()` 方法返回某个配置段的局部视图：

```python
class Config(mutagent.Object):
    ...
    def section(self, key: str) -> Config:
        """获取某个顶层键的子配置视图。

        返回一个新的 Config 对象，其 _layers 过滤为只包含该键下的子字典。
        适合将某个配置段传递给扩展模块，让它只关心自己的命名空间。
        """
        ...
```

使用示例：
```python
# 扩展模块拿到自己的配置段
tui_cfg = self.config.section("tui")
tui_cfg.get("theme", "dark")     # 等价于 self.config.get("tui.theme", "dark")
tui_cfg.get("layout", "split")

# section 返回的也是 Config，同样支持点号路径和分层合并
```

### 2.6 Main 类（声明-实现分离）

将 `__main__.py` 的 REPL 逻辑和 `main.py` 的 agent 创建统一到 `Main` 类：

```python
# src/mutagent/main.py
class Main(mutagent.Object):
    """主入口。可被扩展覆盖以实现自定义 UI。"""
    config: Config
    agent: Agent    # 由 setup_agent() 设置，run() 使用

    def setup_agent(self, system_prompt: str = "") -> Agent:
        """初始化本次会话的 Agent，存入 self.agent。可覆盖以自定义组件组装。"""
        ...

    async def run(self) -> None:
        """运行 Agent 会话主循环。可覆盖以实现 TUI 等自定义 UI。"""
        ...
```

**Main.agent 属性**：`run()` 与 `setup_agent()` 通过 `self.agent` 通信。默认 `run()` 实现先调用 `self.setup_agent()` 设置 `self.agent`，再进入 REPL 循环。这种分离使得：
- 覆盖 `setup_agent()` → 自定义 agent 创建（不同的工具集、不同的 LLMClient），`run()` 不受影响
- 覆盖 `run()` → 自定义 UI（TUI、Web），可复用默认的 `setup_agent()`
- 覆盖两者 → 完全自定义

`builtins/main.impl.py` 提供默认实现：
- `setup_agent()` — 从 config 获取模型配置，组装 ModuleManager → EssentialTools → ToolSelector → LLMClient → Agent，存入 `self.agent`
- `run()` — 调用 `self.setup_agent()`，然后执行当前 `__main__.py` 的 REPL 逻辑（SYSTEM_PROMPT、_input_stream、事件渲染循环）

**`__main__.py` 简化为**：
```python
from mutagent.main import main
if __name__ == "__main__":
    main()
```

### 2.7 import-based 模块加载

将 `load_builtins()` 从显式调用改为 Python import 触发：

**`mutagent/builtins/__init__.py`**：
```python
from mutagent.runtime.impl_loader import ImplLoader
ImplLoader.auto_load(__file__, __name__)
```

**`ImplLoader.auto_load()`** 新增 classmethod：
```python
@classmethod
def auto_load(cls, init_file: str, package_name: str) -> None:
    """在包的 __init__.py 中调用，自动发现并加载该包下所有 .impl.py。"""
    cls().load_all(Path(init_file).parent, package_name)
```

**引导函数中**：
```python
import mutagent.builtins  # 触发 __init__.py → 加载所有内置 .impl.py
```

**循环依赖分析**：无风险。
- `builtins/__init__.py` 导入 `runtime.impl_loader`，不依赖 builtins 自身
- 各 `.impl.py` 导入声明层（`mutagent.agent`、`mutagent.client` 等），声明层不依赖 builtins
- `mutagent/__init__.py` 不导入 builtins，避免意外早期加载

**扩展模块复用同一模式**：
```python
# my_extension/__init__.py
from mutagent.runtime.impl_loader import ImplLoader
ImplLoader.auto_load(__file__, __name__)
```

配置 `"modules": ["my_extension"]` 后，`import my_extension` 自动加载其下所有 `.impl.py`。

### 2.8 引导流程（`main()` 函数）

`main.py` 中的 `main()` 是不可覆盖的引导入口：

```
1. config = Config.load()              ← 纯 Python，扫描三级配置
2. 设置 env → os.environ               ← 直接读 config._layers，无需 @impl
3. 扩展 sys.path                       ← 直接读 config._layers 中已解析的 path
4. import mutagent.builtins            ← 触发内置 @impl 注册（Config/Main 方法可用）
5. import 扩展模块                      ← config modules 字段，可覆盖 @impl
6. Main(config=config).run()           ← 可覆盖的入口
```

步骤 2-3 直接操作 `config._layers` 原始 dict，不经过 `Config.get()`（此时 @impl 未注册）。步骤 4 后 `get()`/`get_model()` 才可用。步骤 6 时所有覆盖已就绪。

### 2.9 包内置默认配置

`src/mutagent/config.json`：

```json
{
  "models": {
    "default": {
      "base_url": "https://api.anthropic.com",
      "auth_token": "",
      "model_id": "claude-sonnet-4-20250514"
    }
  },
  "default_model": "default"
}
```

### 2.10 典型扩展示例

```
my_project/.mutagent/
├── config.json
└── extensions/
    └── tui.py
```

```python
# .mutagent/extensions/tui.py
import mutagent
from mutagent.main import Main

@mutagent.impl(Main.run, override=True)
async def run(self) -> None:
    agent = self.setup_agent(system_prompt=...)
    tui_cfg = self.config.section("tui")
    theme = tui_cfg.get("theme", "dark")
    # ... TUI 初始化和事件循环 ...
```

### 2.11 源码文件布局变更

```
src/mutagent/
├── config.py          [新增] Config 声明 + Config.load() + 合并辅助函数
├── config.json        [新增] 包内置默认配置
├── main.py            [改造] Main 声明 + main() 引导函数
├── __main__.py        [简化] 仅调用 main()
├── builtins/
│   ├── __init__.py    [改造] ImplLoader.auto_load 触发
│   ├── config.impl.py [新增] Config.get / get_model / section
│   ├── main.impl.py   [新增] Main.run / setup_agent + REPL 逻辑
│   ├── agent.impl.py  [不变]
│   └── ...            [不变]
└── runtime/
    └── impl_loader.py [改造] 新增 auto_load() classmethod
```

## 3. 待定问题

（无——所有问题已确认，决策已合并至第 4 节。）

## 4. 已确认决策

| 决策 | 结论 |
|------|------|
| 环境变量覆盖模型选择 | **不支持**，通过配置文件管理 |
| auth_token 环境变量引用 | **MVP 不实现**，token 直接写配置文件 |
| 旧 mutagent.json 兼容 | **不兼容**，直接废弃 |
| `__main__.py` 整合 | **合并到 main.py**，`__main__.py` 仅调用 `main()` |
| Config 可扩展性 | **包裹原始 layers**，`get()` 支持任意键 + 点号路径 |
| Config/Main 可覆盖 | **mutagent.Object 声明-实现分离** |
| Config.get merge 控制 | **`merge` 参数**：`True`（默认）按类型推断合并，`False` 取最高优先级完整值 |
| section() 实现 | **方案 B**：`get(key)` 合并后构造单层 Config，MVP 够用 |
| Main.agent 关系 | **Main 包含 agent 属性**，`setup_agent()` 设置，`run()` 使用，两者独立可覆盖 |
| setup_agent 命名 | **确认 `setup_agent()`**，"为本次会话准备 agent"语义 |
| load_config | **Config.load() classmethod**，保留分层原始数据 |
| load_builtins | **import mutagent.builtins 触发**，ImplLoader.auto_load 模式 |

## 5. 实施步骤清单

### 阶段一：配置基础设施 [✅ 已完成]

- [x] **Task 1.1**: 创建 `src/mutagent/config.py`
  - [x] `Config(mutagent.Object)` 声明：`_layers`、`get()`、`get_model()`、`section()` 桩方法
  - [x] `Config.load()` classmethod：三级扫描 + 层构建 + path 解析
  - [x] 辅助函数：`_load_json()`、`_resolve_paths_inplace()`
  - 状态：✅ 已完成

- [x] **Task 1.2**: 创建 `src/mutagent/builtins/config.impl.py`
  - [x] `Config.get()` 实现：层遍历、类型推断合并策略、点号路径支持、merge 参数
  - [x] `Config.get_model()` 实现：默认模型解析 + auth_token 校验
  - [x] `Config.section()` 实现
  - 状态：✅ 已完成

- [x] **Task 1.3**: 创建 `src/mutagent/config.json`（包默认配置）
  - 状态：✅ 已完成

### 阶段二：Main 类 + 引导重构 [✅ 已完成]

- [x] **Task 2.1**: 改造 `src/mutagent/main.py`
  - [x] `Main(mutagent.Object)` 声明：`config`、`agent`、`setup_agent()`、`run()` 桩方法
  - [x] `main()` 引导函数（6 步引导流程）
  - [x] 删除旧 `load_config()`、`create_agent()`、`load_builtins()`
  - 状态：✅ 已完成

- [x] **Task 2.2**: 创建 `src/mutagent/builtins/main.impl.py`
  - [x] `Main.setup_agent()` 实现（从旧 `create_agent` 迁移）
  - [x] `Main.run()` 实现（从 `__main__.py` 迁移 REPL 逻辑）
  - 状态：✅ 已完成

- [x] **Task 2.3**: 简化 `__main__.py` + 改造 `builtins/__init__.py`
  - [x] `__main__.py` → 仅 `main()` 调用
  - [x] `builtins/__init__.py` → `ImplLoader.auto_load(__file__, __name__)`
  - 状态：✅ 已完成

- [x] **Task 2.4**: `ImplLoader` 新增 `auto_load()` classmethod
  - 状态：✅ 已完成

### 阶段三：清理 + 文档 [✅ 已完成]

- [x] **Task 3.1**: 删除 `mutagent.json`，更新 CLAUDE.md
  - 状态：✅ 已完成

### 阶段四：测试 [✅ 已完成]

- [x] **Task 4.1**: `tests/test_config.py` — 31 个测试全部通过
  - [x] Config 声明层测试（继承、元类、声明方法）
  - [x] Config.get() 测试：简单键、默认值、点号路径、dict 合并、list 合并、标量覆盖、merge=False
  - [x] Config.get_model() 测试：指定名称、默认模型、单模型自动选、未找到、空 auth_token、多模型无默认
  - [x] Config.section() 测试：返回类型、子键访问、缺失键
  - [x] Config.load() 测试：基本加载、项目级配置
  - [x] 辅助函数测试：JSON 加载、路径解析
  - 状态：✅ 已完成

- [x] **Task 4.2**: Main / 引导测试（由 test_e2e.py 中 TestSetupAgent 覆盖）
  - [x] setup_agent() 正确创建 Agent（含默认参数和自定义参数）
  - 状态：✅ 已完成

- [x] **Task 4.3**: 适配已有测试
  - [x] `test_agent.py` — `load_builtins()` → `import mutagent.builtins`
  - [x] `test_selector.py` — `load_builtins()` → `import mutagent.builtins`
  - [x] `test_claude_impl.py` — `load_builtins()` → `import mutagent.builtins`
  - [x] `test_essential_tools.py` — `load_builtins()` → `import mutagent.builtins`
  - [x] `test_e2e.py` — `create_agent()` → `_create_test_agent()`（通过 Main/Config 创建）
  - 状态：✅ 已完成

---

### 实施进度总结
- ✅ **阶段一：配置基础设施** — 100% 完成 (3/3 任务)
- ✅ **阶段二：Main 类 + 引导重构** — 100% 完成 (4/4 任务)
- ✅ **阶段三：清理 + 文档** — 100% 完成 (1/1 任务)
- ✅ **阶段四：测试** — 100% 完成 (3/3 任务)

**核心功能完成度：100%** (11/11 任务)
**测试结果：197 通过 / 1 失败（pre-existing version 不一致） / 2 跳过**
