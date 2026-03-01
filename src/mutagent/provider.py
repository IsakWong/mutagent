"""mutagent.provider -- LLM provider abstraction."""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

import mutagent

if TYPE_CHECKING:
    from mutagent.messages import Message, StreamEvent, ToolSchema


class LLMProvider(mutagent.Declaration):
    """LLM 提供商抽象基类。

    子类通过 mutobj 子类发现机制自动注册。
    配置中指定类路径，resolve_class 自动加载。

    子类需实现 ``from_config`` 和 ``send`` 方法。
    """

    @classmethod
    def from_config(cls, model_config: dict) -> LLMProvider:
        """从模型配置创建 provider 实例。子类覆盖此方法。"""
        ...

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
