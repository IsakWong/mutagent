"""mutagent.builtins.client_impl -- LLMClient implementation."""

import json
import logging
import time
from typing import Any, Iterator

import mutagent
from mutagent.client import LLMClient
from mutagent.messages import Message, Response, StreamEvent, ToolSchema

logger = logging.getLogger(__name__)


@mutagent.impl(LLMClient.send_message)
def send_message(
    self: LLMClient,
    messages: list[Message],
    tools: list[ToolSchema],
    system_prompt: str = "",
    stream: bool = True,
) -> Iterator[StreamEvent]:
    """Send messages via provider and handle logging + recording."""
    logger.info(
        "Sending API request (model=%s, messages=%d)",
        self.model, len(messages),
    )
    t0 = time.monotonic()

    response_obj: Response | None = None
    for event in self.provider.send(
        self.model, messages, tools, system_prompt, stream
    ):
        if event.type == "response_done":
            response_obj = event.response
        yield event

    duration_ms = int((time.monotonic() - t0) * 1000)

    if response_obj is not None:
        logger.info(
            "API response received (stop_reason=%s, duration=%dms)",
            response_obj.stop_reason, duration_ms,
        )
        logger.debug("Usage: %s", response_obj.usage)

        if self.api_recorder is not None:
            from mutagent.builtins.anthropic_provider import (
                _messages_to_claude,
                _response_to_dict,
            )
            claude_messages = _messages_to_claude(messages)
            new_message = claude_messages[-1] if claude_messages else {}
            response_data = _response_to_dict(response_obj)
            self.api_recorder.record_call(
                messages=claude_messages,
                new_message=new_message,
                response=response_data,
                usage=response_obj.usage,
                duration_ms=duration_ms,
            )
