"""mutagent.builtins.context_impl -- AgentContext default implementation."""

import mutagent
from mutagent.context import AgentContext
from mutagent.messages import Message


@mutagent.impl(AgentContext.prepare_prompts)
def prepare_prompts(self: AgentContext) -> list[Message]:
    """按 priority 降序排列 prompts。"""
    return sorted(self.prompts, key=lambda m: m.priority, reverse=True)


@mutagent.impl(AgentContext.prepare_messages)
def prepare_messages(self: AgentContext) -> list[Message]:
    """默认直接返回 messages。"""
    return list(self.messages)


@mutagent.impl(AgentContext.update_usage)
def update_usage(self: AgentContext, usage: dict[str, int]) -> None:
    """累加 token 用量。"""
    total = getattr(self, '_total_input_tokens', 0)
    total += usage.get('input_tokens', 0)
    object.__setattr__(self, '_total_input_tokens', total)

    total_out = getattr(self, '_total_output_tokens', 0)
    total_out += usage.get('output_tokens', 0)
    object.__setattr__(self, '_total_output_tokens', total_out)


@mutagent.impl(AgentContext.get_context_used)
def get_context_used(self: AgentContext) -> int:
    """返回最近一次 LLM 调用的 input_tokens（近似 context 用量）。"""
    return getattr(self, '_total_input_tokens', 0)


@mutagent.impl(AgentContext.get_context_percent)
def get_context_percent(self: AgentContext) -> float | None:
    """返回 context 使用百分比。context_window=0 时返回 None。"""
    if not self.context_window:
        return None
    used = get_context_used(self)
    return used / self.context_window
