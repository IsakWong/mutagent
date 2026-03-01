"""mutagent.builtins.agent -- Agent main loop implementation."""

import asyncio
import logging
import time
from typing import AsyncIterator, Callable

import mutagent
from mutagent.agent import Agent
from mutagent.messages import InputEvent, Message, StreamEvent, TextBlock, ToolUseBlock
from mutagent.runtime.log_store import _tool_log_buffer

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 25


def _get_tool_calls(msg: Message) -> list[ToolUseBlock]:
    """从 Message.blocks 中提取 ToolUseBlock 列表。"""
    return [b for b in msg.blocks if isinstance(b, ToolUseBlock)]


def _get_tool_capture_enabled(agent: Agent) -> bool:
    """Check if tool log capture is enabled via any registered tool source's LogStore."""
    entries = getattr(agent.tools, '_entries', None)
    if not entries:
        return False
    for entry in entries.values():
        log_store = getattr(entry.source, "log_store", None)
        if log_store is not None:
            return log_store.tool_capture_enabled
    return False


@mutagent.impl(Agent.run)
async def run(
    self: Agent,
    input_stream: AsyncIterator[InputEvent],
    stream: bool = True,
    check_pending: Callable[[], bool] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Run the agent conversation loop, consuming input events and yielding output events."""
    async for input_event in input_stream:
        if input_event.type == "user_message":
            logger.info("User message received (%d chars)", len(input_event.text))
            if not input_event.data.get("hidden"):
                self.context.messages.append(
                    Message(role="user", blocks=[TextBlock(text=input_event.text)])
                )

            tool_round = 0

            while True:
                response = None
                got_error = False
                async for event in self.step(stream=stream):
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

                # Update token usage
                self.context.update_usage(response.usage)

                # Add assistant message to history
                self.context.messages.append(response.message)
                tool_calls = _get_tool_calls(response.message)
                logger.info("LLM stop_reason=%s, tool_calls=%d",
                            response.stop_reason, len(tool_calls))

                if tool_calls:
                    if tool_round >= MAX_TOOL_ROUNDS:
                        logger.warning(
                            "Tool call limit reached (%d rounds). "
                            "Injecting summary request.", MAX_TOOL_ROUNDS,
                        )
                        self.context.messages.append(Message(
                            role="user",
                            blocks=[TextBlock(
                                text="[System] Tool call limit reached. "
                                     "Summarize your progress and what remains to be done.",
                            )],
                        ))
                        async for event in self.step(stream=stream):
                            yield event
                            if event.type == "response_done" and event.response:
                                self.context.update_usage(event.response.usage)
                                self.context.messages.append(event.response.message)
                        break

                    tool_round += 1

                    if response.stop_reason != "tool_use":
                        logger.warning(
                            "stop_reason=%s but %d tool_calls found in response, "
                            "executing tools anyway",
                            response.stop_reason, len(tool_calls),
                        )

                    # Execute tool calls
                    capture = _get_tool_capture_enabled(self)
                    for block in tool_calls:
                        logger.info("Executing tool: %s", block.name)
                        args_str = str(block.input)
                        if len(args_str) > 200:
                            args_str = args_str[:200] + f"...({len(args_str)} chars total)"
                        logger.debug("Tool args: %s", args_str)

                        block.status = "running"
                        yield StreamEvent(type="tool_exec_start", tool_call=block)

                        t0 = time.monotonic()
                        if capture:
                            buf: list[str] = []
                            token = _tool_log_buffer.set(buf)
                            try:
                                await self.tools.dispatch(block)
                            finally:
                                _tool_log_buffer.reset(token)
                            if buf:
                                block.result += "\n\n[Tool Logs]\n" + "\n".join(buf)
                        else:
                            await self.tools.dispatch(block)

                        block.duration = time.monotonic() - t0

                        logger.info("Tool %s result: %s (%d chars)",
                                    block.name,
                                    "error" if block.is_error else "ok",
                                    len(block.result))
                        logger.debug("Tool result content: %.200s", block.result)
                        yield StreamEvent(type="tool_exec_end", tool_call=block)

                    # Natural checkpoint: check for pending user input
                    if check_pending and check_pending():
                        logger.info("Pending input detected at tool round checkpoint, ending turn early")
                        break
                else:
                    break

            yield StreamEvent(type="turn_done")


@mutagent.impl(Agent.step)
async def step(
    self: Agent, stream: bool = True
) -> AsyncIterator[StreamEvent]:
    """Execute a single LLM call, yielding streaming events."""
    tools = self.tools.get_tools()
    prompts = self.context.prepare_prompts()
    messages = self.context.prepare_messages()
    async for event in self.llm.send_message(
        messages, tools, prompts=prompts, stream=stream,
    ):
        yield event


@mutagent.impl(Agent.handle_tool_calls)
async def handle_tool_calls(
    self: Agent, tool_calls: list[ToolUseBlock]
) -> None:
    """Dispatch tool calls through the tool set, updating blocks in-place."""
    for block in tool_calls:
        block.status = "running"
        await self.tools.dispatch(block)
