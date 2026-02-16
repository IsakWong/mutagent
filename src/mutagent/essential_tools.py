"""mutagent.essential_tools -- Essential tool primitives for Agent self-evolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import mutagent

if TYPE_CHECKING:
    from mutagent.runtime.module_manager import ModuleManager


class EssentialTools(mutagent.Declaration):
    """Essential tool primitives -- the minimal operation set for Agent evolution.

    Each method is an independent tool declaration. An Agent can override
    any tool's implementation via ``@impl(EssentialTools.<method>)``,
    or patch this class to add/remove tool methods.

    Attributes:
        module_manager: The ModuleManager instance used for runtime patching.
    """

    module_manager: ModuleManager

    def inspect_module(self, module_path: str = "", depth: int = 2) -> str:
        """Inspect the structure of a Python module.

        Args:
            module_path: Dotted module path (e.g. "mutagent.essential_tools").
                Empty string lists top-level mutagent modules.
            depth: How deep to expand sub-modules/classes. Default 2.

        Returns:
            A formatted string showing the module structure.
        """
        return inspect_module_impl.inspect_module(self, module_path=module_path, depth=depth)

    def view_source(self, target: str) -> str:
        """View the source code of a module, class, or function.

        Args:
            target: Dotted path to the target (e.g. "mutagent.agent.Agent").

        Returns:
            The source code as a string.
        """
        return view_source_impl.view_source(self, target)

    def define_module(self, module_path: str, source: str) -> str:
        """Define or redefine a Python module in memory.

        Injects module code at runtime. The module takes effect immediately
        in memory but is NOT automatically persisted to disk. Use save_module
        to persist validated modules.

        Args:
            module_path: Dotted module path to create or redefine.
                Use functional names (e.g. "utils.helpers"), not "mutagent.xxx".
            source: Python source code for the module.

        Returns:
            A status message indicating success.
        """
        return define_module_impl.define_module(self, module_path, source)

    def save_module(self, module_path: str, level: str = "project") -> str:
        """Persist a memory-defined module to disk.

        Args:
            module_path: Dotted module path to save.
            level: Save level.
                "project" (default): save to ./.mutagent/
                "user": save to ~/.mutagent/

        Returns:
            A status message with the written file path.
        """
        return save_module_impl.save_module(self, module_path, level)


from mutagent.builtins  import (
    inspect_module_impl, view_source_impl, define_module_impl, save_module_impl
)
mutagent.register_module_impls(
    inspect_module_impl, view_source_impl, define_module_impl, save_module_impl
)