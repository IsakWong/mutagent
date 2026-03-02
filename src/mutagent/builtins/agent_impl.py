"""mutagent.builtins.agent -- Agent main loop implementation."""

import logging
import time
from typing import AsyncIterator, Callable
from uuid import uuid4

import mutagent
from mutagent.agent import Agent
from mutagent.messages import (
    Message, Response, StreamEvent, TextBlock, ToolUseBlock,
    TurnEndBlock, TurnStartBlock,
)
from mutagent.runtime.log_store import _tool_log_buffer

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 25


def _gen_id() -> str:
    """生成短 ID。"""
    return uuid4().hex[:12]


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
    input_stream: AsyncIterator[Message],
    stream: bool = True,
    check_pending: Callable[[], bool] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Run the agent conversation loop, consuming input messages and yielding output events."""
    _partial_text: list[str] = []
    try:
        async for msg in input_stream:
            self.context.messages.append(msg)

            # 提取 TurnStartBlock — 有则触发处理，无则只存储
            turn_start = None
            for b in msg.blocks:
                if isinstance(b, TurnStartBlock):
                    turn_start = b
                    break
            if turn_start is None:
                continue

            turn_id = turn_start.turn_id or _gen_id()
            turn_start_time = time.monotonic()

            # 计算用户消息文本长度用于日志
            text_len = sum(len(b.text) for b in msg.blocks if isinstance(b, TextBlock))
            logger.info("User message received (%d chars)", text_len)

            tool_round = 0
            _partial_text.clear()

            while True:
                # --- response_start ---
                msg_id = _gen_id()
                response_start_ts = time.time()
                model = getattr(self.llm, "model", "")

                yield StreamEvent(
                    type="response_start",
                    response=Response(
                        message=Message(
                            role="assistant",
                            id=msg_id,
                            model=model,
                            timestamp=response_start_ts,
                        ),
                    ),
                )

                # --- LLM step ---
                response = None
                got_error = False
                async for event in self.step(stream=stream):
                    if event.type == "text_delta" and event.text:
                        _partial_text.append(event.text)
                    yield event
                    if event.type == "response_done":
                        response = event.response
                    elif event.type == "error":
                        got_error = True
                        break

                if got_error:
                    _partial_text.clear()
                    break

                if response is None:
                    _partial_text.clear()
                    yield StreamEvent(
                        type="error",
                        error="No response_done event received from LLM",
                    )
                    break

                # --- 设置 assistant Message 元数据 ---
                response.message.id = msg_id
                response.message.timestamp = response_start_ts
                response.message.model = model
                response.message.duration = time.time() - response_start_ts
                response.message.input_tokens = response.usage.get("input_tokens", 0)
                response.message.output_tokens = response.usage.get("output_tokens", 0)

                # Update token usage
                self.context.update_usage(response.usage)

                # Add assistant message to history
                self.context.messages.append(response.message)
                _partial_text.clear()  # 已提交到 message，清空

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
                        yield StreamEvent(type="tool_exec_start", tool_call=block, timestamp=time.time())

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
                        yield StreamEvent(type="tool_exec_end", tool_call=block, timestamp=time.time())

                    # Natural checkpoint: check for pending user input
                    if check_pending and check_pending():
                        logger.info("Pending input detected at tool round checkpoint, ending turn early")
                        break
                else:
                    break

            # --- Turn 结束 ---
            turn_duration = time.monotonic() - turn_start_time

            # 追加 TurnEndBlock 到最后一条 assistant Message
            if self.context.messages and self.context.messages[-1].role == "assistant":
                self.context.messages[-1].blocks.append(
                    TurnEndBlock(turn_id=turn_id, duration=turn_duration)
                )

            yield StreamEvent(type="turn_done", turn_id=turn_id)

    finally:
        # --- 中断清理 ---
        # 提交部分文本（正常退出时 _partial_text 为空，no-op）
        if _partial_text:
            self.context.messages.append(Message(
                role="assistant",
                blocks=[TextBlock(text="".join(_partial_text) + "\n\n[interrupted]")],
            ))
        # 标记未完成 ToolUseBlock（正常退出时全部 done，no-op）
        if self.context.messages and self.context.messages[-1].role == "assistant":
            for b in self.context.messages[-1].blocks:
                if isinstance(b, ToolUseBlock) and b.status != "done":
                    b.status = "done"
                    b.result = "[interrupted]"
                    b.is_error = True


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
