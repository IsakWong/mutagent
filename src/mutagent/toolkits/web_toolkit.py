"""mutagent.toolkits.web_toolkit -- WebToolkit declaration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mutagent.tools import Toolkit

if TYPE_CHECKING:
    from mutagent.config import Config


class WebToolkit(Toolkit):
    """Web 信息检索工具集。

    Attributes:
        config: 配置对象，用于读取 API key 等设置。
    """

    config: Config

    def search(self, query: str, max_results: int = 5) -> str:
        """搜索 Web 并返回结果摘要。

        Args:
            query: 搜索关键词。
            max_results: 最大返回结果数。

        Returns:
            搜索结果列表，包含标题、URL、摘要。
        """
        return web_impl_jina.search(self, query, max_results)

    def fetch(self, url: str) -> str:
        """读取网页内容并返回文本。

        Args:
            url: 要读取的网页 URL。

        Returns:
            网页的主要文本内容（Markdown 格式）。
        """
        return web_impl_jina.fetch(self, url)


from mutagent.builtins import web_impl_jina
import mutagent
mutagent.register_module_impls(web_impl_jina)
