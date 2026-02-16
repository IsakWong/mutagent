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

    def patch_module(self, module_path: str, source: str) -> str:
        """Patch a module with new source code at runtime.

        Args:
            module_path: Dotted module path to patch or create.
            source: Python source code for the module.

        Returns:
            A status message indicating success.
        """
        return patch_module_impl.patch_module(self, module_path, source)

    def save_module(self, module_path: str, file_path: str = "") -> str:
        """Persist a patched module to the filesystem.

        Args:
            module_path: Dotted module path to save.
            file_path: Optional target directory. Auto-derived if empty.

        Returns:
            A status message with the written file path.
        """
        return save_module_impl.save_module(self, module_path, file_path)

    def run_code(self, code: str) -> str:
        """Execute a Python code snippet and return the result.

        Args:
            code: Python code to execute.

        Returns:
            Captured stdout/stderr and return value, or error traceback.
        """
        return run_code_impl.run_code(self, code)


from mutagent.builtins  import (
    inspect_module_impl, view_source_impl, patch_module_impl, save_module_impl, run_code_impl
)
mutagent.register_module_impls(
    inspect_module_impl, view_source_impl, patch_module_impl, save_module_impl, run_code_impl
)