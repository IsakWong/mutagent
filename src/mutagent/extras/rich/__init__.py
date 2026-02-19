"""mutagent.extras.rich -- Rich enhanced terminal for mutagent."""

try:
    import rich  # noqa: F401
except ImportError:
    raise ImportError(
        "mutagent.extras.rich requires the 'rich' package. "
        "Install it with: pip install mutagent[rich]"
    )

from . import userio_impl   # noqa: F401  -- register @impl overrides
from . import block_handlers  # noqa: F401  -- register rich BlockHandlers
