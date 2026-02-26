"""mutagent.client -- LLM client declaration."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, AsyncIterator

import mutagent

if TYPE_CHECKING:
    from mutagent.messages import Message, StreamEvent, ToolSchema
    from mutagent.provider import LLMProvider
    from mutagent.runtime.api_recorder import ApiRecorder

logger = logging.getLogger(__name__)


class LLMClient(mutagent.Declaration):
    """LLM client interface.

    组合 LLMProvider + API 录制。Provider 负责实际的 HTTP 调用，
    LLMClient 负责调用计时、日志和 API 录制。

    Attributes:
        provider: LLM 提供商实例。
        model: Model identifier (e.g. "claude-sonnet-4-20250514").
        api_recorder: Optional API call recorder for session logging.
    """

    provider: LLMProvider
    model: str
    api_recorder: ApiRecorder | None = None

    async def send_message(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
        system_prompt: str = "",
        stream: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        """Send messages to the LLM and yield streaming events.

        Delegates to provider.send() and handles API recording.

        Args:
            messages: Conversation history.
            tools: Available tool schemas for the LLM to use.
            system_prompt: System-level instruction for the LLM.
            stream: Whether to use SSE streaming for the HTTP request.

        Yields:
            StreamEvent instances.
        """
        ...


from .builtins import client_impl
mutagent.register_module_impls(client_impl)
