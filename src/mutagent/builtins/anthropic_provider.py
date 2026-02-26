"""mutagent.builtins.anthropic_provider -- Anthropic Claude API provider."""

import json
import logging
import time
from typing import Any, AsyncIterator

import httpx

from mutagent.messages import (
    Message,
    Response,
    StreamEvent,
    ToolCall,
    ToolSchema,
)
from mutagent.provider import LLMProvider

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider。

    Attributes:
        base_url: API 基础 URL（如 "https://api.anthropic.com"）。
        api_key: Anthropic API key。
    """

    base_url: str
    api_key: str

    @classmethod
    def from_config(cls, config: dict) -> "AnthropicProvider":
        if not config.get("auth_token"):
            raise ValueError("AnthropicProvider requires 'auth_token' in model config.")
        return cls(
            base_url=config.get("base_url", "https://api.anthropic.com"),
            api_key=config["auth_token"],
        )

    async def send(
        self,
        model: str,
        messages: list[Message],
        tools: list[ToolSchema],
        system_prompt: str = "",
        stream: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        """Send messages to Claude API and yield streaming events."""
        claude_messages = _messages_to_claude(messages)
        payload: dict[str, Any] = {
            "model": model,
            "messages": claude_messages,
            "max_tokens": 4096,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if tools:
            payload["tools"] = _tools_to_claude(tools)

        headers = {
            "authorization": f"Bearer {self.api_key}",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        if stream:
            async for event in _send_stream(self.base_url, payload, headers):
                yield event
        else:
            async for event in _send_no_stream(self.base_url, payload, headers):
                yield event


def _messages_to_claude(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert internal Message list to Claude API messages format.

    Handles consecutive same-role messages by merging them into a single
    message with a content array (required by Anthropic API).
    """
    result = []
    for msg in messages:
        if msg.role == "user" and msg.tool_results:
            content = []
            for tr in msg.tool_results:
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": tr.tool_call_id,
                    "content": tr.content,
                }
                if tr.is_error:
                    block["is_error"] = True
                content.append(block)
            # Also include text content if present
            if msg.content:
                content.append({"type": "text", "text": msg.content})
            result.append({"role": "user", "content": content})
        elif msg.role == "assistant" and msg.tool_calls:
            content_list: list[dict[str, Any]] = []
            if msg.content:
                content_list.append({"type": "text", "text": msg.content})
            for tc in msg.tool_calls:
                content_list.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.arguments,
                })
            result.append({"role": "assistant", "content": content_list})
        else:
            result.append({"role": msg.role, "content": msg.content})

    # Merge consecutive same-role messages (Anthropic API requirement)
    return _merge_consecutive_roles(result)


def _merge_consecutive_roles(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive messages with the same role into one.

    Anthropic API rejects consecutive same-role messages. This merges them
    by converting content to an array of content blocks.
    """
    if not messages:
        return messages
    merged: list[dict[str, Any]] = [messages[0]]
    for msg in messages[1:]:
        if msg["role"] == merged[-1]["role"]:
            # Same role — merge content into array
            prev = merged[-1]
            prev_content = _to_content_blocks(prev["content"])
            cur_content = _to_content_blocks(msg["content"])
            prev["content"] = prev_content + cur_content
        else:
            merged.append(msg)
    return merged


def _to_content_blocks(content: Any) -> list[dict[str, Any]]:
    """Normalize message content to a list of content blocks."""
    if isinstance(content, list):
        return content
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    return []


def _tools_to_claude(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    """Convert internal ToolSchema list to Claude API tools format."""
    result = []
    for tool in tools:
        entry: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema or {"type": "object", "properties": {}},
        }
        result.append(entry)
    return result


def _response_from_claude(data: dict[str, Any]) -> Response:
    """Convert Claude API response to internal Response."""
    stop_reason = data.get("stop_reason", "")
    usage = data.get("usage", {})

    content_blocks = data.get("content", [])
    text_parts = []
    tool_calls = []

    for block in content_blocks:
        if block["type"] == "text":
            text_parts.append(block["text"])
        elif block["type"] == "tool_use":
            tool_calls.append(ToolCall(
                id=block["id"],
                name=block["name"],
                arguments=block.get("input", {}),
            ))

    message = Message(
        role="assistant",
        content="\n".join(text_parts),
        tool_calls=tool_calls,
    )

    return Response(
        message=message,
        stop_reason=stop_reason,
        usage=usage,
    )


def _response_to_dict(response: Response) -> dict[str, Any]:
    """Convert a Response object to a plain dict for recording."""
    content: list[dict[str, Any]] = []
    if response.message.content:
        content.append({"type": "text", "text": response.message.content})
    for tc in response.message.tool_calls:
        content.append({
            "type": "tool_use",
            "id": tc.id,
            "name": tc.name,
            "input": tc.arguments,
        })
    return {
        "content": content,
        "stop_reason": response.stop_reason,
    }


async def _send_no_stream(
    base_url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> AsyncIterator[StreamEvent]:
    """Non-streaming path: make a regular HTTP request and wrap as StreamEvents."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10)) as client:
        resp = await client.post(
            f"{base_url}/v1/messages",
            headers=headers,
            json=payload,
        )
    data = resp.json()
    if resp.status_code != 200:
        error_msg = data.get("error", {}).get("message", json.dumps(data))
        logger.warning("API error (%d): %s", resp.status_code, error_msg)
        yield StreamEvent(
            type="error",
            error=f"Claude API error ({resp.status_code}): {error_msg}",
        )
        return

    response = _response_from_claude(data)

    if response.message.content:
        yield StreamEvent(type="text_delta", text=response.message.content)

    for tc in response.message.tool_calls:
        yield StreamEvent(type="tool_use_start", tool_call=tc)
        yield StreamEvent(type="tool_use_end")

    yield StreamEvent(type="response_done", response=response)


async def _send_stream(
    base_url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> AsyncIterator[StreamEvent]:
    """Streaming path: parse SSE events from Claude API and yield StreamEvents."""
    payload["stream"] = True

    async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10)) as client:
        async with client.stream(
            "POST",
            f"{base_url}/v1/messages",
            headers=headers,
            json=payload,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                try:
                    data = json.loads(body)
                    error_msg = data.get("error", {}).get("message", json.dumps(data))
                except Exception:
                    error_msg = f"HTTP {resp.status_code}"
                logger.warning("API stream error (%d): %s", resp.status_code, error_msg)
                yield StreamEvent(
                    type="error",
                    error=f"Claude API error ({resp.status_code}): {error_msg}",
                )
                return

            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            stop_reason = ""
            usage: dict[str, int] = {}

            current_block_type: str = ""
            current_tool_id: str = ""
            current_tool_name: str = ""
            current_tool_json_parts: list[str] = []

            event_type = ""
            async for raw_line in resp.aiter_lines():
                line = raw_line

                if line.startswith("event: "):
                    event_type = line[7:]
                    continue

                if line.startswith("data: "):
                    data_str = line[6:]
                    if not event_type:
                        continue

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    try:
                        if event_type == "message_start":
                            msg_data = data.get("message", {})
                            usage.update(msg_data.get("usage", {}))

                        elif event_type == "content_block_start":
                            block = data.get("content_block", {})
                            current_block_type = block.get("type", "")
                            if current_block_type == "tool_use":
                                current_tool_id = block.get("id", "")
                                current_tool_name = block.get("name", "")
                                current_tool_json_parts = []
                                tc = ToolCall(
                                    id=current_tool_id,
                                    name=current_tool_name,
                                )
                                yield StreamEvent(
                                    type="tool_use_start", tool_call=tc
                                )

                        elif event_type == "content_block_delta":
                            delta = data.get("delta", {})
                            delta_type = delta.get("type", "")
                            if delta_type == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    text_parts.append(text)
                                    yield StreamEvent(
                                        type="text_delta", text=text
                                    )
                            elif delta_type == "input_json_delta":
                                json_chunk = delta.get("partial_json", "")
                                if json_chunk:
                                    current_tool_json_parts.append(json_chunk)
                                    yield StreamEvent(
                                        type="tool_use_delta",
                                        tool_json_delta=json_chunk,
                                    )

                        elif event_type == "content_block_stop":
                            if current_block_type == "tool_use":
                                json_str = "".join(current_tool_json_parts)
                                try:
                                    arguments = json.loads(json_str) if json_str else {}
                                except json.JSONDecodeError:
                                    arguments = {}
                                tool_calls.append(ToolCall(
                                    id=current_tool_id,
                                    name=current_tool_name,
                                    arguments=arguments,
                                ))
                                yield StreamEvent(type="tool_use_end")
                            current_block_type = ""

                        elif event_type == "message_delta":
                            delta = data.get("delta", {})
                            stop_reason = delta.get("stop_reason", stop_reason)
                            usage.update(data.get("usage", {}))

                        elif event_type == "message_stop":
                            message = Message(
                                role="assistant",
                                content="".join(text_parts),
                                tool_calls=tool_calls,
                            )
                            response = Response(
                                message=message,
                                stop_reason=stop_reason,
                                usage=usage,
                            )
                            yield StreamEvent(
                                type="response_done", response=response
                            )

                    except Exception as e:
                        yield StreamEvent(
                            type="error",
                            error=f"Error processing SSE event '{event_type}': {e}",
                        )

                    event_type = ""
                    continue

                if not line:
                    event_type = ""
