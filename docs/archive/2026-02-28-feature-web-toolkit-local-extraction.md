# WebToolkit 可插拔实现与本地内容提取 设计规范

**状态**：✅ 已完成
**日期**：2026-02-28
**类型**：功能设计

## 1. 背景

当前 WebToolkit（T1）存在三个问题：

1. **API key 依赖**：`fetch` 和 `search` 均依赖 Jina AI 远程 API。未配置 API key 时虽能使用免费额度，但存在速率限制和服务不可控风险。
2. **强制 Markdown**：`fetch` 通过 Jina Reader API 返回 Markdown，无法获取原始网页内容或 clean HTML。
3. **无本地提取能力**：内容提取完全依赖远程服务，无法离线使用。
4. **实现硬编码**：search 和 fetch 直接绑定 Jina 实现，无法替换或扩展。

### 目标

- 抽象 search 和 fetch 为可插拔实现（Declaration 子类），支持多 provider 共存
- `fetch` 支持三种输出格式：`markdown`、`html`、`raw`
- WebToolkit 内置 `raw` 获取能力（仅 httpx），不依赖任何 FetchImpl
- 提供基于 readability-lxml + markdownify 的本地 FetchImpl
- 工具描述根据已发现的实现动态生成（无提取依赖时只暴露 `raw`）
- LLM 可在工具调用时选择使用哪个实现

## 2. 设计方案

### 2.1 技术选型：readability-lxml + markdownify

本地内容提取选择 readability-lxml + markdownify 管道方案：

| 维度 | readability + markdownify | trafilatura |
|------|--------------------------|-------------|
| Markdown 质量 | 高（有序列表、blockquote、代码缩进） | 一般（列表全转无序、blockquote 丢失） |
| 中间 HTML | 可访问，天然支持 html 格式输出 | 不可访问，markdown 从内部 XML 生成 |
| 传递依赖 | 7 个，~159 KB | 16 个，~845 KB |
| 性能 | ~3.2 ms/页 | ~3.2 ms/页 |
| 可定制 | 子类化 MarkdownConverter | 仅配置 flag |

管道架构天然满足三格式输出需求：httpx 获取 raw HTML → readability 提取 clean HTML → markdownify 转 Markdown。

### 2.2 Provider 抽象：SearchImpl / FetchImpl

引入两个 Declaration 基类，作为 search 和 fetch 的可插拔实现点：

```python
# mutagent/toolkits/web_toolkit.py

class SearchImpl(mutagent.Declaration):
    """搜索实现基类。

    子类通过 mutobj 子类发现机制自动注册。
    每个子类代表一种搜索后端（如 Jina、SearXNG）。

    Attributes:
        name: 实现标识符，用于 impl 参数选择（如 "jina"）。
        description: 简短描述，用于动态生成工具说明。
    """

    name: str
    description: str

    async def search(self, query: str, max_results: int = 5) -> str:
        """执行搜索并返回格式化结果。"""
        ...


class FetchImpl(mutagent.Declaration):
    """网页内容提取实现基类。

    负责将原始 HTML 转换为 clean HTML 或 Markdown。
    raw 格式由 WebToolkit 内置处理，不经过 FetchImpl。

    Attributes:
        name: 实现标识符（如 "local"）。
        description: 简短描述。
    """

    name: str
    description: str

    async def fetch(self, url: str, format: str = "markdown") -> str:
        """获取并提取网页内容。

        Args:
            url: 网页 URL。
            format: 输出格式 — "markdown" 或 "html"。
        """
        ...
```

**关键设计**：FetchImpl 只负责内容提取（`markdown` / `html` 格式）。`raw` 格式由 WebToolkit 内置处理（直接 httpx GET），不需要任何 FetchImpl。

### 2.3 内置实现

#### JinaSearchImpl

```python
# mutagent/builtins/web_jina.py

class JinaSearchImpl(SearchImpl):
    """Jina AI 搜索。"""
    name = "jina"
    description = "Jina Search API"
    config: Config

    async def search(self, query, max_results=5) -> str:
        # 使用 https://s.jina.ai/ API
        # 401/429 时返回友好引导
        ...
```

#### JinaFetchImpl

```python
# mutagent/builtins/web_jina.py（与 JinaSearchImpl 同文件）

class JinaFetchImpl(FetchImpl):
    """Jina Reader API 获取（需配置 API key）。"""
    name = "jina"
    description = "Jina Reader API"
    config: Config

    async def fetch(self, url, format="markdown") -> str:
        # 使用 https://r.jina.ai/ API，返回 markdown
        # format="html" 不支持，返回提示
        ...
```

JinaFetchImpl 与 JinaSearchImpl 在同一模块 `web_jina.py` 中，import 即注册。用户配置了 Jina API key 时，可通过 `impl="jina"` 选择使用。

#### LocalFetchImpl

```python
# mutagent/builtins/web_local.py

class LocalFetchImpl(FetchImpl):
    """本地内容提取（readability + markdownify）。"""
    name = "local"
    description = "本地提取"
    config: Config

    async def fetch(self, url, format="markdown") -> str:
        raw_html = await _httpx_get(url)
        title, clean_html = _extract(raw_html, url)  # readability
        if format == "html":
            return _format_html(title, clean_html, url)
        return _format_markdown(title, clean_html, url)  # markdownify
```

### 2.4 WebToolkit 声明

```python
class WebToolkit(Toolkit):
    """Web 信息检索工具集。"""
    config: Config

    def search(self, query: str, max_results: int = 5, impl: str = "") -> str:
        """搜索 Web 并返回结果摘要。

        Args:
            query: 搜索关键词。
            max_results: 最大返回结果数。
            impl: 使用的搜索实现。
        """
        ...

    def fetch(self, url: str, format: str = "markdown", impl: str = "") -> str:
        """读取网页内容并返回文本。

        Args:
            url: 要读取的网页 URL。
            format: 输出格式 — "markdown"（默认）、"html"（提取后的正文）、"raw"（原始网页）。
            impl: 使用的获取实现。
        """
        ...
```

### 2.5 WebToolkit 实现：raw 内置 + provider 分发

```python
# mutagent/builtins/web_toolkit_impl.py

_TIMEOUT = 30
_MAX_CONTENT_CHARS = 50000

async def _httpx_get_raw(url: str) -> str:
    """内置 raw 获取，仅依赖 httpx（mutagent 核心依赖）。"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
    content = resp.text
    if len(content) > _MAX_CONTENT_CHARS:
        content = content[:_MAX_CONTENT_CHARS] + "\n\n[内容已截断]"
    parts = [f"# {url}\n", content]
    return "\n".join(parts)

@mutagent.impl(WebToolkit.search)
async def search(self, query, max_results=5, impl=""):
    search_impls = _discover_impls(SearchImpl)
    if not search_impls:
        return "没有可用的搜索实现。请确认已注册 SearchImpl。"
    name = impl or "jina"  # 默认 jina
    impl_cls = search_impls.get(name)
    if impl_cls is None:
        available = ", ".join(search_impls.keys())
        return f"未知搜索实现 \"{name}\"。可用：{available}"
    instance = impl_cls(config=self.config)
    return await instance.search(query, max_results)

@mutagent.impl(WebToolkit.fetch)
async def fetch(self, url, format="markdown", impl=""):
    # raw 格式：WebToolkit 内置，不需要 FetchImpl
    if format == "raw":
        return await _httpx_get_raw(url)

    # html/markdown 格式：需要 FetchImpl
    fetch_impls = _discover_impls(FetchImpl)
    if not fetch_impls:
        return (
            f"格式 \"{format}\" 需要内容提取依赖。\n"
            "当前仅支持 format=\"raw\"（原始网页 HTML）。\n\n"
            "安装本地提取：pip install mutagent[web-extract]"
        )
    name = impl or "local"  # 默认 local
    impl_cls = fetch_impls.get(name)
    if impl_cls is None:
        available = ", ".join(fetch_impls.keys())
        return f"未知获取实现 \"{name}\"。可用：{available}"
    instance = impl_cls(config=self.config)
    return await instance.fetch(url, format)
```

### 2.6 动态工具描述

通过 Toolkit 的 `_customize_schema` 钩子，根据已发现的实现动态调整 schema。

#### Toolkit 基类扩展

```python
# tools.py - Toolkit 基类
class Toolkit(mutagent.Declaration):
    def _customize_schema(self, method_name: str, schema: ToolSchema) -> ToolSchema:
        """动态调整工具 schema。子类可覆盖。"""
        return schema
```

#### ToolSet 集成

在 `tool_set_impl.py` 的 `add()` 和 `_make_entries_for_toolkit()` 中调用钩子：

```python
schema = make_schema(decl_method, tool_name)
if hasattr(instance, '_customize_schema'):
    schema = instance._customize_schema(method_name, schema)
```

#### WebToolkit 动态 schema

根据已发现的 FetchImpl 动态调整 `Web-fetch` 的 schema：

```python
@mutagent.impl(WebToolkit._customize_schema)
def _customize_schema(self, method_name, schema):
    import mutobj

    if method_name == "search":
        impls = mutobj.discover_subclasses(SearchImpl)
        if impls:
            impl_list = "、".join(f"{c.name}（{c.description}）" for c in impls)
            desc = f"搜索 Web 并返回结果摘要。可用实现：{impl_list}。"
        else:
            desc = "搜索 Web 并返回结果摘要。（无可用搜索实现）"
        return ToolSchema(
            name=schema.name, description=desc,
            input_schema=schema.input_schema,
        )

    if method_name == "fetch":
        fetch_impls = mutobj.discover_subclasses(FetchImpl)
        props = dict(schema.input_schema.get("properties", {}))
        if fetch_impls:
            # 完整能力：markdown/html/raw + impl 选择
            impl_list = "、".join(f"{c.name}（{c.description}）" for c in fetch_impls)
            desc = f"读取网页内容并返回文本。可用提取实现：{impl_list}。"
            props["format"] = {
                "type": "string",
                "description": '输出格式 — "markdown"（默认）、"html"（提取后的正文）、"raw"（原始网页）。',
                "default": "markdown",
            }
            props["impl"] = {
                "type": "string",
                "description": f"提取实现（默认 \"{fetch_impls[0].name}\"）。",
                "default": "",
            }
        else:
            # 仅 raw：移除 format 和 impl 参数
            desc = "读取原始网页内容（HTML）。"
            props.pop("format", None)
            props.pop("impl", None)
        new_input = dict(schema.input_schema)
        new_input["properties"] = props
        # required 只保留 url
        new_input["required"] = ["url"]
        return ToolSchema(
            name=schema.name, description=desc,
            input_schema=new_input,
        )

    return schema
```

**LLM 看到的效果**：

未安装提取依赖时：
```
Web-fetch: 读取原始网页内容（HTML）。
  - url (string, required): 要读取的网页 URL。
```

安装提取依赖后：
```
Web-fetch: 读取网页内容并返回文本。可用提取实现：local（本地提取）。
  - url (string, required): 要读取的网页 URL。
  - format (string, default "markdown"): 输出格式 — "markdown"、"html"、"raw"。
  - impl (string, default ""): 提取实现（默认 "local"）。
```

### 2.7 文件结构

```
mutagent/
  toolkits/
    web_toolkit.py              # Declaration: WebToolkit, SearchImpl, FetchImpl
  builtins/
    web_toolkit_impl.py         # WebToolkit 实现：raw 内置 + provider 分发 + 动态描述
    web_jina.py                 # JinaSearchImpl（从 web_impl_jina.py 重命名重构）
    web_local.py                # LocalFetchImpl（新增，依赖 readability + markdownify）
```

原 `web_impl_jina.py` 拆分为：
- `web_jina.py` — JinaSearchImpl + JinaFetchImpl
- `web_toolkit_impl.py` — WebToolkit 分发逻辑 + raw 内置获取

### 2.8 Search 无 API key 处理

`JinaSearchImpl` 在 401/429 响应时返回友好提示：

```python
if resp.status_code in (401, 429):
    return (
        "搜索请求被拒绝（可能超出免费额度）。\n\n"
        "配置 Jina API key 以获取更高配额：\n"
        "1. 访问 https://jina.ai/api-key 获取免费 API key\n"
        "2. 在配置文件中添加：\n"
        '   {"WebToolkit": {"jina_api_key": "jina_xxxxx"}}'
    )
```

### 2.9 依赖管理

readability-lxml + markdownify 作为 mutagent **optional dependency**：

```toml
# mutagent/pyproject.toml
[project.optional-dependencies]
web-extract = [
    "readability-lxml>=0.8",
    "markdownify>=1.0",
]
```

`web_local.py` 在模块顶层检查依赖，import 失败则模块不加载、LocalFetchImpl 不注册：

```python
from readability import Document as ReadabilityDoc
from markdownify import markdownify as md
```

符合 mutobj "不 import 就不存在"原则。未安装依赖时 `discover_subclasses(FetchImpl)` 返回空列表，`_customize_schema` 自动将工具降级为仅 `raw` 格式。

mutbot 直接依赖本地提取：

```toml
# mutbot/pyproject.toml
dependencies = [
    "mutagent[web-extract]>=0.1.0",
    ...
]
```

### 2.10 注册与加载

```python
# web_toolkit.py 尾部 — 核心模块始终加载
from mutagent.builtins import web_toolkit_impl, web_jina
import mutagent
mutagent.register_module_impls(web_toolkit_impl)
mutagent.register_module_impls(web_jina)

# web_local 只在安装了依赖时由上层 import
# mutbot 的 guide.py 中：
#   import mutagent.builtins.web_local  # 注册 LocalFetchImpl
```

## 3. 待定问题

（已全部解决，无待定问题）

## 4. 实施步骤清单

### 阶段一：基础架构 [✅ 已完成]

- [x] **Task 1.1**: 扩展 Toolkit 基类 — `_customize_schema` 钩子
  - [x] Toolkit 基类添加 `_customize_schema(method_name, schema) -> ToolSchema` 默认方法
  - [x] `tool_set_impl.py` 的 `add()` 中 schema 生成后调用钩子
  - [x] `tool_set_impl.py` 的 `_make_entries_for_toolkit()` 中同样调用钩子
  - 状态：✅ 已完成

- [x] **Task 1.2**: 定义 SearchImpl / FetchImpl Declaration
  - [x] 在 `web_toolkit.py` 中定义 SearchImpl 和 FetchImpl 基类
  - [x] 声明 `name`、`description` 类变量和抽象方法
  - 状态：✅ 已完成

- [x] **Task 1.3**: 重构 WebToolkit 声明
  - [x] `search` 增加 `impl` 参数
  - [x] `fetch` 增加 `format` 和 `impl` 参数
  - [x] 移除对 `web_impl_jina` 的直接引用
  - 状态：✅ 已完成

### 阶段二：实现模块 [✅ 已完成]

- [x] **Task 2.1**: 新建 `web_toolkit_impl.py`
  - [x] `_httpx_get_raw(url)` 内置 raw 获取
  - [x] `_discover_impls(base_cls)` provider 发现
  - [x] `search` 实现：默认 jina + provider 分发
  - [x] `fetch` 实现：raw 内置 + html/markdown 分发到 FetchImpl（默认 local）
  - [x] `_customize_schema` 动态描述注入（无 FetchImpl 时只暴露 raw）
  - 状态：✅ 已完成

- [x] **Task 2.2**: 重构 `web_impl_jina.py` → `web_jina.py`
  - [x] JinaSearchImpl（从现有 search 实现迁移）
  - [x] JinaFetchImpl（从现有 fetch 实现迁移，使用 Jina Reader API）
  - [x] 401/429 友好提示
  - 状态：✅ 已完成

- [x] **Task 2.3**: 新建 `web_local.py`
  - [x] LocalFetchImpl 类定义
  - [x] `_httpx_get(url)` HTTP 获取
  - [x] `_extract(html, url)` readability 提取 → (title, clean_html)
  - [x] `_to_markdown(html)` markdownify 转换
  - [x] format=html / markdown 两条路径
  - [x] 内容截断（50K 字符限制）
  - 状态：✅ 已完成

### 阶段三：集成与依赖 [✅ 已完成]

- [x] **Task 3.1**: 更新模块注册
  - [x] `web_toolkit.py` 尾部注册 `web_toolkit_impl` 和 `web_jina`
  - [x] mutbot `guide.py` 中 import `web_local` 注册 LocalFetchImpl
  - 状态：✅ 已完成

- [x] **Task 3.2**: 更新 pyproject.toml
  - [x] mutagent 添加 `web-extract` optional dependency
  - [x] mutbot 依赖 `mutagent[web-extract]`
  - 状态：✅ 已完成

- [x] **Task 3.3**: 清理旧文件
  - [x] 删除 `web_impl_jina.py`（已拆分为 `web_jina.py` + `web_toolkit_impl.py`）
  - 状态：✅ 已完成

### 阶段四：测试 [✅ 已完成]

- [x] **Task 4.1**: SearchImpl / FetchImpl 发现测试
  - [x] `discover_subclasses` 正确发现已注册的实现
  - 状态：✅ 已完成

- [x] **Task 4.2**: WebToolkit 分发测试
  - [x] search 默认使用 jina
  - [x] fetch 默认使用 local
  - [x] 未知 impl 名称时的错误信息
  - 状态：✅ 已完成

- [x] **Task 4.3**: fetch raw 内置测试
  - [x] format=raw 直接返回原始 HTML（不需要 FetchImpl）
  - [x] 超时和 HTTP 错误处理
  - [x] 长内容截断
  - 状态：✅ 已完成

- [x] **Task 4.4**: `_customize_schema` 测试
  - [x] 有 FetchImpl 时 schema 包含 format 和 impl 参数
  - [x] search 描述包含已发现的实现列表
  - 状态：✅ 已完成

- [x] **Task 4.5**: JinaSearchImpl / JinaFetchImpl 测试
  - [x] 正常搜索 + 401/429 友好提示 + 超时处理
  - [x] JinaFetchImpl markdown 格式 + html 不支持提示
  - 状态：✅ 已完成

- [x] **Task 4.6**: LocalFetchImpl 测试
  - [x] format=markdown（readability + markdownify）
  - [x] format=html（readability only）
  - [x] 超时和 HTTP 错误处理
  - 状态：✅ 已完成

- [x] **Task 4.7**: 重写 `test_web_toolkit.py`
  - [x] 适配新的 provider 架构（46 个测试全部通过）
  - 状态：✅ 已完成

## 5. 测试验证

### 单元测试
- [x] Toolkit `_customize_schema` 钩子
- [x] SearchImpl / FetchImpl 发现与分发
- [x] JinaSearchImpl + JinaFetchImpl（mock httpx）
- [x] LocalFetchImpl（mock httpx + 真实 readability/markdownify）
- [x] WebToolkit raw 内置获取
- 执行结果：46/46 通过

### 回归测试
- [x] mutagent 完整测试套件
- 执行结果：738 passed, 4 skipped, 0 failed
