"""mutagent.builtins.save_module -- save_module tool implementation."""

import logging
from pathlib import Path

import mutagent
from mutagent.toolkits.module_toolkit import ModuleToolkit

logger = logging.getLogger(__name__)

_LEVEL_DIRS = {
    "project": lambda: Path.cwd() / ".mutagent",
    "user": lambda: Path.home() / ".mutagent",
}


@mutagent.impl(ModuleToolkit.save_module)
def save_module(self: ModuleToolkit, module_path: str, level: str = "project") -> str:
    """Persist a memory-defined module to disk."""
    try:
        dir_factory = _LEVEL_DIRS.get(level)
        if dir_factory is None:
            return f"Error: unknown level {level!r}. Use 'project' or 'user'."
        directory = dir_factory()
        path = self.module_manager.save_module(module_path, directory)
        logger.info("Module %s saved to %s", module_path, path)
        return f"OK: {module_path} saved to {path}"
    except Exception as e:
        logger.error("Failed to save %s: %s: %s", module_path, type(e).__name__, e)
        return f"Error saving {module_path}: {type(e).__name__}: {e}"
