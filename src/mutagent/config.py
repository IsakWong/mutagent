"""mutagent.config -- Hierarchical configuration system."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import mutagent

if TYPE_CHECKING:
    from mutagent.agent import Agent

_PACKAGE_DIR = Path(__file__).parent


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
    def load(cls) -> Config:
        """Scan config files from all levels and construct a Config object.

        Priority: ./.mutagent/config.json > ~/.mutagent/config.json > package config.json

        This is a plain classmethod (not an @impl stub) because it runs
        during the bootstrap phase before builtins are loaded.
        """
        layers: list[tuple[Path, dict]] = []

        # Level 3: package built-in defaults (lowest priority)
        pkg_config = _PACKAGE_DIR / "config.json"
        if pkg_config.exists():
            data = _load_json(pkg_config)
            if data is not None:
                _resolve_paths_inplace(data, _PACKAGE_DIR)
                layers.append((_PACKAGE_DIR, data))

        # Level 2: user-level config
        home_config = Path.home() / ".mutagent" / "config.json"
        if home_config.exists():
            data = _load_json(home_config)
            if data is not None:
                _resolve_paths_inplace(data, home_config.parent)
                layers.append((home_config.parent, data))

        # Level 1: project-level config (highest priority)
        project_config = Path.cwd() / ".mutagent" / "config.json"
        if project_config.exists():
            data = _load_json(project_config)
            if data is not None:
                _resolve_paths_inplace(data, project_config.parent)
                layers.append((project_config.parent, data))

        return cls(_layers=layers)

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


# ---------------------------------------------------------------------------
# Helpers (plain functions, no @impl dependency)
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict | None:
    """Load a single JSON file.  Returns None on parse/IO failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_paths_inplace(data: dict, config_dir: Path) -> None:
    """Resolve relative ``path`` entries to absolute paths in-place.

    Each relative path is resolved against *config_dir*.  Absolute paths
    are kept as-is.  The list values are replaced with resolved string
    paths so that downstream consumers don't need config_dir context.
    """
    raw_paths = data.get("path")
    if not isinstance(raw_paths, list):
        return
    resolved: list[str] = []
    for p in raw_paths:
        pp = Path(p)
        if not pp.is_absolute():
            pp = (config_dir / pp).resolve()
        resolved.append(str(pp))
    data["path"] = resolved
