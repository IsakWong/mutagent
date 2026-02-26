"""mutagent.client -- LLM client declaration."""

from __future__ import annotations

import fnmatch
import logging
import time
from typing import TYPE_CHECKING, Any, AsyncIterator

import mutagent

if TYPE_CHECKING:
    from mutagent.messages import Message, StreamEvent, ToolSchema
    from mutagent.provider import LLMProvider
    from mutagent.runtime.api_recorder import ApiRecorder

logger = logging.getLogger(__name__)

# 常见模型的 context window 大小（token 数），作为配置未指定时的兜底。
# 支持通配符：精确匹配优先，通配符按长度降序匹配（更长 = 更具体）。
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # Anthropic
    "claude-*": 200_000,
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "o1": 200_000,
    "o3-mini": 200_000,
}


def get_model_context_window(model_id: str) -> int | None:
    """Look up context window for *model_id* from ``MODEL_CONTEXT_WINDOWS``.

    Resolution order:
    1. Exact match (key has no wildcard chars ``*`` / ``?``).
    2. Wildcard patterns via :func:`fnmatch.fnmatch`, longest pattern first.

    Returns ``None`` if no entry matches.
    """
    # 1. Exact match
    if model_id in MODEL_CONTEXT_WINDOWS:
        val = MODEL_CONTEXT_WINDOWS[model_id]
        # Only count as exact if key contains no wildcard
        key = model_id
        if "*" not in key and "?" not in key:
            return val

    # 2. Wildcard: collect matching patterns, longest first
    for pattern, val in sorted(
        MODEL_CONTEXT_WINDOWS.items(), key=lambda kv: len(kv[0]), reverse=True
    ):
        if ("*" in pattern or "?" in pattern) and fnmatch.fnmatch(model_id, pattern):
            return val

    return None


class LLMClient(mutagent.Declaration):
    """LLM client interface.

    组合 LLMProvider + API 录制。Provider 负责实际的 HTTP 调用，
    LLMClient 负责调用计时、日志和 API 录制。

    Attributes:
        provider: LLM 提供商实例。
        model: Model identifier (e.g. "claude-sonnet-4-20250514").
        context_window: 模型的上下文窗口大小（token 数），未知时为 None。
        api_recorder: Optional API call recorder for session logging.
    """

    provider: LLMProvider
    model: str
    context_window: int | None = None
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
