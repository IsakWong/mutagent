"""mutagent.main -- Bootstrap and Main entry point."""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from typing import TYPE_CHECKING

from pathlib import Path

import mutagent
from mutagent.config import Config

if TYPE_CHECKING:
    from mutagent.agent import Agent


class App(mutagent.Object):
    """App entry point.  Override via ``@impl`` for custom UI (e.g. TUI).

    Attributes:
        config: The loaded Config object.
        agent: The Agent for this session, set by ``setup_agent()``.
    """

    config: Config
    agent: Agent

    def load_config(self, config_path):
        """Load configuration from the given path and store in ``self.config``.

        Override if you want to control config loading (e.g. different path,
        different format, etc.).  The default implementation uses
        ``Config.load()`` which scans standard locations.

        Args:
            config_path: Path to the config file (not used by default).
        """
        ...

    def setup_agent(self, system_prompt: str = "") -> Agent:
        """Initialise the session Agent and store it in ``self.agent``.

        Override to customise component assembly (different tools,
        different LLMClient, etc.).

        Args:
            system_prompt: System prompt for the agent.

        Returns:
            The created Agent instance (also stored as ``self.agent``).
        """
        ...

    async def run(self) -> None:
        """Run the agent session loop.

        The default implementation calls ``setup_agent()`` then enters
        a terminal REPL.  Override for TUI, Web, or other interfaces.
        """
        ...


def main() -> None:
    """Bootstrap mutagent.  Not overridable.
    """
    # 1. Load built-in implementations (and any auto-discovered ones)
    from . import builtins
    builtins.load()

    # 2. Create app
    app = App()
    app.load_config(".mutagent/config.json")

    # 3. Run app
    asyncio.run(app.run())
