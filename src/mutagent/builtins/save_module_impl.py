"""mutagent.builtins.save_module -- save_module tool implementation."""

import json
import logging
import sys
from pathlib import Path

import mutagent
from mutagent.toolkits.module_toolkit import ModuleToolkit
from mutagent.tools import Toolkit

logger = logging.getLogger(__name__)

_LEVEL_DIRS = {
    "project": lambda: Path.cwd() / ".mutagent",
    "user": lambda: Path.home() / ".mutagent",
}


def _module_has_toolkit(module_path: str) -> bool:
    """Check whether a loaded module contains any Toolkit subclass."""
    module = sys.modules.get(module_path)
    if module is None:
        return False
    for val in module.__dict__.values():
        if isinstance(val, type) and issubclass(val, Toolkit) and val is not Toolkit:
            return True
    return False


def _ensure_config_modules(directory: Path, module_path: str) -> None:
    """Append *module_path* to the ``modules`` list in *directory*/config.json."""
    config_path = directory / "config.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    modules = data.get("modules")
    if not isinstance(modules, list):
        modules = []
        data["modules"] = modules

    if module_path not in modules:
        modules.append(module_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


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
        msg = f"OK: {module_path} saved to {path}"

        if _module_has_toolkit(module_path):
            try:
                _ensure_config_modules(directory, module_path)
                logger.info("Added %s to config modules in %s", module_path, directory)
            except Exception as exc:
                logger.warning("Config update failed for %s: %s", module_path, exc)
                msg += f"\nWarning: config update failed: {exc}"

        return msg
    except Exception as e:
        logger.error("Failed to save %s: %s: %s", module_path, type(e).__name__, e)
        return f"Error saving {module_path}: {type(e).__name__}: {e}"
