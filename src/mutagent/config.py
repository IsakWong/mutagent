"""mutagent.config -- Hierarchical configuration system."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import mutagent

if TYPE_CHECKING:
    from mutagent.agent import Agent


class Config(mutagent.Declaration):
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
    def load(cls, config_files: list[str | Path]) -> Config:
        """从配置文件列表构建 Config 对象。

        文件按列表顺序加载，靠后的优先级更高。
        不存在的文件自动跳过。

        路径展开规则：
        - "~" 前缀展开为用户目录（Path.home()）
        - 相对路径相对于 cwd 展开
        - 绝对路径不变

        Args:
            config_files: 配置文件路径列表（低优先级 → 高优先级）。
        """
        return config_impl.load(cls, config_files)

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
        return config_impl.get(self, path, default=default, merge=merge)

    def get_model(self, name: str | None = None) -> dict:
        """Get a model configuration dict by name.

        Args:
            name: Model name (a key in the ``models`` dict).
                If None, uses the default model.

        Returns:
            A dict with model configuration fields (e.g. ``provider``,
            ``model_id``, ``base_url``, ``auth_token``).
            Field validation is delegated to each Provider's ``from_config()``.

        Raises:
            SystemExit: If the model is not found.
        """
        return config_impl.get_model(self, name)

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
        return config_impl.section(self, key)


from mutagent.builtins import config_impl
mutagent.register_module_impls(config_impl)