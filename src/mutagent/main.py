"""mutagent.main -- Bootstrap and Main entry point."""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from typing import TYPE_CHECKING

import mutagent
from mutagent.config import Config

if TYPE_CHECKING:
    from mutagent.agent import Agent


class Main(mutagent.Object):
    """Main entry point.  Override via ``@impl`` for custom UI (e.g. TUI).

    Attributes:
        config: The loaded Config object.
        agent: The Agent for this session, set by ``setup_agent()``.
    """

    config: Config
    agent: Agent

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

    Steps 1-5 are the bootstrap phase (plain Python, no @impl dependency).
    Step 6 calls the overridable ``Main.run()``.
    """
    # 1. Load config (plain Python)
    config = Config.load()

    # 2. Set environment variables
    for _config_dir, data in config._layers:
        for key, value in data.get("env", {}).items():
            os.environ[key] = value

    # 3. Extend sys.path (paths already resolved to absolute in Config.load)
    for _config_dir, data in config._layers:
        for p in data.get("path", []):
            if p not in sys.path:
                sys.path.insert(0, p)

    # 4. Load builtins (registers all @impl, including Config and Main)
    import mutagent.builtins  # noqa: F401

    # 5. Load extension modules (may override @impl)
    for _config_dir, data in config._layers:
        for module_name in data.get("modules", []):
            importlib.import_module(module_name)

    # 6. Create Main and run (overridable)
    entry = Main(config=config)
    asyncio.run(entry.run())
