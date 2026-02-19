"""mutagent.agent -- Agent declaration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

import mutagent

if TYPE_CHECKING:
    from mutagent.client import LLMClient
    from mutagent.messages import InputEvent, StreamEvent, ToolCall, ToolResult
    from mutagent.tool_set import ToolSet


class Agent(mutagent.Declaration):
    """Agent manages the conversation loop with an LLM.

    The agent sends messages to the LLM, handles tool calls by dispatching
    them through the ToolSet, and continues until the LLM signals
    end_turn.

    Attributes:
        client: The LLM client for sending messages.
        tool_set: The tool set for tool management and dispatch.
        system_prompt: System prompt for the LLM.
        messages: Conversation history.
        max_tool_rounds: Maximum number of tool call rounds per user
            message before forcing the agent to stop and summarize.
            Default 25.
    """

    client: LLMClient
    tool_set: ToolSet
    system_prompt: str
    messages: list
    max_tool_rounds: int

    def run(
        self, input_stream: Iterator[InputEvent], stream: bool = True
    ) -> Iterator[StreamEvent]:
        """Run the agent conversation loop, consuming input events and yielding output events.

        This is the main entry point. It consumes InputEvents from input_stream,
        processes each through the LLM (with tool call loops), and yields
        StreamEvents for each piece of incremental output.

        The generator runs until input_stream is exhausted.

        Args:
            input_stream: Iterator of user input events.
            stream: Whether to use SSE streaming for the HTTP request.

        Yields:
            StreamEvent instances for each piece of incremental output.
            A "turn_done" event is yielded after each user message is fully processed.
        """
        return agent_impl.run(self, input_stream, stream=stream)

    def step(self, stream: bool = True) -> Iterator[StreamEvent]:
        """Execute a single LLM call, yielding streaming events.

        Args:
            stream: Whether to use SSE streaming for the HTTP request.

        Yields:
            StreamEvent instances from the LLM client.
        """
        return agent_impl.step(self, stream=stream)

    def handle_tool_calls(self, tool_calls: list[ToolCall]) -> list[ToolResult]:
        """Execute tool calls and return results.

        Args:
            tool_calls: List of tool calls from the LLM.

        Returns:
            List of tool results.
        """
        return agent_impl.handle_tool_calls(self, tool_calls)


from .builtins import agent_impl
mutagent.register_module_impls(agent_impl)