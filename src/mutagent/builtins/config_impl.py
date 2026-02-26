"""Default implementation for mutagent.config.Config methods."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import mutagent
from mutagent.config import Config


@classmethod
@mutagent.impl(Config.load)
def load(cls, config_files: list[str | Path]) -> Config:
    """从配置文件列表构建 Config 对象。

    列表按序加载，靠后的优先级更高。
    """
    layers: list[tuple[Path, dict]] = []
    for raw_path in config_files:
        p = Path(raw_path).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if not p.exists():
            continue
        data = _load_json(p)
        if data is not None:
            _resolve_paths_inplace(data, p.parent)
            layers.append((p.parent, data))

    return cls(_layers=layers)


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

    return _expand_env(resolved)


@mutagent.impl(Config.get_model)
def get_model(self, name: str | None = None) -> dict:
    if name is None:
        name = _resolve_default_model(self)
    models = self.get("models", {})
    if name not in models:
        raise SystemExit(f"Error: model '{name}' not found in config.")
    model = dict(models[name])  # shallow copy
    return model


@mutagent.impl(Config.section)
def section(self, key: str) -> Config:
    value = self.get(key)
    if isinstance(value, dict):
        from pathlib import Path

        return Config(_layers=[(Path(), value)])
    return Config(_layers=[])


def _expand_env(value: Any) -> Any:
    """递归展开配置值中的环境变量引用。

    支持 $VAR 和 ${VAR} 语法。环境变量不存在时保留原文。
    仅对 str 值展开，不影响 int/bool 等类型。
    """
    if isinstance(value, str):
        return re.sub(
            r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)',
            lambda m: os.environ.get(m.group(1) or m.group(2), m.group(0)),
            value,
        )
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


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