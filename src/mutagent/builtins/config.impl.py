"""Default implementation for mutagent.config.Config methods."""

from __future__ import annotations

from typing import Any

import mutagent
from mutagent.config import Config


@mutagent.impl(Config.get)
def get(self, path: str, default: Any = None, *, merge: bool = True) -> Any:
    parts = path.split(".")
    top_key = parts[0]
    rest = parts[1:]

    # Collect values for the top-level key from all layers
    values: list = []
    for _config_dir, data in self._layers:
        if top_key in data:
            values.append(data[top_key])

    if not values:
        return default

    if merge:
        resolved = _merge_values(values)
    else:
        resolved = values[-1]  # highest priority

    # Traverse remaining path segments
    for segment in rest:
        if isinstance(resolved, dict) and segment in resolved:
            resolved = resolved[segment]
        else:
            return default

    return resolved


@mutagent.impl(Config.get_model)
def get_model(self, name: str | None = None) -> dict:
    if name is None:
        name = _resolve_default_model(self)
    models = self.get("models", {})
    if name not in models:
        raise SystemExit(f"Error: model '{name}' not found in config.")
    model = dict(models[name])  # shallow copy
    if not model.get("auth_token"):
        raise SystemExit(
            f"Error: auth_token for model '{name}' is empty.\n"
            f"Set it in ~/.mutagent/config.json or ./.mutagent/config.json."
        )
    return model


@mutagent.impl(Config.section)
def section(self, key: str) -> Config:
    value = self.get(key)
    if isinstance(value, dict):
        from pathlib import Path

        return Config(_layers=[(Path(), value)])
    return Config(_layers=[])


def _merge_values(values: list) -> Any:
    """Merge a list of values from layers (low to high priority).

    Strategy is inferred from types:
    - All dicts → dict merge (higher priority overwrites same keys)
    - All lists → list concatenation with deduplication
    - Otherwise → highest priority wins
    """
    if all(isinstance(v, dict) for v in values):
        merged: dict = {}
        for v in values:
            merged.update(v)
        return merged
    elif all(isinstance(v, list) for v in values):
        seen: set = set()
        result: list = []
        for v in values:
            for item in v:
                key = item if isinstance(item, str) else id(item)
                if key not in seen:
                    result.append(item)
                    seen.add(key)
        return result
    else:
        return values[-1]


def _resolve_default_model(config: Config) -> str:
    """Resolve the default model name."""
    default = config.get("default_model", "")
    if default:
        return default
    models = config.get("models", {})
    if len(models) == 1:
        return next(iter(models))
    raise SystemExit(
        "Error: no default_model configured and multiple models available.\n"
        f"Available models: {', '.join(models.keys())}"
    )
