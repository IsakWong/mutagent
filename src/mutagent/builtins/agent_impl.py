"""mutagent.builtins.agent -- Agent main loop implementation."""

from typing import Iterator

import mutagent
from mutagent.agent import Agent
from mutagent.messages import InputEvent, Message, StreamEvent, ToolCall, ToolResult


@mutagent.impl(Agent.run)
def run(
    self: Agent, input_stream: Iterator[InputEvent], stream: bool = True
) -> Iterator[StreamEvent]:
    """Run the agent conversation loop, consuming input events and yielding output events."""
    for input_event in input_stream:
        if input_event.type == "user_message":
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

                if response.stop_reason == "tool_use" and response.message.tool_calls:
                    # Handle tool calls, yielding execution events
                    results = []
                    for call in response.message.tool_calls:
                        yield StreamEvent(type="tool_exec_start", tool_call=call)
                        result = self.tool_selector.dispatch(call)
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
    tools = self.tool_selector.get_tools({})
    yield from self.client.send_message(
        self.messages, tools, system_prompt=self.system_prompt, stream=stream,
    )


@mutagent.impl(Agent.handle_tool_calls)
def handle_tool_calls(
    self: Agent, tool_calls: list[ToolCall]
) -> list[ToolResult]:
    """Dispatch tool calls through the selector."""
    results = []
    for call in tool_calls:
        result = self.tool_selector.dispatch(call)
        results.append(result)
    return results
