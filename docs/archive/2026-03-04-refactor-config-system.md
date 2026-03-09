# Config 接口重构 设计规范

**状态**：✅ 已完成
**日期**：2026-03-04
**类型**：重构

## 背景

当前 `Config` 承担了过多职责：多层合并、文件加载、model 解析、环境变量展开等。需要将 Config 简化为纯接口 Declaration，让宿主系统（mutbot 或其他）提供具体实现。

## 设计方案

### Config Declaration

Config 是可观察的配置容器。所有方法提供默认实现，Config 本身即可用的空配置。宿主系统通过子类覆盖方法提供具体行为。

```python
class Config(mutagent.Declaration):
    """可观察的配置容器。

    默认实现：
    - get() → 返回 default
    - set() → 空操作
    - on_change() → 返回空 Disposable
    - affects() → glob 双向匹配
    """

    def get(self, name: str, *, default: Any = None) -> Any:
        """读取配置值。name 为点分路径。

        示例：
            config.get("providers.anthropic.auth_token")
            config.get("providers")  # 返回整个 providers dict
            config.get("agents.sub_agent.model", default="claude-sonnet")
        """
        return default  # 默认实现：返回 default

    def set(self, name: str, value: Any, *, source: str = "") -> None:
        """设置配置值并触发变更通知。

        name: 点分路径（如 "providers.anthropic"）
        value: 新值（任意类型）
        source: 变更来源标识（如 "user", "workspace", "runtime"）

        设置一个节点会隐式影响所有子路径。例如：
        set("providers.anthropic", new_dict) 会触发所有监听
        providers.anthropic 及其子路径的回调。
        """
        # 默认实现：空操作

    def on_change(self, pattern: str, callback: ChangeCallback) -> Disposable:
        """监听配置变更。

        pattern 支持 glob 风格通配符：
        - 精确路径："providers.anthropic.auth_token"
        - 单级通配 *："providers.*" — 匹配 providers 的任意直接子项
        - 递归通配 **："providers.**" — 匹配 providers 下任意深度
        - 混合："providers.*.models" — 任意 provider 的 models

        触发规则（pattern 与 set 的 key 双向匹配）：
        1. key 匹配 pattern → 触发（监听范围内的 key 被设置）
           on_change("providers.*", cb) + set("providers.anthropic") → ✓
        2. key 是 pattern 的祖先 → 触发（父节点被替换，子路径隐式变更）
           on_change("providers.anthropic.auth_token", cb) + set("providers.anthropic") → ✓
           on_change("providers.**", cb) + set("providers") → ✓
        3. 不相关 → 不触发
           on_change("providers.*", cb) + set("agents.xxx") → ✗
           on_change("providers.*", cb) + set("providers.anthropic.auth_token") → ✗
           （* 只匹配一级，auth_token 是两级深）

        示例：
            # 监听任意 provider 配置变化（直接子项）
            config.on_change("providers.*", on_provider_changed)
            # 监听任意 provider 的 models 列表变化
            config.on_change("providers.*.models", on_models_changed)
            # 监听 providers 下所有变化
            config.on_change("providers.**", on_any_provider_changed)
        """
        return Disposable()  # 默认实现：返回空 Disposable

    def affects(self, pattern: str, key: str) -> bool:
        """判断 key 的变更是否影响 pattern 指定的路径。

        双向匹配：
        1. key 匹配 pattern → True（标准 glob）
        2. key 是 pattern 的祖先 → True（父节点被替换，子路径隐式变更）
        3. 不相关 → False

        子类可覆盖以定制匹配策略。
        """
        ...  # 默认实现：glob 双向匹配
```

### 变更通知类型

```python
class ConfigChangeEvent:
    """变更事件。"""
    key: str           # 被设置的完整路径（如 "providers.anthropic"）
    source: str        # 变更来源标识（如 "user", "workspace"）
    config: Config     # 触发变更的 Config 实例

ChangeCallback = Callable[[ConfigChangeEvent], None]

class Disposable:
    """取消器。"""
    def dispose(self) -> None: ...
```

### DictConfig — mutagent CLI 的示例实现

以下 DictConfig 是 `main_impl.py` 中的示例实现，仅供 mutagent CLI 使用。宿主系统（如 mutbot）应提供自己的 Config 子类。

```python
class DictConfig(Config):
    """单 dict 配置。mutagent CLI 用。"""
    _data: dict
    _listeners: list  # list[tuple[str, ChangeCallback]]

    def get(self, name: str, *, default: Any = None) -> Any:
        """点分路径导航 _data。"""
        node = self._data
        for key in name.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, name: str, value: Any, *, source: str = "") -> None:
        """按点分路径写入 _data，触发匹配的 on_change 回调。"""
        node = self._data
        keys = name.split(".")
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value
        # 通知匹配的监听者
        event = ConfigChangeEvent(key=name, source=source, config=self)
        for pattern, cb in self._listeners:
            if self.affects(pattern, name):
                cb(event)

    def on_change(self, pattern: str, callback: ChangeCallback) -> Disposable:
        """注册监听。返回 Disposable 用于取消。"""
        entry = (pattern, callback)
        self._listeners.append(entry)
        def dispose():
            self._listeners.remove(entry)
        return Disposable(dispose=dispose)
```

加载逻辑（`main_impl.load_config`）：

```python
@mutagent.impl(App.load_config)
def load_config(self, config_path: str = ".mutagent/config.json") -> None:
    """从单个配置文件加载。

    config_path 来源：
    - 默认：".mutagent/config.json"（当前目录）
    - --config 启动参数指定
    """
    p = Path(config_path).expanduser()
    data = json.loads(p.read_text()) if p.exists() else {}
    self.config = DictConfig(_data=data)
```

mutagent CLI 加载后 `_data` 不再变化（不调用 `set()`，不注册 `on_change`）。

### LLMProvider 变更

重命名 `from_config` → `from_spec`，避免与 `Config` 对象混淆。`get_model` / `get_all_models` 从 Config 移至 LLMProvider 并重命名：

```python
class LLMProvider(mutagent.Declaration):

    @classmethod
    def from_spec(cls, spec: dict) -> LLMProvider:
        """从模型规格创建 provider 实例。子类覆盖此方法。
        spec 包含 provider、auth_token、base_url、model_id 等字段。"""
        ...

    @classmethod
    def resolve_model(cls, config: Config, name: str | None = None) -> dict | None:
        """从 Config 中查找并组装指定模型的 spec。
        name 为 None 时使用默认模型。找不到时返回 None。"""
        ...

    @classmethod
    def list_models(cls, config: Config) -> list[dict]:
        """列出 Config 中所有已配置的模型 spec。"""
        ...

    async def send(self, ...) -> AsyncIterator[StreamEvent]:
        ...
```

- `from_config` → `from_spec`：参数从 `model_config: dict` 改为 `spec: dict`，子类同步改名
- `get_model` → `resolve_model`：语义更准确（查找 + 组装）
- `get_all_models` → `list_models`：更简洁
- **修复 SystemExit**：现有 `Config.get_model()` 找不到模型时 `raise SystemExit(...)`。`resolve_model` 改为返回 `None`，由调用方决定处理

### 移除的方法

**Config**：
- `section(key)` — 生产代码无调用
- `get_model()` / `get_all_models()` — 移至 LLMProvider（`resolve_model` + `list_models`）
- `load()` classmethod — 移除。加载逻辑由 `main_impl.load_config` 或宿主系统负责

### 对宿主系统的接口契约

宿主系统（mutbot 或其他）使用 Config 的方式：

1. 创建 Config 子类（或 `@impl` 覆盖）提供 get/set/on_change 的完整实现
2. 构造实例，传递给 mutagent 的 Agent/Toolkit 等组件
3. 配置变更时调用 `config.set(name, value, source="xxx")` 通知所有消费者
4. 消费者通过 `config.get(name)` 读取、`config.on_change(pattern, cb)` 订阅

mutagent 层不关心配置从哪来、怎么持久化、有几层——这些是宿主系统的实现细节。

## 关键参考

### 源码
- `mutagent/src/mutagent/config.py` — 现有 Config Declaration
- `mutagent/src/mutagent/provider.py` — LLMProvider Declaration（get_model/get_all_models 迁移目标）
- `mutagent/src/mutagent/builtins/config_impl.py` — 现有 @impl（层合并、env 展开、get_model）
- `mutagent/src/mutagent/builtins/main_impl.py` — 现有 load_config / setup_agent / run（Config 主要消费者）
- `mutagent/src/mutagent/main.py` — App Declaration + main() 入口

### 需要更新的调用方
- `main.py:76-79` — `app.load_config([...])` → 单路径 + `--config` 参数
- `main_impl.py:165` — `Config.load(config_path)` → DictConfig 构造
- `main_impl.py:168,182,187,205,220,242,244,262` — `self.config.get(path, default)` → `default` 改为 keyword-only
- `main_impl.py:196,281,339` — `self.config.get_model()` → `LLMProvider.resolve_model(self.config)`
- `main_impl.py:48` — `provider_cls.from_config(model_config)` → `provider_cls.from_spec(spec)`
- `anthropic_provider.py:39` / `openai_provider.py:38` — `from_config` → `from_spec`，参数 `config` → `spec`
- `web_jina.py:36` — `config.get("WebToolkit.jina_api_key")` → `default` 改为 keyword-only
- `test_openai_provider.py` — `from_config` 测试 → `from_spec`

### 相关规范
- `mutbot/docs/specifications/refactor-config-system.md` — mutbot 分层配置系统设计（本文档的下游）

### 外部参考
- VS Code `onDidChangeConfiguration` — on_change 通配符匹配设计参考

## 实施步骤

- [x] **Task 1**: Config Declaration 重写
  - [x] `config.py`: 重写 Config 为纯桩方法 Declaration（`get`、`set`、`on_change`）
  - [x] `config.py`: 定义 `ConfigChangeEvent`、`ChangeCallback`、`Disposable` 类型
  - [x] `config.py`: 移除 `_layers`、`load()`、`section()`、`get_model()`、`get_all_models()` 方法
  - [x] `config.py`: 保留底部 `config_impl` import/register（Task 4 统一清理）
  - 状态：✅ 已完成

- [x] **Task 2**: DictConfig + main_impl 改造
  - [x] `builtins/main_impl.py`: 新增 `DictConfig(Config)` 子类（`get` + `set` + `on_change` 实现）
  - [x] `builtins/main_impl.py`: 新增 `_pattern_matches(pattern, key)` 工具函数
  - [x] `builtins/main_impl.py`: 重写 `load_config` — 单路径加载，构造 DictConfig，保留 env/path/modules 逻辑
  - [x] `main.py:App.load_config`: 更新签名 `config_path: str = ".mutagent/config.json"`
  - [x] `main.py:main()`: 添加 `--config` 参数支持，`app.load_config(args.config)`
  - [x] `builtins/main_impl.py`: 更新所有 `self.config.get(path, default)` 调用，`default` 改为 keyword-only（加 `default=`）
  - 状态：✅ 已完成

- [x] **Task 3**: LLMProvider 方法迁移 + 重命名
  - [x] `provider.py`: `from_config` → `from_spec`，新增 `resolve_model` 和 `list_models` 类方法桩
  - [x] `builtins/anthropic_provider.py`、`builtins/openai_provider.py`: `from_config` → `from_spec`，参数 `config` → `spec`
  - [x] `builtins/main_impl.py`: 将 `config_impl.py` 中 `get_model` / `get_all_models` / `_resolve_default_model` / `_collect_model_names` 逻辑迁移为 `LLMProvider.resolve_model` / `list_models` 的 `@impl`
  - [x] `builtins/main_impl.py`: 更新 `_create_llm_client` 和调用方（`from_config` → `from_spec`，`get_model` → `LLMProvider.resolve_model`）
  - [x] 修复 SystemExit → 返回 None，调用方处理
  - 状态：✅ 已完成

- [x] **Task 4**: 清理 config_impl.py
  - [x] 移除整个 `builtins/config_impl.py` 文件
  - [x] `config.py`: 移除底部 `from mutagent.builtins import config_impl` 和 `register_module_impls`
  - 状态：✅ 已完成

- [x] **Task 5**: 清理测试
  - [x] 重写 `test_config.py`：DictConfig 测试 + LLMProvider.resolve_model/list_models 测试 + _pattern_matches 测试
  - [x] 更新 `test_e2e.py`、`test_web_toolkit.py` 中 `Config(_layers=[...])` → `DictConfig(_data={...})`
  - [x] 更新 `test_openai_provider.py` 中 `from_config()` → `from_spec()` 测试
  - [x] 运行全量测试验证：689 passed, 5 skipped
  - 状态：✅ 已完成

- [x] **Task 6**: Agent.config 字段
  - [x] `agent.py`: Agent Declaration 新增 `config: Config` 字段
  - [x] `builtins/main_impl.py`: Agent() 构造传入 config=self.config（主 agent + sub-agent）
  - [x] 运行全量测试验证：689 passed, 5 skipped
  - 状态：✅ 已完成

## 测试验证

全量测试通过：689 passed, 5 skipped（2.90s）
