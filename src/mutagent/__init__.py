"""mutagent - A Python AI Agent framework for runtime self-iterating code."""

__version__ = "0.6.999"

from mutobj import Declaration, impl, field, register_module_impls, unregister_module_impls
from mutagent.tools import Toolkit

__all__ = ["Declaration", "impl", "field", "register_module_impls", "unregister_module_impls", "Toolkit"]
