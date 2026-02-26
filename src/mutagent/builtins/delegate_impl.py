"""mutagent.builtins.delegate -- AgentToolkit.delegate implementation."""

import logging

import mutagent
from mutagent.toolkits.agent_toolkit import AgentToolkit
from mutagent.messages import InputEvent, StreamEvent

logger = logging.getLogger(__name__)


@mutagent.impl(AgentToolkit.delegate)
async def delegate(self: AgentToolkit, agent_name: str, task: str) -> str:
    """Delegate a task to a named Sub-Agent (async)."""
    agent = self.agents.get(agent_name)
    if agent is None:
        available = list(self.agents.keys())
        return f"Unknown agent: {agent_name}. Available: {available}"

    logger.info("Delegating to sub-agent '%s': %.100s", agent_name, task)

    # Clear message history (each call is independent)
    agent.messages.clear()

    # Build async input stream with the task
    async def input_stream():
        yield InputEvent(type="user_message", text=task)

    # Run sub-agent and collect result
    text_parts = []
    async for event in agent.run(input_stream()):
        if event.type == "text_delta":
            text_parts.append(event.text)

    result = "".join(text_parts)
    logger.info("Sub-agent '%s' completed (%d chars)", agent_name, len(result))
    return result
