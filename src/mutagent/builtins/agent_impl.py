"""mutagent.builtins.agent -- Agent main loop implementation."""

import logging
from typing import Iterator

import mutagent
from mutagent.agent import Agent
from mutagent.messages import InputEvent, Message, StreamEvent, ToolCall, ToolResult
from mutagent.runtime.log_store import _tool_log_buffer
from mutagent.tool_set import ToolEntry

logger = logging.getLogger(__name__)


def _get_tool_capture_enabled(agent: Agent) -> bool:
    """Check if tool log capture is enabled via any registered tool source's LogStore."""
    entries = getattr(agent.tool_set, '_entries', None)
    if not entries:
        return False
    for entry in entries.values():
        log_store = getattr(entry.source, "log_store", None)
        if log_store is not None:
            return log_store.tool_capture_enabled
    return False


@mutagent.impl(Agent.run)
def run(
    self: Agent, input_stream: Iterator[InputEvent], stream: bool = True
) -> Iterator[StreamEvent]:
    """Run the agent conversation loop, consuming input events and yielding output events."""
    for input_event in input_stream:
        if input_event.type == "user_message":
            logger.info("User message received (%d chars)", len(input_event.text))
            self.messages.append(Message(role="user", content=input_event.text))

            while True:
                response = None
                got_error = False
                for event in self.step(stream=stream):
                    yield event
                    if event.type == "response_done":
                        response = event.response
                    elif event.type == "error":
                        got_error = True
                        break

                if got_error:
                    break

                if response is None:
                    yield StreamEvent(
                        type="error",
                        error="No response_done event received from LLM",
                    )
                    break

                # Add assistant message to history
                self.messages.append(response.message)
                logger.info("LLM stop_reason=%s, tool_calls=%d",
                            response.stop_reason, len(response.message.tool_calls))

                if response.message.tool_calls:
                    if response.stop_reason != "tool_use":
                        logger.warning(
                            "stop_reason=%s but %d tool_calls found in response, "
                            "executing tools anyway",
                            response.stop_reason, len(response.message.tool_calls),
                        )
                    # Handle tool calls, yielding execution events
                    results = []
                    capture = _get_tool_capture_enabled(self)
                    for call in response.message.tool_calls:
                        logger.info("Executing tool: %s", call.name)
                        args_str = str(call.arguments)
                        if len(args_str) > 200:
                            args_str = args_str[:200] + f"...({len(args_str)} chars total)"
                        logger.debug("Tool args: %s", args_str)
                        yield StreamEvent(type="tool_exec_start", tool_call=call)

                        if capture:
                            buf: list[str] = []
                            token = _tool_log_buffer.set(buf)
                            try:
                                result = self.tool_set.dispatch(call)
                            finally:
                                _tool_log_buffer.reset(token)
                            if buf:
                                result = ToolResult(
                                    tool_call_id=result.tool_call_id,
                                    content=result.content + "\n\n[Tool Logs]\n" + "\n".join(buf),
                                    is_error=result.is_error,
                                )
                        else:
                            result = self.tool_set.dispatch(call)

                        logger.info("Tool %s result: %s (%d chars)",
                                    call.name,
                                    "error" if result.is_error else "ok",
                                    len(result.content))
                        logger.debug("Tool result content: %.200s", result.content)
                        yield StreamEvent(
                            type="tool_exec_end", tool_call=call, tool_result=result
                        )
                        results.append(result)
                    # Add tool results as a user message
                    self.messages.append(Message(role="user", tool_results=results))
                else:
                    # end_turn or no tool calls — done with this turn
                    break

            yield StreamEvent(type="turn_done")


@mutagent.impl(Agent.step)
def step(
    self: Agent, stream: bool = True
) -> Iterator[StreamEvent]:
    """Execute a single LLM call, yielding streaming events."""
    tools = self.tool_set.get_tools()
    yield from self.client.send_message(
        self.messages, tools, system_prompt=self.system_prompt, stream=stream,
    )


@mutagent.impl(Agent.handle_tool_calls)
def handle_tool_calls(
    self: Agent, tool_calls: list[ToolCall]
) -> list[ToolResult]:
    """Dispatch tool calls through the tool set."""
    results = []
    for call in tool_calls:
        result = self.tool_set.dispatch(call)
        results.append(result)
    return results
