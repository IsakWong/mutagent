"""mutagent.http -- HTTP 客户端工厂（Declaration）。"""

from __future__ import annotations

from typing import Any

import httpx

import mutagent


class HttpClient(mutagent.Declaration):
    """HTTP 客户端工厂。

    提供统一的 httpx.AsyncClient 创建入口，集中管理默认 headers（User-Agent 等）。
    上层项目（如 mutbot）可通过 @impl 覆盖以定制 User-Agent。
    """

    @staticmethod
    def create(**kwargs: Any) -> httpx.AsyncClient:
        """创建 httpx.AsyncClient，统一设置默认 headers。"""
        ...


@mutagent.impl(HttpClient.create)
def _create(**kwargs: Any) -> httpx.AsyncClient:
    headers: dict[str, str] = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("user-agent", f"mutagent/{mutagent.__version__}")
    kwargs["headers"] = headers
    return httpx.AsyncClient(**kwargs)
