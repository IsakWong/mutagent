"""mutagent.agent -- Agent declaration."""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Callable

import mutagent

if TYPE_CHECKING:
    from mutagent.client import LLMClient
    from mutagent.config import Config
    from mutagent.context import AgentContext
    from mutagent.messages import Message, StreamEvent, ToolUseBlock
    from mutagent.tools import ToolSet


class Agent(mutagent.Declaration):
    """Agent manages the conversation loop with an LLM.

    The agent sends messages to the LLM, handles tool calls by dispatching
    them through the ToolSet, and continues until the LLM signals
    end_turn.

    Attributes:
        llm: The LLM client for sending messages.
        tools: The tool set for tool management and dispatch.
        context: Agent context managing prompts, messages, and token tracking.
        config: The shared configuration instance.
    """

    llm: LLMClient
    tools: ToolSet
    context: AgentContext
    config: Config

    async def run(
        self,
        input_stream: AsyncIterator[Message],
        stream: bool = True,
        check_pending: Callable[[], bool] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Run the agent conversation loop, consuming input messages and yielding output events.

        Args:
            input_stream: AsyncIterator of user input Messages. Messages containing
                a TurnStartBlock trigger agent processing; others are stored only.
            stream: Whether to use SSE streaming for the HTTP request.
            check_pending: Optional callback that returns True if new input
                is available.

        Yields:
            StreamEvent instances for each piece of incremental output.
        """
        return agent_impl.run(self, input_stream, stream=stream, check_pending=check_pending)

    async def step(self, stream: bool = True) -> AsyncIterator[StreamEvent]:
        """Execute a single LLM call, yielding streaming events."""
        return agent_impl.step(self, stream=stream)

    async def handle_tool_calls(self, tool_calls: list[ToolUseBlock]) -> None:
        """Execute tool calls, updating each ToolUseBlock in-place.

        Args:
            tool_calls: List of ToolUseBlock from the LLM response.
        """
        return await agent_impl.handle_tool_calls(self, tool_calls)


from .builtins import agent_impl
mutagent.register_module_impls(agent_impl)
