"""mutagent.provider -- LLM provider abstraction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator

import mutagent

if TYPE_CHECKING:
    from mutagent.config import Config
    from mutagent.messages import Message, StreamEvent, ToolSchema


class LLMProvider(mutagent.Declaration):
    """LLM 提供商抽象基类。

    子类通过 mutobj 子类发现机制自动注册。
    配置中指定类路径，resolve_class 自动加载。

    子类需实现 ``from_spec`` 和 ``send`` 方法。
    """

    @classmethod
    def from_spec(cls, spec: dict) -> LLMProvider:
        """从模型规格创建 provider 实例。子类覆盖此方法。

        spec 包含 provider、auth_token、base_url、model_id 等字段。
        """
        ...

    @classmethod
    def resolve_model(cls, config: Config, name: str | None = None) -> dict | None:
        """从 Config 中查找并组装指定模型的 spec。

        name 为 None 时使用默认模型。找不到时返回 None。
        """
        ...  # Declaration 桩：返回 None

    @classmethod
    def list_models(cls, config: Config) -> list[dict]:
        """列出 Config 中所有已配置的模型 spec。"""
        ...  # Declaration 桩：返回 []

    async def send(
        self,
        model: str,
        messages: list[Message],
        tools: list[ToolSchema],
        prompts: list[Message] | None = None,
        stream: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        """发送请求到 LLM 后端，返回流式事件。

        Args:
            model: 模型 ID（如 "claude-sonnet-4-20250514"）。
            messages: 对话历史。
            tools: 可用工具 schema 列表。
            prompts: 系统指令 Message 列表。
            stream: 是否使用 SSE 流式请求。

        Yields:
            StreamEvent 实例。最后一个事件始终为 ``response_done``。
        """
        ...


# --- 注册默认实现 ---
from mutagent.builtins import provider_impl  # noqa: E402
mutagent.register_module_impls(provider_impl)
