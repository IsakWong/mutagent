"""mutagent.builtins.save_module -- save_module tool implementation."""

from pathlib import Path

import mutagent
from mutagent.essential_tools import EssentialTools

_LEVEL_DIRS = {
    "project": lambda: Path.cwd() / ".mutagent",
    "user": lambda: Path.home() / ".mutagent",
}


@mutagent.impl(EssentialTools.save_module)
def save_module(self: EssentialTools, module_path: str, level: str = "project") -> str:
    """Persist a memory-defined module to disk."""
    try:
        dir_factory = _LEVEL_DIRS.get(level)
        if dir_factory is None:
            return f"Error: unknown level {level!r}. Use 'project' or 'user'."
        directory = dir_factory()
        path = self.module_manager.save_module(module_path, directory)
        return f"OK: {module_path} saved to {path}"
    except Exception as e:
        return f"Error saving {module_path}: {type(e).__name__}: {e}"
