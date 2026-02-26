"""Tests for OpenAI API implementation (builtins/openai_provider.py)."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from mutagent.messages import Message, Response, ToolCall, ToolResult, ToolSchema
from mutagent.builtins.openai_provider import (
    OpenAIProvider,
    _messages_to_openai,
    _tools_to_openai,
    _response_from_openai,
)


class TestMessagesToOpenAI:

    def test_simple_user_message(self):
        msgs = [Message(role="user", content="Hello")]
        result = _messages_to_openai(msgs)
        assert result == [{"role": "user", "content": "Hello"}]

    def test_simple_assistant_message(self):
        msgs = [Message(role="assistant", content="Hi there")]
        result = _messages_to_openai(msgs)
        assert result == [{"role": "assistant", "content": "Hi there"}]

    def test_assistant_with_tool_calls_and_content(self):
        tc = ToolCall(id="call_1", name="get_weather", arguments={"city": "Tokyo"})
        msgs = [Message(role="assistant", content="Let me check.", tool_calls=[tc])]
        result = _messages_to_openai(msgs)

        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "Let me check."
        assert len(result[0]["tool_calls"]) == 1
        assert result[0]["tool_calls"][0] == {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "get_weather",
                "arguments": json.dumps({"city": "Tokyo"}),
            },
        }

    def test_assistant_tool_calls_no_text(self):
        tc = ToolCall(id="call_1", name="run_code", arguments={"code": "1+1"})
        msgs = [Message(role="assistant", content="", tool_calls=[tc])]
        result = _messages_to_openai(msgs)

        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        # When content is empty, OpenAI format should set content to None
        assert result[0]["content"] is None
        assert len(result[0]["tool_calls"]) == 1
        assert result[0]["tool_calls"][0]["function"]["name"] == "run_code"

    def test_assistant_multiple_tool_calls(self):
        tc1 = ToolCall(id="call_1", name="tool_a", arguments={"x": 1})
        tc2 = ToolCall(id="call_2", name="tool_b", arguments={"y": 2})
        msgs = [Message(role="assistant", content="", tool_calls=[tc1, tc2])]
        result = _messages_to_openai(msgs)

        assert len(result) == 1
        assert len(result[0]["tool_calls"]) == 2
        assert result[0]["tool_calls"][0]["id"] == "call_1"
        assert result[0]["tool_calls"][1]["id"] == "call_2"

    def test_user_with_tool_results(self):
        """Tool results become separate 'tool' role messages in OpenAI format."""
        tr = ToolResult(tool_call_id="call_1", content="42")
        msgs = [Message(role="user", tool_results=[tr])]
        result = _messages_to_openai(msgs)

        assert len(result) == 1
        assert result[0] == {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "42",
        }

    def test_user_with_multiple_tool_results(self):
        """Multiple tool results become multiple 'tool' role messages."""
        tr1 = ToolResult(tool_call_id="call_1", content="result_1")
        tr2 = ToolResult(tool_call_id="call_2", content="result_2")
        msgs = [Message(role="user", tool_results=[tr1, tr2])]
        result = _messages_to_openai(msgs)

        assert len(result) == 2
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "call_1"
        assert result[0]["content"] == "result_1"
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "call_2"
        assert result[1]["content"] == "result_2"

    def test_multi_turn_conversation(self):
        msgs = [
            Message(role="user", content="Hi"),
            Message(role="assistant", content="Hello!"),
            Message(role="user", content="Help me"),
        ]
        result = _messages_to_openai(msgs)
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"

    def test_tool_call_arguments_serialized_to_json(self):
        """Verify arguments dict is serialized to JSON string in OpenAI format."""
        tc = ToolCall(id="call_x", name="func", arguments={"a": [1, 2], "b": True})
        msgs = [Message(role="assistant", content="", tool_calls=[tc])]
        result = _messages_to_openai(msgs)

        args_str = result[0]["tool_calls"][0]["function"]["arguments"]
        assert isinstance(args_str, str)
        assert json.loads(args_str) == {"a": [1, 2], "b": True}


class TestToolsToOpenAI:

    def test_single_tool(self):
        tools = [ToolSchema(
            name="get_weather",
            description="Get current weather for a city.",
            input_schema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                },
                "required": ["city"],
            },
        )]
        result = _tools_to_openai(tools)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "get_weather"
        assert result[0]["function"]["description"] == "Get current weather for a city."
        assert "properties" in result[0]["function"]["parameters"]
        assert result[0]["function"]["parameters"]["required"] == ["city"]

    def test_empty_tools(self):
        result = _tools_to_openai([])
        assert result == []

    def test_tool_with_empty_schema(self):
        tools = [ToolSchema(name="noop", description="Does nothing")]
        result = _tools_to_openai(tools)
        assert result[0]["function"]["parameters"] == {"type": "object", "properties": {}}

    def test_multiple_tools(self):
        tools = [
            ToolSchema(name="tool_a", description="Tool A", input_schema={"type": "object", "properties": {"x": {"type": "integer"}}}),
            ToolSchema(name="tool_b", description="Tool B", input_schema={"type": "object", "properties": {"y": {"type": "string"}}}),
        ]
        result = _tools_to_openai(tools)
        assert len(result) == 2
        assert result[0]["function"]["name"] == "tool_a"
        assert result[1]["function"]["name"] == "tool_b"
        assert all(r["type"] == "function" for r in result)


class TestResponseFromOpenAI:

    def test_text_response(self):
        data = {
            "choices": [{
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        resp = _response_from_openai(data)
        assert resp.message.role == "assistant"
        assert resp.message.content == "Hello!"
        assert resp.stop_reason == "end_turn"
        assert resp.usage == {"input_tokens": 10, "output_tokens": 5}

    def test_tool_use_response(self):
        data = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "I'll check that.",
                    "tool_calls": [{
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": json.dumps({"city": "Tokyo"}),
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 15},
        }
        resp = _response_from_openai(data)
        assert resp.message.content == "I'll check that."
        assert len(resp.message.tool_calls) == 1
        tc = resp.message.tool_calls[0]
        assert tc.id == "call_123"
        assert tc.name == "get_weather"
        assert tc.arguments == {"city": "Tokyo"}
        assert resp.stop_reason == "tool_use"

    def test_multiple_tool_calls(self):
        data = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "tool_a",
                                "arguments": json.dumps({"x": 1}),
                            },
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {
                                "name": "tool_b",
                                "arguments": json.dumps({"y": "hello"}),
                            },
                        },
                    ],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {},
        }
        resp = _response_from_openai(data)
        assert len(resp.message.tool_calls) == 2
        assert resp.message.content == ""
        assert resp.message.tool_calls[0].name == "tool_a"
        assert resp.message.tool_calls[1].name == "tool_b"

    def test_empty_content(self):
        data = {
            "choices": [{
                "message": {"role": "assistant", "content": ""},
                "finish_reason": "stop",
            }],
            "usage": {},
        }
        resp = _response_from_openai(data)
        assert resp.message.content == ""
        assert resp.message.tool_calls == []

    def test_null_content_becomes_empty_string(self):
        data = {
            "choices": [{
                "message": {"role": "assistant", "content": None},
                "finish_reason": "stop",
            }],
            "usage": {},
        }
        resp = _response_from_openai(data)
        assert resp.message.content == ""

    def test_stop_reason_mapping_length(self):
        data = {
            "choices": [{
                "message": {"role": "assistant", "content": "truncated..."},
                "finish_reason": "length",
            }],
            "usage": {},
        }
        resp = _response_from_openai(data)
        assert resp.stop_reason == "max_tokens"

    def test_stop_reason_mapping_content_filter(self):
        data = {
            "choices": [{
                "message": {"role": "assistant", "content": ""},
                "finish_reason": "content_filter",
            }],
            "usage": {},
        }
        resp = _response_from_openai(data)
        assert resp.stop_reason == "content_filter"

    def test_stop_reason_unknown_passes_through(self):
        data = {
            "choices": [{
                "message": {"role": "assistant", "content": ""},
                "finish_reason": "some_unknown_reason",
            }],
            "usage": {},
        }
        resp = _response_from_openai(data)
        assert resp.stop_reason == "some_unknown_reason"

    def test_usage_mapping(self):
        data = {
            "choices": [{
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
        resp = _response_from_openai(data)
        assert resp.usage["input_tokens"] == 100
        assert resp.usage["output_tokens"] == 50

    def test_usage_missing_fields(self):
        data = {
            "choices": [{
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }],
            "usage": {},
        }
        resp = _response_from_openai(data)
        assert resp.usage == {}

    def test_invalid_tool_call_arguments_json(self):
        """Malformed JSON in tool call arguments should default to empty dict."""
        data = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_bad",
                        "type": "function",
                        "function": {
                            "name": "broken_tool",
                            "arguments": "not-valid-json{{{",
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {},
        }
        resp = _response_from_openai(data)
        assert len(resp.message.tool_calls) == 1
        assert resp.message.tool_calls[0].arguments == {}


def _make_client():
    """创建测试用 LLMClient（使用 OpenAIProvider）。"""
    from mutagent.client import LLMClient
    provider = OpenAIProvider(
        base_url="https://api.openai.com/v1",
        api_key="test-key",
    )
    return LLMClient(provider=provider, model="gpt-4o")


class TestSendMessageIntegration:

    def test_send_message_success(self):
        """Test send_message with a mocked requests response (stream=False)."""
        mock_response_data = {
            "choices": [{
                "message": {"role": "assistant", "content": "Hello from OpenAI!"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response_data

        with patch("requests.post", return_value=mock_resp):
            client = _make_client()
            messages = [Message(role="user", content="Hi")]
            events = list(client.send_message(messages, [], stream=False))

        resp_event = [e for e in events if e.type == "response_done"][0]
        resp = resp_event.response
        assert resp.message.content == "Hello from OpenAI!"
        assert resp.stop_reason == "end_turn"
        assert resp.usage["input_tokens"] == 5
        assert resp.usage["output_tokens"] == 3

    def test_send_message_with_tools(self):
        """Test send_message includes tools in the request."""
        mock_response_data = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": json.dumps({"city": "Tokyo"}),
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8},
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response_data

        tools = [ToolSchema(
            name="get_weather",
            description="Get current weather for a city.",
            input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
        )]

        with patch("requests.post", return_value=mock_resp):
            client = _make_client()
            messages = [Message(role="user", content="What's the weather?")]
            events = list(client.send_message(messages, tools, stream=False))

        resp_event = [e for e in events if e.type == "response_done"][0]
        resp = resp_event.response
        assert resp.stop_reason == "tool_use"
        assert len(resp.message.tool_calls) == 1
        assert resp.message.tool_calls[0].name == "get_weather"
        assert resp.message.tool_calls[0].arguments == {"city": "Tokyo"}

    def test_send_message_api_error(self):
        """Test send_message yields error event on API error."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {
            "error": {"message": "Incorrect API key provided"}
        }

        with patch("requests.post", return_value=mock_resp):
            client = _make_client()
            events = list(client.send_message(
                [Message(role="user", content="Hi")], [], stream=False
            ))

        assert len(events) == 1
        assert events[0].type == "error"
        assert "Incorrect API key provided" in events[0].error

    def test_send_message_text_delta_emitted(self):
        """Test that a text_delta event is emitted before response_done."""
        mock_response_data = {
            "choices": [{
                "message": {"role": "assistant", "content": "Some text reply"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 4},
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response_data

        with patch("requests.post", return_value=mock_resp):
            client = _make_client()
            events = list(client.send_message(
                [Message(role="user", content="Hi")], [], stream=False
            ))

        text_events = [e for e in events if e.type == "text_delta"]
        assert len(text_events) == 1
        assert text_events[0].text == "Some text reply"

    def test_send_message_tool_use_events(self):
        """Test that tool_use_start and tool_use_end events are emitted for each tool call."""
        mock_response_data = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "tool_a",
                                "arguments": json.dumps({"x": 1}),
                            },
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {
                                "name": "tool_b",
                                "arguments": json.dumps({"y": 2}),
                            },
                        },
                    ],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 12},
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response_data

        with patch("requests.post", return_value=mock_resp):
            client = _make_client()
            events = list(client.send_message(
                [Message(role="user", content="Do stuff")], [], stream=False
            ))

        tool_start_events = [e for e in events if e.type == "tool_use_start"]
        tool_end_events = [e for e in events if e.type == "tool_use_end"]
        assert len(tool_start_events) == 2
        assert len(tool_end_events) == 2
        assert tool_start_events[0].tool_call.name == "tool_a"
        assert tool_start_events[1].tool_call.name == "tool_b"

    def test_send_message_system_prompt(self):
        """Test that system_prompt is prepended as a system message."""
        mock_response_data = {
            "choices": [{
                "message": {"role": "assistant", "content": "OK"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 15, "completion_tokens": 1},
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response_data
        captured_payload = {}

        def capture_post(url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return mock_resp

        with patch("requests.post", side_effect=capture_post):
            client = _make_client()
            messages = [Message(role="user", content="Hi")]
            list(client.send_message(
                messages, [], system_prompt="You are helpful.", stream=False
            ))

        # Verify system message was prepended
        msgs = captured_payload["messages"]
        assert msgs[0] == {"role": "system", "content": "You are helpful."}
        assert msgs[1] == {"role": "user", "content": "Hi"}

    def test_send_message_no_system_prompt(self):
        """Test that no system message is added when system_prompt is empty."""
        mock_response_data = {
            "choices": [{
                "message": {"role": "assistant", "content": "OK"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1},
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response_data
        captured_payload = {}

        def capture_post(url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return mock_resp

        with patch("requests.post", side_effect=capture_post):
            client = _make_client()
            messages = [Message(role="user", content="Hi")]
            list(client.send_message(messages, [], stream=False))

        msgs = captured_payload["messages"]
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"


class TestOpenAIProviderFromConfig:

    def test_from_config_defaults(self):
        provider = OpenAIProvider.from_config({"auth_token": "sk-test"})
        assert provider.base_url == "https://api.openai.com/v1"
        assert provider.api_key == "sk-test"

    def test_from_config_missing_auth_token(self):
        with pytest.raises(ValueError, match="auth_token"):
            OpenAIProvider.from_config({})

    def test_from_config_custom(self):
        config = {
            "base_url": "https://api.groq.com/openai/v1",
            "auth_token": "gsk_abc123",
        }
        provider = OpenAIProvider.from_config(config)
        assert provider.base_url == "https://api.groq.com/openai/v1"
        assert provider.api_key == "gsk_abc123"


_has_openai_key = bool(os.environ.get("OPENAI_API_KEY"))


@pytest.mark.skipif(not _has_openai_key, reason="OPENAI_API_KEY not set")
class TestOpenAIRealAPI:
    """Integration tests using the real OpenAI API (skipped without API key)."""

    def _make_real_client(self):
        from mutagent.client import LLMClient
        provider = OpenAIProvider(
            base_url="https://api.openai.com/v1",
            api_key=os.environ["OPENAI_API_KEY"],
        )
        return LLMClient(provider=provider, model="gpt-4o-mini")

    def test_real_send_message(self):
        """Send a real message to OpenAI API and verify the response structure."""
        client = self._make_real_client()
        messages = [Message(role="user", content="Reply with exactly: PONG")]
        events = list(client.send_message(messages, []))

        resp_event = [e for e in events if e.type == "response_done"][0]
        resp = resp_event.response
        assert isinstance(resp, Response)
        assert resp.message.role == "assistant"
        assert resp.message.content
        assert resp.stop_reason == "end_turn"
        assert resp.usage.get("input_tokens", 0) > 0
        assert resp.usage.get("output_tokens", 0) > 0

    def test_real_send_message_with_tool_use(self):
        """Send a real message with tools and verify tool_use response."""
        client = self._make_real_client()
        tools = [ToolSchema(
            name="get_weather",
            description="Get current weather for a city.",
            input_schema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                },
                "required": ["city"],
            },
        )]
        messages = [Message(role="user", content="What's the weather in Tokyo?")]
        events = list(client.send_message(messages, tools))

        resp_event = [e for e in events if e.type == "response_done"][0]
        resp = resp_event.response
        assert isinstance(resp, Response)
        assert resp.message.role == "assistant"
        assert resp.stop_reason == "tool_use"
        assert len(resp.message.tool_calls) >= 1
        tc = resp.message.tool_calls[0]
        assert tc.name == "get_weather"
        assert "city" in tc.arguments
