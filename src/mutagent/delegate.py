"""mutagent.delegate -- DelegateTool declaration."""

from __future__ import annotations

from typing import TYPE_CHECKING

import mutagent

if TYPE_CHECKING:
    from mutagent.agent import Agent


class DelegateTool(mutagent.Declaration):
    """Delegate tool for multi-agent collaboration.

    Holds a set of pre-created Sub-Agent instances. The ``delegate``
    method dispatches a task to a named Sub-Agent, which runs to
    completion and returns its result.

    Each delegate call clears the Sub-Agent's message history first,
    so every call is an independent task.

    Attributes:
        agents: Dict of pre-created Sub-Agent instances keyed by name.
    """

    agents: dict[str, Agent]

    def delegate(self, agent_name: str, task: str) -> str:
        """Delegate a task to a named Sub-Agent.

        Args:
            agent_name: Name of the Sub-Agent to delegate to.
            task: Task description for the Sub-Agent.

        Returns:
            The Sub-Agent's execution result as text.
        """
        return delegate_impl.delegate(self, agent_name, task)


from .builtins import delegate_impl
mutagent.register_module_impls(delegate_impl)
