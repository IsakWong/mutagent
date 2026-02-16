"""mutagent.main -- Bootstrap and Main entry point."""

from __future__ import annotations

import importlib
import os
import sys
from typing import TYPE_CHECKING

from pathlib import Path

import mutagent
from mutagent.config import Config

if TYPE_CHECKING:
    from mutagent.agent import Agent
    from mutagent.messages import StreamEvent


class App(mutagent.Declaration):
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

    def input_stream(self):
        """Generator that reads user input from stdin."""
        ...

    def handle_stream_event(self, event: StreamEvent):
        """Handle an output event from the agent.

        Override to control how events are displayed to the user (e.g. TUI,
        Web, etc.).  The default implementation prints to console.

        Args:
            event: The event emitted by the agent.
        """
        ...

    def confirm_exit(self) -> bool:
        """Ask user to confirm exit after an interruption.

        Override to control how exit confirmation is handled (e.g. TUI dialog,
        web prompt, etc.).  The default implementation prompts in console.

        Returns:
            True if the user confirms they want to exit, False to continue.
        """
        ...

    def run(self) -> None:
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
    app.run()
