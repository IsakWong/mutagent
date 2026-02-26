"""mutagent.builtins.openai_provider -- OpenAI Chat Completions API provider."""

import json
import logging
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


class OpenAIProvider(LLMProvider):
    """OpenAI Chat Completions 格式 API provider。

    兼容所有使用 OpenAI Chat Completions 格式的 API（如 OpenAI、Groq 等）。

    Attributes:
        base_url: API 基础 URL（如 "https://api.openai.com/v1"）。
        api_key: API key。
    """

    base_url: str
    api_key: str

    @classmethod
    def from_config(cls, config: dict) -> "OpenAIProvider":
        if not config.get("auth_token"):
            raise ValueError("OpenAIProvider requires 'auth_token' in model config.")
        return cls(
            base_url=config.get("base_url", "https://api.openai.com/v1"),
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
        """Send messages to OpenAI-compatible API and yield streaming events."""
        openai_messages = _messages_to_openai(messages)
        if system_prompt:
            openai_messages.insert(0, {"role": "system", "content": system_prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
        }
        if tools:
            payload["tools"] = _tools_to_openai(tools)

        headers = {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }

        if stream:
            async for event in _send_stream(self.base_url, payload, headers):
                yield event
        else:
            async for event in _send_no_stream(self.base_url, payload, headers):
                yield event


def _messages_to_openai(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert internal Message list to OpenAI messages format."""
    result = []
    for msg in messages:
        if msg.role == "user" and msg.tool_results:
            for tr in msg.tool_results:
                entry: dict[str, Any] = {
                    "role": "tool",
                    "tool_call_id": tr.tool_call_id,
                    "content": tr.content,
                }
                result.append(entry)
        elif msg.role == "assistant" and msg.tool_calls:
            entry = {"role": "assistant"}
            if msg.content:
                entry["content"] = msg.content
            else:
                entry["content"] = None
            tool_calls_list = []
            for tc in msg.tool_calls:
                tool_calls_list.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                })
            entry["tool_calls"] = tool_calls_list
            result.append(entry)
        else:
            result.append({"role": msg.role, "content": msg.content})
    return result


def _tools_to_openai(tools: list[ToolSchema]) -> list[dict[str, Any]]:
    """Convert internal ToolSchema list to OpenAI tools format."""
    result = []
    for tool in tools:
        result.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema or {"type": "object", "properties": {}},
            },
        })
    return result


def _response_from_openai(data: dict[str, Any]) -> Response:
    """Convert OpenAI API response to internal Response."""
    choice = data.get("choices", [{}])[0]
    message_data = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "")

    # Map finish_reason to stop_reason
    stop_reason_map = {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "length": "max_tokens",
        "content_filter": "content_filter",
    }
    stop_reason = stop_reason_map.get(finish_reason, finish_reason)

    # Parse tool calls
    tool_calls = []
    for tc_data in message_data.get("tool_calls", []):
        func = tc_data.get("function", {})
        try:
            arguments = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            arguments = {}
        tool_calls.append(ToolCall(
            id=tc_data.get("id", ""),
            name=func.get("name", ""),
            arguments=arguments,
        ))

    # Parse usage
    usage_data = data.get("usage", {})
    usage = {}
    if "prompt_tokens" in usage_data:
        usage["input_tokens"] = usage_data["prompt_tokens"]
    if "completion_tokens" in usage_data:
        usage["output_tokens"] = usage_data["completion_tokens"]

    message = Message(
        role="assistant",
        content=message_data.get("content", "") or "",
        tool_calls=tool_calls,
    )

    return Response(message=message, stop_reason=stop_reason, usage=usage)


async def _send_no_stream(
    base_url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> AsyncIterator[StreamEvent]:
    """Non-streaming path for OpenAI API."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10)) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
    data = resp.json()
    if resp.status_code != 200:
        error_msg = data.get("error", {}).get("message", json.dumps(data))
        logger.warning("OpenAI API error (%d): %s", resp.status_code, error_msg)
        yield StreamEvent(
            type="error",
            error=f"OpenAI API error ({resp.status_code}): {error_msg}",
        )
        return

    response = _response_from_openai(data)

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
    """Streaming path: parse OpenAI SSE and yield StreamEvents."""
    payload["stream"] = True
    payload["stream_options"] = {"include_usage": True}

    async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10)) as client:
        async with client.stream(
            "POST",
            f"{base_url}/chat/completions",
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
                logger.warning("OpenAI API stream error (%d): %s", resp.status_code, error_msg)
                yield StreamEvent(
                    type="error",
                    error=f"OpenAI API error ({resp.status_code}): {error_msg}",
                )
                return

            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            # Track tool call state by index
            tool_call_data: dict[int, dict[str, Any]] = {}
            stop_reason = ""
            usage: dict[str, int] = {}
            finish_reason = ""

            async for raw_line in resp.aiter_lines():
                line = raw_line

                if not line.startswith("data: "):
                    continue

                data_str = line[6:]
                if data_str == "[DONE]":
                    # Assemble final response
                    # Finalize any pending tool calls
                    for idx in sorted(tool_call_data.keys()):
                        tc_info = tool_call_data[idx]
                        json_str = tc_info.get("args_json", "")
                        try:
                            arguments = json.loads(json_str) if json_str else {}
                        except json.JSONDecodeError:
                            arguments = {}
                        tool_calls.append(ToolCall(
                            id=tc_info.get("id", ""),
                            name=tc_info.get("name", ""),
                            arguments=arguments,
                        ))
                        yield StreamEvent(type="tool_use_end")

                    # Map finish_reason
                    stop_reason_map = {
                        "stop": "end_turn",
                        "tool_calls": "tool_use",
                        "length": "max_tokens",
                    }
                    stop_reason = stop_reason_map.get(finish_reason, finish_reason)

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
                    yield StreamEvent(type="response_done", response=response)
                    break

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                # Usage chunk (with stream_options.include_usage)
                if data.get("usage"):
                    usage_data = data["usage"]
                    if "prompt_tokens" in usage_data:
                        usage["input_tokens"] = usage_data["prompt_tokens"]
                    if "completion_tokens" in usage_data:
                        usage["output_tokens"] = usage_data["completion_tokens"]

                choices = data.get("choices", [])
                if not choices:
                    continue

                delta = choices[0].get("delta", {})
                fr = choices[0].get("finish_reason")
                if fr:
                    finish_reason = fr

                # Text content
                content = delta.get("content")
                if content:
                    text_parts.append(content)
                    yield StreamEvent(type="text_delta", text=content)

                # Tool calls
                for tc_delta in delta.get("tool_calls", []):
                    idx = tc_delta.get("index", 0)
                    if idx not in tool_call_data:
                        # New tool call
                        func = tc_delta.get("function", {})
                        tool_call_data[idx] = {
                            "id": tc_delta.get("id", ""),
                            "name": func.get("name", ""),
                            "args_json": func.get("arguments", ""),
                        }
                        tc = ToolCall(
                            id=tc_delta.get("id", ""),
                            name=func.get("name", ""),
                        )
                        yield StreamEvent(type="tool_use_start", tool_call=tc)
                    else:
                        # Delta for existing tool call
                        func = tc_delta.get("function", {})
                        args_chunk = func.get("arguments", "")
                        if args_chunk:
                            tool_call_data[idx]["args_json"] += args_chunk
                            yield StreamEvent(
                                type="tool_use_delta",
                                tool_json_delta=args_chunk,
                            )
