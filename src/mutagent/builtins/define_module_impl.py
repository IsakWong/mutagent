"""mutagent.builtins.define_module -- define_module tool implementation."""

import mutagent
from mutagent.essential_tools import EssentialTools


@mutagent.impl(EssentialTools.define_module)
def define_module(self: EssentialTools, module_path: str, source: str) -> str:
    """Define or redefine a Python module in memory."""
    try:
        self.module_manager.patch_module(module_path, source)
        version = self.module_manager.get_version(module_path)
        return f"OK: {module_path} defined (v{version})"
    except Exception as e:
        return f"Error defining {module_path}: {type(e).__name__}: {e}"
