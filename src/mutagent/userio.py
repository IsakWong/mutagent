"""mutagent.userio -- UserIO and BlockHandler declarations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import mutagent
from mutagent import field

if TYPE_CHECKING:
    from mutagent.messages import Content, StreamEvent


class BlockHandler(mutagent.Declaration):
    """Base class for block type handlers.

    Each BlockHandler subclass handles a specific mutagent: block type,
    responsible for parsing, streaming rendering, and interaction behavior.

    BlockHandler subclasses are automatically discovered and registered
    by UserIO, similar to the Toolkit auto-discovery mechanism.

    Subclasses should set ``block_type`` to the block type string they handle
    (e.g. "tasks", "status", "code").

    Attributes:
        block_type: The block type this handler processes (e.g. "tasks").
    """

    block_type: str

    def on_start(self, metadata: dict) -> None:
        """Called when a block of this type starts.

        Args:
            metadata: Block metadata parsed from the opening fence line.
        """
        return userio_impl.block_handler_on_start(self, metadata)

    def on_line(self, text: str) -> None:
        """Called for each line of content inside the block (streaming).

        Args:
            text: One line of block content.
        """
        return userio_impl.block_handler_on_line(self, text)

    def on_end(self) -> None:
        """Called when the block ends (closing fence detected)."""
        return userio_impl.block_handler_on_end(self)

    def render(self, content: Content) -> None:
        """Render a complete Content object (present() path).

        Args:
            content: The complete content block to render.
        """
        return userio_impl.block_handler_render(self, content)


class UserIO(mutagent.Declaration):
    """User interaction layer abstraction.

    UserIO is responsible for rendering Agent output and collecting user input.
    App holds a UserIO instance and delegates interaction responsibilities to it.

    The render_event method processes the LLM text stream, detecting mutagent:
    prefixed fenced code blocks and delegating to the appropriate BlockHandler.

    The present method handles non-LLM output (system events, tool side-effects,
    Sub-Agent output) by accepting complete Content objects.

    Attributes:
        block_handlers: Registry mapping block type strings to BlockHandler instances.
    """

    block_handlers: dict = field(default_factory=dict)

    def render_event(self, event: StreamEvent) -> None:
        """Render a streaming event from the Agent.

        This is the main rendering entry point for LLM text output. It acts as
        a streaming parser + router: plain text is rendered directly, while
        mutagent: prefixed fenced code blocks are detected and delegated to
        the corresponding BlockHandler.

        Args:
            event: A StreamEvent from the Agent's output stream.
        """
        return userio_impl.render_event(self, event)

    def present(self, content: Content) -> None:
        """Render a complete Content block (non-LLM bypass output).

        Used for system-level output from non-LLM sources: Agent state changes,
        tool side-effects, Sub-Agent output, etc.

        Args:
            content: The structured content block to render.
        """
        return userio_impl.present(self, content)

    def read_input(self) -> str:
        """Read a line of user input.

        Returns:
            The user's input text, stripped of leading/trailing whitespace.
        """
        return userio_impl.read_input(self)

    def confirm_exit(self) -> bool:
        """Ask the user to confirm exit.

        Returns:
            True if the user wants to exit, False to continue.
        """
        return userio_impl.confirm_exit(self)

    def input_stream(self):
        """Generator that yields Message objects from user input.

        Yields:
            Message instances for each user message (with TurnStartBlock).
        """
        return userio_impl.input_stream(self)


from .builtins import userio_impl
mutagent.register_module_impls(userio_impl)
