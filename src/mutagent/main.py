"""mutagent.main -- Bootstrap and Main entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING

import mutagent
from mutagent.config import Config

if TYPE_CHECKING:
    from mutagent.agent import Agent
    from mutagent.userio import UserIO


class App(mutagent.Declaration):
    """App entry point.  Override via ``@impl`` for custom UI (e.g. TUI).

    Interaction with the user (input collection, output rendering) is
    delegated to the ``userio`` attribute, a :class:`UserIO` instance.

    Attributes:
        config: The loaded Config object.
        agent: The Agent for this session, set by ``setup_agent()``.
        userio: The UserIO instance handling user interaction.
    """

    config: Config
    agent: Agent
    userio: UserIO

    def load_config(self, config_path):
        """Load configuration from the given path and store in ``self.config``.

        Override if you want to control config loading (e.g. different path,
        different format, etc.).  The default implementation uses
        ``Config.load()`` which scans standard locations.

        Args:
            config_path: Path to the config file (not used by default).
        """
        return main_impl.load_config(self, config_path)

    def setup_agent(self, system_prompt: str = "") -> Agent:
        """Initialise the session Agent and store it in ``self.agent``.

        Also creates the UserIO instance and stores it in ``self.userio``.
        Override to customise component assembly (different tools,
        different LLMClient, etc.).

        Args:
            system_prompt: System prompt for the agent.

        Returns:
            The created Agent instance (also stored as ``self.agent``).
        """
        return main_impl.setup_agent(self, system_prompt=system_prompt)

    def run(self) -> None:
        """Run the agent session loop.

        The default implementation calls ``setup_agent()`` then enters
        a terminal REPL.  Override for TUI, Web, or other interfaces.
        """
        return main_impl.run(self)


def main() -> None:
    """Bootstrap mutagent.  Not overridable.
    """
    app = App()
    app.load_config([
        "~/.mutagent/config.json",
        ".mutagent/config.json",
    ])
    app.run()


from .builtins import main_impl
mutagent.register_module_impls(main_impl)
