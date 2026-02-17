"""mutagent.builtins.delegate -- DelegateTool implementation."""

import logging

import mutagent
from mutagent.delegate import DelegateTool
from mutagent.messages import InputEvent, StreamEvent

logger = logging.getLogger(__name__)


@mutagent.impl(DelegateTool.delegate)
def delegate(self: DelegateTool, agent_name: str, task: str) -> str:
    """Delegate a task to a named Sub-Agent (synchronous blocking)."""
    agent = self.agents.get(agent_name)
    if agent is None:
        available = list(self.agents.keys())
        return f"Unknown agent: {agent_name}. Available: {available}"

    logger.info("Delegating to sub-agent '%s': %.100s", agent_name, task)

    # Clear message history (each call is independent)
    agent.messages.clear()

    # Build input stream with the task
    def input_stream():
        yield InputEvent(type="user_message", text=task)

    # Run sub-agent and collect result
    text_parts = []
    for event in agent.run(input_stream()):
        if event.type == "text_delta":
            text_parts.append(event.text)

    result = "".join(text_parts)
    logger.info("Sub-agent '%s' completed (%d chars)", agent_name, len(result))
    return result
