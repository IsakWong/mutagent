"""mutagent.config -- Hierarchical configuration system."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import mutagent

if TYPE_CHECKING:
    from mutagent.agent import Agent


class Config(mutagent.Object):
    """Extensible configuration object.

    Preserves per-level raw data and assembles merged results on access.
    Built-in fields (models, env, etc.) are accessed through dedicated
    methods; extensions can place arbitrary fields in config.json and
    read them via ``get()``.  All methods can be overridden via ``@impl``.

    Attributes:
        _layers: List of ``(config_dir, raw_data)`` tuples, ordered from
            lowest to highest priority.
    """

    _layers: list  # list[tuple[Path, dict]]

    @classmethod
    def load(cls, config_path: Path) -> Config:
        """Scan config files from all levels and construct a Config object.

        Priority: ./{config_path} > ~/{config_path}

        This is a plain classmethod (not an @impl stub) because it runs
        during the bootstrap phase before builtins are loaded
        
        Args:
            config_path: Relative path to the config file (e.g. ".mutagent/config.json").
        """
        ...

    def get(self, path: str, default: Any = None, *, merge: bool = True) -> Any:
        """Get a configuration value by key path.

        Supports dotted paths (e.g. ``"models.glm.base_url"``).

        Args:
            path: Dot-separated key path.
            default: Value to return if the path is not found.
            merge: If True (default), merge values across layers using
                type-based inference (dicts merge, lists concatenate,
                scalars use highest priority).  If False, return the
                highest-priority layer's value without merging.

        Returns:
            The resolved configuration value, or *default*.
        """
        ...

    def get_model(self, name: str | None = None) -> dict:
        """Get a model configuration dict by name.

        Args:
            name: Model name (a key in the ``models`` dict).
                If None, uses the default model.

        Returns:
            A dict with at least ``base_url``, ``auth_token``, ``model_id``.

        Raises:
            SystemExit: If the model is not found or auth_token is empty.
        """
        ...

    def section(self, key: str) -> Config:
        """Get a sub-configuration view for a top-level key.

        Returns a new Config whose data is the merged result of the given
        key, wrapped as a single-layer Config.  Useful for passing a
        namespaced config section to extension modules.

        Args:
            key: Top-level configuration key.

        Returns:
            A new Config scoped to that section.
        """
        ...
