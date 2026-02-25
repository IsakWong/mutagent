"""mutagent.builtins.web_impl_jina -- WebToolkit implementation using Jina AI APIs."""

from __future__ import annotations

import logging
from urllib.parse import quote

import requests

import mutagent
from mutagent.toolkits.web_toolkit import WebToolkit

logger = logging.getLogger(__name__)

# 内容截断阈值（字符数）
_MAX_CONTENT_CHARS = 50000

_SEARCH_API = "https://s.jina.ai/"
_READER_API = "https://r.jina.ai/"
_TIMEOUT = 30


def _get_headers(toolkit: WebToolkit) -> dict[str, str]:
    """构建请求头，包含可选的 API key。"""
    headers: dict[str, str] = {"Accept": "application/json"}
    api_key = toolkit.config.get("WebToolkit.jina_api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


@mutagent.impl(WebToolkit.search)
def search(self: WebToolkit, query: str, max_results: int = 5) -> str:
    """搜索 Web 并返回结果摘要。"""
    encoded_query = quote(query)
    url = f"{_SEARCH_API}{encoded_query}"
    headers = _get_headers(self)

    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.Timeout:
        return f"搜索超时（{_TIMEOUT}s）。请稍后重试。"
    except requests.RequestException as exc:
        logger.warning("Web search failed: %s", exc)
        return f"搜索失败：{exc}"

    try:
        data = resp.json()
    except ValueError:
        return "搜索返回了无法解析的响应。"

    items = data.get("data", [])
    if not items:
        return f"没有找到与 \"{query}\" 相关的结果。"

    items = items[:max_results]

    parts: list[str] = []
    parts.append(f"搜索结果：\"{query}\"（共 {len(items)} 条）\n")
    for i, item in enumerate(items, 1):
        title = item.get("title", "(无标题)")
        item_url = item.get("url", "")
        description = item.get("description", "")
        parts.append(f"### {i}. {title}")
        if item_url:
            parts.append(f"URL: {item_url}")
        if description:
            parts.append(f"{description}")
        parts.append("")

    return "\n".join(parts)


@mutagent.impl(WebToolkit.fetch)
def fetch(self: WebToolkit, url: str) -> str:
    """读取网页内容并返回文本。"""
    reader_url = f"{_READER_API}{url}"
    headers = _get_headers(self)

    try:
        resp = requests.get(reader_url, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.Timeout:
        return f"读取超时（{_TIMEOUT}s）。请稍后重试。"
    except requests.RequestException as exc:
        logger.warning("Web fetch failed for %s: %s", url, exc)
        return f"读取失败：{exc}"

    try:
        data = resp.json()
    except ValueError:
        # 如果不是 JSON，直接返回文本内容
        content = resp.text
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + "\n\n[内容已截断]"
        return content

    page = data.get("data", {})
    title = page.get("title", "")
    content = page.get("content", "")

    if not content:
        return f"无法提取 {url} 的内容。"

    parts: list[str] = []
    if title:
        parts.append(f"# {title}\n")
        parts.append(f"URL: {url}\n")
    if len(content) > _MAX_CONTENT_CHARS:
        content = content[:_MAX_CONTENT_CHARS] + "\n\n[内容已截断]"
    parts.append(content)

    return "\n".join(parts)
