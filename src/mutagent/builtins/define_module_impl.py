"""mutagent.builtins.define_module -- define_module tool implementation."""

import logging

import mutagent
from mutagent.toolkits.module_toolkit import ModuleToolkit

logger = logging.getLogger(__name__)


@mutagent.impl(ModuleToolkit.define)
def define(self: ModuleToolkit, module_path: str, source: str) -> str:
    """Define or redefine a Python module in memory."""
    warning = ""
    if module_path.startswith("mutagent."):
        logger.warning("Redefining framework module: %s", module_path)
        warning = (
            "\n⚠ Warning: You are redefining a framework module. "
            "This replaces the entire module including all existing implementations. "
            "Consider using @impl to override specific methods instead."
        )
    self.module_manager.patch_module(module_path, source)
    version = self.module_manager.get_version(module_path)
    logger.info("Module %s defined (v%d)", module_path, version)
    logger.debug("Source for %s (%d lines, %d bytes)",
                 module_path, source.count('\n') + 1, len(source))
    return f"OK: {module_path} defined (v{version}){warning}"
