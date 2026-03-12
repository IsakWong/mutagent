"""mutagent.builtins.web_toolkit_impl -- WebToolkit 实现：raw 内置 + provider 分发 + 动态描述。"""

from __future__ import annotations

import logging

import httpx
import mutobj

import mutagent
from mutagent.net.client import HttpClient
from mutagent.messages import ToolSchema
from mutagent.toolkits.web_toolkit import FetchImpl, SearchImpl, WebToolkit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_TIMEOUT = 30
_MAX_CONTENT_CHARS = 50000


# ---------------------------------------------------------------------------
# Provider 发现
# ---------------------------------------------------------------------------

def _discover_impls(base_cls: type) -> dict[str, type]:
    """发现所有已注册的实现子类，返回 {name: cls} 映射。"""
    return {cls.name: cls for cls in mutobj.discover_subclasses(base_cls)}


# ---------------------------------------------------------------------------
# 内置 raw 获取
# ---------------------------------------------------------------------------

async def _httpx_get_raw(url: str) -> str:
    """内置 raw 获取，仅依赖 httpx。"""
    try:
        async with HttpClient.create() as client:
            resp = await client.get(url, timeout=_TIMEOUT, follow_redirects=True)
            resp.raise_for_status()
    except httpx.TimeoutException:
        return f"读取超时（{_TIMEOUT}s）。请稍后重试。"
    except httpx.HTTPError as exc:
        logger.warning("Web fetch failed for %s: %s", url, exc)
        return f"读取失败：{exc}"

    content = resp.text
    if len(content) > _MAX_CONTENT_CHARS:
        content = content[:_MAX_CONTENT_CHARS] + "\n\n[内容已截断]"
    return f"# {url}\n\n{content}"


# ---------------------------------------------------------------------------
# WebToolkit.search 实现
# ---------------------------------------------------------------------------

@mutagent.impl(WebToolkit.search)
async def search(self: WebToolkit, query: str, max_results: int = 5, impl: str = "") -> str:
    """搜索 Web 并返回结果摘要。"""
    search_impls = _discover_impls(SearchImpl)
    if not search_impls:
        return "没有可用的搜索实现。请确认已注册 SearchImpl。"
    name = impl or "jina"
    impl_cls = search_impls.get(name)
    if impl_cls is None:
        available = ", ".join(search_impls.keys())
        return f"未知搜索实现 \"{name}\"。可用：{available}"
    instance = impl_cls(config=self.config)
    return await instance.search(query, max_results)


# ---------------------------------------------------------------------------
# WebToolkit.fetch 实现
# ---------------------------------------------------------------------------

@mutagent.impl(WebToolkit.fetch)
async def fetch(self: WebToolkit, url: str, format: str = "markdown", impl: str = "") -> str:
    """读取网页内容并返回文本。"""
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
    name = impl or "local"
    impl_cls = fetch_impls.get(name)
    if impl_cls is None:
        available = ", ".join(fetch_impls.keys())
        return f"未知获取实现 \"{name}\"。可用：{available}"
    instance = impl_cls(config=self.config)
    return await instance.fetch(url, format)


# ---------------------------------------------------------------------------
# WebToolkit._customize_schema 实现
# ---------------------------------------------------------------------------

@mutagent.impl(WebToolkit._customize_schema)
def _customize_schema(self: WebToolkit, method_name: str, schema: ToolSchema) -> ToolSchema:
    """根据已发现的实现动态调整工具 schema。"""
    if method_name == "search":
        impls = mutobj.discover_subclasses(SearchImpl)
        if impls:
            impl_list = "、".join(f"{c.name}（{c.description}）" for c in impls)
            desc = f"搜索 Web 并返回结果摘要。可用实现：{impl_list}。"
        else:
            desc = "搜索 Web 并返回结果摘要。（无可用搜索实现）"
        return ToolSchema(
            name=schema.name,
            description=desc,
            input_schema=schema.input_schema,
        )

    if method_name == "fetch":
        fetch_impls = mutobj.discover_subclasses(FetchImpl)
        props = dict(schema.input_schema.get("properties", {}))
        if fetch_impls:
            impl_list = "、".join(f"{c.name}（{c.description}）" for c in fetch_impls)
            desc = f"读取网页内容并返回文本。可用提取实现：{impl_list}。"
            props["format"] = {
                "type": "string",
                "description": (
                    '输出格式 — "markdown"（默认）、'
                    '"html"（提取后的正文）、"raw"（原始网页）。'
                ),
                "default": "markdown",
            }
            props["impl"] = {
                "type": "string",
                "description": f"提取实现（默认 \"{fetch_impls[0].name}\"）。",
                "default": "",
            }
        else:
            desc = "读取原始网页内容（HTML）。"
            props.pop("format", None)
            props.pop("impl", None)
        new_input = dict(schema.input_schema)
        new_input["properties"] = props
        new_input["required"] = ["url"]
        return ToolSchema(
            name=schema.name,
            description=desc,
            input_schema=new_input,
        )

    return schema
