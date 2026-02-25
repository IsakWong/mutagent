"""Tests for Agent declaration and main loop implementation."""

from unittest.mock import MagicMock

import pytest

import mutagent
from mutagent.agent import Agent
from mutagent.client import LLMClient
from mutagent.toolkits.module_toolkit import ModuleToolkit
from mutagent.toolkits.log_toolkit import LogToolkit
from mutagent.messages import (
    InputEvent,
    Message,
    Response,
    StreamEvent,
    ToolCall,
    ToolResult,
    ToolSchema,
)
from mutagent.runtime.module_manager import ModuleManager
from mutagent.tools import ToolSet
from mutobj.core import DeclarationMeta, _DECLARED_METHODS

import mutagent.builtins  # noqa: F401  -- register all @impl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _single_input(text: str):
    """Create an iterator yielding a single user_message InputEvent."""
    yield InputEvent(type="user_message", text=text)


def _multi_input(*texts: str):
    """Create an iterator yielding multiple user_message InputEvents."""
    for text in texts:
        yield InputEvent(type="user_message", text=text)


def _collect_events(iter):
    """Collect all StreamEvents from an iterator into a list."""
    return list(iter)


def _collect_text(iter):
    """Collect text from text_delta events."""
    text_parts = []
    for event in iter:
        if event.type == "text_delta":
            text_parts.append(event.text)
    return "".join(text_parts)


def _make_stream_events_for_response(response: Response) -> list[StreamEvent]:
    """Build the list of StreamEvents that a non-streaming send_message would yield."""
    events = []
    if response.message.content:
        events.append(StreamEvent(type="text_delta", text=response.message.content))
    for tc in response.message.tool_calls:
        events.append(StreamEvent(type="tool_use_start", tool_call=tc))
        events.append(StreamEvent(type="tool_use_end"))
    events.append(StreamEvent(type="response_done", response=response))
    return events


def _make_agent(mock_client=None):
    """Create an Agent with ToolSet for testing."""
    if mock_client is None:
        mock_client = LLMClient(
            model="test-model",
            api_key="test-key",
            base_url="https://api.test.com",
        )
    mgr = ModuleManager()
    module_tools = ModuleToolkit(module_manager=mgr)
    tool_set = ToolSet()
    tool_set.add(module_tools)
    agent = Agent(
        client=mock_client,
        tool_set=tool_set,
        system_prompt="You are a helpful assistant.",
        messages=[],
    )
    tool_set.agent = agent
    return agent, mgr


# ---------------------------------------------------------------------------
# Declaration tests
# ---------------------------------------------------------------------------

class TestAgentDeclaration:

    def test_inherits_from_mutagent_declaration(self):
        assert issubclass(Agent, mutagent.Declaration)

    def test_uses_declaration_meta(self):
        assert isinstance(Agent, DeclarationMeta)

    def test_declared_methods(self):
        declared = getattr(Agent, _DECLARED_METHODS, set())
        assert "run" in declared
        assert "step" in declared
        assert "handle_tool_calls" in declared


# ---------------------------------------------------------------------------
# Agent loop tests (adapted for streaming input interface)
# ---------------------------------------------------------------------------

class TestAgentLoop:

    @pytest.fixture
    def mock_client(self):
        """Create a mock LLM client."""
        client = LLMClient(
            model="test-model",
            api_key="test-key",
            base_url="https://api.test.com",
        )
        return client

    @pytest.fixture
    def agent(self, mock_client):
        agent, mgr = _make_agent(mock_client)
        yield agent
        mgr.cleanup()

    def test_simple_response(self, agent):
        """Agent receives a simple text response (no tool calls)."""
        response = Response(
            message=Message(role="assistant", content="Hello! How can I help?"),
            stop_reason="end_turn",
            usage={"input_tokens": 10, "output_tokens": 5},
        )
        events = _make_stream_events_for_response(response)

        def mock_send(*args, **kwargs):
            for e in events:
                yield e

        agent.client.send_message = mock_send

        text = _collect_text(agent.run(_single_input("Hi")))

        assert text == "Hello! How can I help?"
        assert len(agent.messages) == 2  # user + assistant
        assert agent.messages[0].role == "user"
        assert agent.messages[0].content == "Hi"
        assert agent.messages[1].role == "assistant"

    def test_tool_call_then_response(self, agent):
        """Agent handles a tool call then gets final response."""
        # First response: tool call
        tool_response = Response(
            message=Message(
                role="assistant",
                content="Let me inspect the module.",
                tool_calls=[ToolCall(id="tc_1", name="Module-inspect", arguments={"module_path": "mutagent"})],
            ),
            stop_reason="tool_use",
        )
        # Second response: final text
        final_response = Response(
            message=Message(role="assistant", content="The result is ready."),
            stop_reason="end_turn",
        )

        events_1 = _make_stream_events_for_response(tool_response)
        events_2 = _make_stream_events_for_response(final_response)
        call_idx = 0

        def mock_send(*args, **kwargs):
            nonlocal call_idx
            evts = [events_1, events_2][call_idx]
            call_idx += 1
            for e in evts:
                yield e

        agent.client.send_message = mock_send

        text = _collect_text(agent.run(_single_input("What is 1+1?")))

        assert text == "Let me inspect the module.The result is ready."
        assert len(agent.messages) == 4  # user, assistant(tool_call), user(tool_result), assistant(final)
        assert agent.messages[2].role == "user"
        assert len(agent.messages[2].tool_results) == 1
        assert "mutagent" in agent.messages[2].tool_results[0].content

    def test_multiple_tool_calls(self, agent):
        """Agent handles multiple tool calls in one response."""
        tool_response = Response(
            message=Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="tc_1", name="Module-inspect", arguments={"module_path": "mutagent"}),
                    ToolCall(id="tc_2", name="Module-inspect", arguments={"module_path": "mutagent.agent"}),
                ],
            ),
            stop_reason="tool_use",
        )
        final_response = Response(
            message=Message(role="assistant", content="Done."),
            stop_reason="end_turn",
        )

        events_1 = _make_stream_events_for_response(tool_response)
        events_2 = _make_stream_events_for_response(final_response)
        call_idx = 0

        def mock_send(*args, **kwargs):
            nonlocal call_idx
            evts = [events_1, events_2][call_idx]
            call_idx += 1
            for e in evts:
                yield e

        agent.client.send_message = mock_send

        text = _collect_text(agent.run(_single_input("Run two things")))

        assert text == "Done."
        assert len(agent.messages[2].tool_results) == 2

    def test_step_yields_events(self, agent):
        """step() yields StreamEvents from client.send_message."""
        response = Response(
            message=Message(role="assistant", content="Response"),
            stop_reason="end_turn",
        )
        events = _make_stream_events_for_response(response)

        def mock_send(*args, **kwargs):
            for e in events:
                yield e

        agent.client.send_message = mock_send
        agent.messages.append(Message(role="user", content="Test"))

        collected = _collect_events(agent.step())

        assert len(collected) == 2  # text_delta + response_done
        assert collected[0].type == "text_delta"
        assert collected[0].text == "Response"
        assert collected[1].type == "response_done"
        assert collected[1].response is response

    def test_handle_tool_calls_dispatches(self, agent):
        """handle_tool_calls dispatches each call through the tool set."""
        calls = [
            ToolCall(id="tc_1", name="Module-define", arguments={"module_path": "test_dispatch.mod", "source": "x = 42\n"}),
        ]
        results = agent.handle_tool_calls(calls)

        assert len(results) == 1
        assert results[0].tool_call_id == "tc_1"
        assert "OK" in results[0].content


# ---------------------------------------------------------------------------
# Streaming event sequence tests
# ---------------------------------------------------------------------------

class TestStreamingEventSequence:

    @pytest.fixture
    def agent(self):
        agent, mgr = _make_agent()
        yield agent
        mgr.cleanup()

    def test_event_order_simple(self, agent):
        """Simple response yields: text_delta, response_done, turn_done."""
        response = Response(
            message=Message(role="assistant", content="Hello"),
            stop_reason="end_turn",
        )

        def mock_send(*args, **kwargs):
            yield StreamEvent(type="text_delta", text="Hello")
            yield StreamEvent(type="response_done", response=response)

        agent.client.send_message = mock_send

        events = _collect_events(agent.run(_single_input("Hi")))
        types = [e.type for e in events]

        assert types == ["text_delta", "response_done", "turn_done"]

    def test_event_order_with_tool_call(self, agent):
        """Tool call response yields correct event sequence including turn_done."""
        tool_response = Response(
            message=Message(
                role="assistant",
                content="Thinking...",
                tool_calls=[ToolCall(id="tc_1", name="Module-define", arguments={"module_path": "test_evt.mod", "source": "x=1\n"})],
            ),
            stop_reason="tool_use",
        )
        final_response = Response(
            message=Message(role="assistant", content="Done"),
            stop_reason="end_turn",
        )

        call_idx = 0

        def mock_send(*args, **kwargs):
            nonlocal call_idx
            if call_idx == 0:
                call_idx += 1
                yield StreamEvent(type="text_delta", text="Thinking...")
                yield StreamEvent(
                    type="tool_use_start",
                    tool_call=ToolCall(id="tc_1", name="Module-define"),
                )
                yield StreamEvent(type="tool_use_end")
                yield StreamEvent(type="response_done", response=tool_response)
            else:
                yield StreamEvent(type="text_delta", text="Done")
                yield StreamEvent(type="response_done", response=final_response)

        agent.client.send_message = mock_send

        events = _collect_events(agent.run(_single_input("Calc")))
        types = [e.type for e in events]

        assert types == [
            "text_delta",        # "Thinking..."
            "tool_use_start",    # LLM constructs tool call
            "tool_use_end",
            "response_done",     # first LLM call done
            "tool_exec_start",   # Agent executes tool
            "tool_exec_end",     # tool result
            "text_delta",        # "Done"
            "response_done",     # second LLM call done
            "turn_done",         # turn complete
        ]

        # Verify tool_exec events carry correct data
        exec_start = events[4]
        assert exec_start.tool_call.name == "Module-define"
        exec_end = events[5]
        assert exec_end.tool_result is not None
        assert exec_end.tool_result.tool_call_id == "tc_1"

    def test_error_event_stops_turn(self, agent):
        """An error event from LLM stops the current turn but yields turn_done."""
        def mock_send(*args, **kwargs):
            yield StreamEvent(type="error", error="API failed")

        agent.client.send_message = mock_send

        events = _collect_events(agent.run(_single_input("Hi")))
        types = [e.type for e in events]

        assert types == ["error", "turn_done"]
        assert events[0].error == "API failed"
        # Only user message should be in history (no assistant message added)
        assert len(agent.messages) == 1
        assert agent.messages[0].role == "user"

    def test_stream_false_produces_events(self, agent):
        """stream=False still yields events through the same interface."""
        response = Response(
            message=Message(role="assistant", content="Non-streamed"),
            stop_reason="end_turn",
        )

        def mock_send(*args, **kwargs):
            yield StreamEvent(type="text_delta", text="Non-streamed")
            yield StreamEvent(type="response_done", response=response)

        agent.client.send_message = mock_send

        text = _collect_text(agent.run(_single_input("Test"), stream=False))
        assert text == "Non-streamed"


# ---------------------------------------------------------------------------
# Multi-turn and error recovery tests
# ---------------------------------------------------------------------------

class TestMultiTurnAndErrorRecovery:

    @pytest.fixture
    def agent(self):
        agent, mgr = _make_agent()
        yield agent
        mgr.cleanup()

    def test_multi_turn_single_run(self, agent):
        """Multiple InputEvents are processed through a single agent.run() call."""
        response_1 = Response(
            message=Message(role="assistant", content="Hi there"),
            stop_reason="end_turn",
        )
        response_2 = Response(
            message=Message(role="assistant", content="I'm fine"),
            stop_reason="end_turn",
        )

        call_idx = 0

        def mock_send(*args, **kwargs):
            nonlocal call_idx
            if call_idx == 0:
                call_idx += 1
                yield StreamEvent(type="text_delta", text="Hi there")
                yield StreamEvent(type="response_done", response=response_1)
            else:
                yield StreamEvent(type="text_delta", text="I'm fine")
                yield StreamEvent(type="response_done", response=response_2)

        agent.client.send_message = mock_send

        events = _collect_events(
            agent.run(_multi_input("Hello", "How are you?"))
        )
        types = [e.type for e in events]

        # Two turns, each with text_delta + response_done + turn_done
        assert types == [
            "text_delta", "response_done", "turn_done",
            "text_delta", "response_done", "turn_done",
        ]

        # Messages: user1, assistant1, user2, assistant2
        assert len(agent.messages) == 4
        assert agent.messages[0].content == "Hello"
        assert agent.messages[1].content == "Hi there"
        assert agent.messages[2].content == "How are you?"
        assert agent.messages[3].content == "I'm fine"

    def test_error_then_continue(self, agent):
        """After an error in one turn, agent continues processing the next input."""
        response_ok = Response(
            message=Message(role="assistant", content="OK now"),
            stop_reason="end_turn",
        )

        call_idx = 0

        def mock_send(*args, **kwargs):
            nonlocal call_idx
            if call_idx == 0:
                call_idx += 1
                yield StreamEvent(type="error", error="API timeout")
            else:
                yield StreamEvent(type="text_delta", text="OK now")
                yield StreamEvent(type="response_done", response=response_ok)

        agent.client.send_message = mock_send

        events = _collect_events(
            agent.run(_multi_input("First", "Second"))
        )
        types = [e.type for e in events]

        # First turn: error + turn_done. Second turn: text_delta + response_done + turn_done
        assert types == [
            "error", "turn_done",
            "text_delta", "response_done", "turn_done",
        ]

        # Messages: user1 (error, no assistant), user2, assistant2
        assert len(agent.messages) == 3
        assert agent.messages[0].content == "First"
        assert agent.messages[1].content == "Second"
        assert agent.messages[2].content == "OK now"


# ---------------------------------------------------------------------------
# stop_reason vs tool_calls mismatch tests
# ---------------------------------------------------------------------------

class TestStopReasonToolCallsMismatch:
    """Tests for when stop_reason and tool_calls presence disagree."""

    @pytest.fixture
    def agent(self):
        agent, mgr = _make_agent()
        yield agent
        mgr.cleanup()

    def test_end_turn_with_tool_calls_executes_tools(self, agent):
        """When stop_reason=end_turn but tool_calls exist, tools should still be executed."""
        tool_response = Response(
            message=Message(
                role="assistant",
                content="Let me check:",
                tool_calls=[ToolCall(id="tc_1", name="Module-inspect", arguments={})],
            ),
            stop_reason="end_turn",  # mismatch: should be tool_use
        )
        final_response = Response(
            message=Message(role="assistant", content="Here are the results."),
            stop_reason="end_turn",
        )

        call_idx = 0

        def mock_send(*args, **kwargs):
            nonlocal call_idx
            if call_idx == 0:
                call_idx += 1
                for e in _make_stream_events_for_response(tool_response):
                    yield e
            else:
                for e in _make_stream_events_for_response(final_response):
                    yield e

        agent.client.send_message = mock_send

        events = _collect_events(agent.run(_single_input("Check modules")))
        types = [e.type for e in events]

        # Tool should be executed despite stop_reason=end_turn
        assert "tool_exec_start" in types
        assert "tool_exec_end" in types
        # Full message history: user, assistant(tool_call), user(tool_result), assistant(final)
        assert len(agent.messages) == 4
        assert len(agent.messages[2].tool_results) == 1

    def test_end_turn_without_tool_calls_ends_turn(self, agent):
        """When stop_reason=end_turn and no tool_calls, turn ends normally (regression)."""
        response = Response(
            message=Message(role="assistant", content="All done."),
            stop_reason="end_turn",
        )

        def mock_send(*args, **kwargs):
            for e in _make_stream_events_for_response(response):
                yield e

        agent.client.send_message = mock_send

        events = _collect_events(agent.run(_single_input("Hi")))
        types = [e.type for e in events]

        assert types == ["text_delta", "response_done", "turn_done"]
        assert len(agent.messages) == 2

    def test_tool_use_with_tool_calls_still_works(self, agent):
        """When stop_reason=tool_use and tool_calls exist, behavior unchanged (regression)."""
        tool_response = Response(
            message=Message(
                role="assistant",
                content="Inspecting...",
                tool_calls=[ToolCall(id="tc_1", name="Module-inspect", arguments={"module_path": "mutagent"})],
            ),
            stop_reason="tool_use",
        )
        final_response = Response(
            message=Message(role="assistant", content="Done."),
            stop_reason="end_turn",
        )

        call_idx = 0

        def mock_send(*args, **kwargs):
            nonlocal call_idx
            if call_idx == 0:
                call_idx += 1
                for e in _make_stream_events_for_response(tool_response):
                    yield e
            else:
                for e in _make_stream_events_for_response(final_response):
                    yield e

        agent.client.send_message = mock_send

        events = _collect_events(agent.run(_single_input("Inspect")))
        types = [e.type for e in events]

        assert "tool_exec_start" in types
        assert "tool_exec_end" in types
        assert len(agent.messages) == 4


# ---------------------------------------------------------------------------
# max_tool_rounds tests
# ---------------------------------------------------------------------------

class TestMaxToolRounds:
    """Tests for the max_tool_rounds limit on tool call loops."""

    def _make_tool_loop_agent(self, max_rounds, total_tool_responses):
        """Create an agent that will produce `total_tool_responses` consecutive
        tool-calling responses followed by a final text response.

        Returns (agent, mgr) where agent has mock client configured.
        """
        agent, mgr = _make_agent()
        object.__setattr__(agent, 'max_tool_rounds', max_rounds)

        call_idx = 0

        def mock_send(*args, **kwargs):
            nonlocal call_idx
            idx = call_idx
            call_idx += 1
            if idx < total_tool_responses:
                # Tool-calling response
                resp = Response(
                    message=Message(
                        role="assistant",
                        content=f"Round {idx}",
                        tool_calls=[ToolCall(
                            id=f"tc_{idx}",
                            name="Module-inspect",
                            arguments={},
                        )],
                    ),
                    stop_reason="tool_use",
                )
                for e in _make_stream_events_for_response(resp):
                    yield e
            else:
                # Final text response (or summary after limit)
                resp = Response(
                    message=Message(
                        role="assistant",
                        content="Summary of progress.",
                    ),
                    stop_reason="end_turn",
                )
                for e in _make_stream_events_for_response(resp):
                    yield e

        agent.client.send_message = mock_send
        return agent, mgr

    def test_max_tool_rounds_stops_loop(self):
        """Agent stops after max_tool_rounds and requests summary."""
        agent, mgr = self._make_tool_loop_agent(max_rounds=3, total_tool_responses=10)
        try:
            events = _collect_events(agent.run(_single_input("Do work")))
            types = [e.type for e in events]

            # Should have tool_exec events for 3 rounds, then stop
            tool_exec_starts = [e for e in events if e.type == "tool_exec_start"]
            assert len(tool_exec_starts) == 3

            # The 4th tool-calling response triggers the limit: its assistant
            # message is added, then the system injects a summary request
            # and does one final LLM call.

            # Verify the system limit message is in messages
            limit_msgs = [
                m for m in agent.messages
                if m.role == "user" and m.content and "Tool call limit reached" in m.content
            ]
            assert len(limit_msgs) == 1

            # Should still end with turn_done
            assert types[-1] == "turn_done"
        finally:
            mgr.cleanup()

    def test_below_max_tool_rounds_normal(self):
        """Agent completes normally when tool rounds are below limit."""
        agent, mgr = self._make_tool_loop_agent(max_rounds=25, total_tool_responses=3)
        try:
            events = _collect_events(agent.run(_single_input("Small task")))
            types = [e.type for e in events]

            tool_exec_starts = [e for e in events if e.type == "tool_exec_start"]
            assert len(tool_exec_starts) == 3

            # No limit message injected
            limit_msgs = [
                m for m in agent.messages
                if m.role == "user" and m.content and "Tool call limit reached" in m.content
            ]
            assert len(limit_msgs) == 0

            assert types[-1] == "turn_done"
        finally:
            mgr.cleanup()

    def test_custom_max_tool_rounds(self):
        """Custom max_tool_rounds=1 stops after 1 round."""
        agent, mgr = self._make_tool_loop_agent(max_rounds=1, total_tool_responses=5)
        try:
            events = _collect_events(agent.run(_single_input("Quick")))

            tool_exec_starts = [e for e in events if e.type == "tool_exec_start"]
            assert len(tool_exec_starts) == 1

            limit_msgs = [
                m for m in agent.messages
                if m.role == "user" and m.content and "Tool call limit reached" in m.content
            ]
            assert len(limit_msgs) == 1
        finally:
            mgr.cleanup()

    def test_default_max_tool_rounds_behavior(self):
        """Agent without explicit max_tool_rounds uses default of 25."""
        agent, mgr = _make_agent()
        try:
            # max_tool_rounds is not set at construction, impl uses
            # getattr(self, 'max_tool_rounds', 25) as default
            assert getattr(agent, 'max_tool_rounds', 25) == 25
        finally:
            mgr.cleanup()

    def test_explicit_max_tool_rounds(self):
        """Agent with explicit max_tool_rounds stores the value."""
        agent, mgr = _make_agent()
        try:
            object.__setattr__(agent, 'max_tool_rounds', 10)
            assert agent.max_tool_rounds == 10
        finally:
            mgr.cleanup()
