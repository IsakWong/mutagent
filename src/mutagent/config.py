"""mutagent.config -- 可观察的配置容器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

import mutagent

if TYPE_CHECKING:
    pass


@dataclass
class ConfigChangeEvent:
    """配置变更事件。"""
    key: str                    # 被设置的完整路径（如 "providers.anthropic"）
    source: str                 # 变更来源标识（如 "user", "workspace"）
    config: Config = field(repr=False)  # 触发变更的 Config 实例


ChangeCallback = Callable[[ConfigChangeEvent], None]


class Disposable:
    """取消器。调用 dispose() 取消订阅。"""

    _dispose_fn: Callable[[], None] | None

    def __init__(self, dispose: Callable[[], None] | None = None) -> None:
        self._dispose_fn = dispose

    def dispose(self) -> None:
        if self._dispose_fn is not None:
            self._dispose_fn()
            self._dispose_fn = None


# ---------------------------------------------------------------------------
# Pattern matching helpers
# ---------------------------------------------------------------------------

def _glob_match(pattern_parts: list[str], key_parts: list[str]) -> bool:
    """glob 风格匹配。* 匹配单段，** 匹配任意段。"""
    return _do_match(pattern_parts, 0, key_parts, 0)


def _do_match(pp: list[str], pi: int, kp: list[str], ki: int) -> bool:
    while pi < len(pp) and ki < len(kp):
        if pp[pi] == "**":
            for skip in range(ki, len(kp) + 1):
                if _do_match(pp, pi + 1, kp, skip):
                    return True
            return False
        if pp[pi] == "*" or pp[pi] == kp[ki]:
            pi += 1
            ki += 1
        else:
            return False
    while pi < len(pp) and pp[pi] == "**":
        pi += 1
    return pi == len(pp) and ki == len(kp)


class Config(mutagent.Declaration):
    """可观察的配置容器。

    所有方法提供默认实现，Config 本身即可用的空配置：
    - get() → 返回 default
    - set() → 空操作
    - on_change() → 返回空 Disposable
    - affects() → glob 双向匹配
    """

    def get(self, name: str, *, default: Any = None) -> Any:
        """读取配置值。name 为点分路径。

        示例：
            config.get("providers.anthropic.auth_token")
            config.get("providers")  # 返回整个 providers dict
            config.get("agents.sub_agent.model", default="claude-sonnet")
        """
        return default

    def set(self, name: str, value: Any, *, source: str = "") -> None:
        """设置配置值并触发变更通知。

        name: 点分路径（如 "providers.anthropic"）
        value: 新值（任意类型）
        source: 变更来源标识（如 "user", "workspace", "runtime"）

        设置一个节点会隐式影响所有子路径。例如：
        set("providers.anthropic", new_dict) 会触发所有监听
        providers.anthropic 及其子路径的回调。
        """

    def on_change(self, pattern: str, callback: ChangeCallback) -> Disposable:
        """监听配置变更。

        pattern 支持 glob 风格通配符：
        - 精确路径："providers.anthropic.auth_token"
        - 单级通配 *："providers.*" — 匹配 providers 的任意直接子项
        - 递归通配 **："providers.**" — 匹配 providers 下任意深度
        - 混合："providers.*.models" — 任意 provider 的 models

        触发规则（pattern 与 set 的 key 双向匹配）：
        1. key 匹配 pattern → 触发（监听范围内的 key 被设置）
           on_change("providers.*", cb) + set("providers.anthropic") → ✓
        2. key 是 pattern 的祖先 → 触发（父节点被替换，子路径隐式变更）
           on_change("providers.anthropic.auth_token", cb) + set("providers.anthropic") → ✓
           on_change("providers.**", cb) + set("providers") → ✓
        3. 不相关 → 不触发
           on_change("providers.*", cb) + set("agents.xxx") → ✗
           on_change("providers.*", cb) + set("providers.anthropic.auth_token") → ✗
           （* 只匹配一级，auth_token 是两级深）
        """
        return Disposable()

    def affects(self, pattern: str, key: str) -> bool:
        """判断 key 的变更是否影响 pattern 指定的路径。

        双向匹配：
        1. key 匹配 pattern → True（标准 glob）
        2. key 是 pattern 的祖先 → True（父节点被替换，子路径隐式变更）
        3. 不相关 → False

        子类可覆盖以定制匹配策略。
        """
        pattern_parts = pattern.split(".")
        key_parts = key.split(".")

        # 规则 1: key 匹配 pattern
        if _glob_match(pattern_parts, key_parts):
            return True

        # 规则 2: key 是 pattern 的祖先
        if len(key_parts) < len(pattern_parts):
            prefix_match = True
            for i, kp in enumerate(key_parts):
                pp = pattern_parts[i]
                if pp == "**":
                    break
                if pp != "*" and pp != kp:
                    prefix_match = False
                    break
            if prefix_match:
                return True

        return False
