"""mutagent.builtins.define_module -- define_module tool implementation."""

import logging

import mutagent
from mutagent.essential_tools import EssentialTools

logger = logging.getLogger(__name__)


@mutagent.impl(EssentialTools.define_module)
def define_module(self: EssentialTools, module_path: str, source: str) -> str:
    """Define or redefine a Python module in memory."""
    warning = ""
    if module_path.startswith("mutagent."):
        logger.warning("Redefining framework module: %s", module_path)
        warning = (
            "\n⚠ Warning: You are redefining a framework module. "
            "This replaces the entire module including all existing implementations. "
            "Consider using @impl to override specific methods instead."
        )
    try:
        self.module_manager.patch_module(module_path, source)
        version = self.module_manager.get_version(module_path)
        logger.info("Module %s defined (v%d)", module_path, version)
        logger.debug("Source for %s (%d lines, %d bytes)",
                     module_path, source.count('\n') + 1, len(source))
        return f"OK: {module_path} defined (v{version}){warning}"
    except Exception as e:
        logger.error("Failed to define %s: %s: %s", module_path, type(e).__name__, e)
        return f"Error defining {module_path}: {type(e).__name__}: {e}"
