"""mutagent.agent -- Agent declaration."""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

import mutagent

if TYPE_CHECKING:
    from mutagent.client import LLMClient
    from mutagent.messages import InputEvent, StreamEvent, ToolCall, ToolResult
    from mutagent.selector import ToolSelector


class Agent(mutagent.Object):
    """Agent manages the conversation loop with an LLM.

    The agent sends messages to the LLM, handles tool calls by dispatching
    them through the ToolSelector, and continues until the LLM signals
    end_turn.

    Attributes:
        client: The LLM client for sending messages.
        tool_selector: The tool selector for tool discovery and dispatch.
        system_prompt: System prompt for the LLM.
        messages: Conversation history.
    """

    client: LLMClient
    tool_selector: ToolSelector
    system_prompt: str
    messages: list

    async def run(
        self, input_stream: AsyncIterator[InputEvent], stream: bool = True
    ) -> AsyncIterator[StreamEvent]:
        """Run the agent conversation loop, consuming input events and yielding output events.

        This is the main entry point. It consumes InputEvents from input_stream,
        processes each through the LLM (with tool call loops), and yields
        StreamEvents for each piece of incremental output.

        The generator runs until input_stream is exhausted.

        Args:
            input_stream: Async iterator of user input events.
            stream: Whether to use SSE streaming for the HTTP request.

        Yields:
            StreamEvent instances for each piece of incremental output.
            A "turn_done" event is yielded after each user message is fully processed.
        """
        ...

    async def step(self, stream: bool = True) -> AsyncIterator[StreamEvent]:
        """Execute a single LLM call, yielding streaming events.

        Args:
            stream: Whether to use SSE streaming for the HTTP request.

        Yields:
            StreamEvent instances from the LLM client.
        """
        ...

    async def handle_tool_calls(self, tool_calls: list[ToolCall]) -> list[ToolResult]:
        """Execute tool calls and return results.

        Args:
            tool_calls: List of tool calls from the LLM.

        Returns:
            List of tool results.
        """
        ...
