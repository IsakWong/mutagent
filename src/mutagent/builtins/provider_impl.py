"""LLMProvider.resolve_model / list_models 默认实现。

从 Config 的 providers 结构中查找和列举模型。
"""

from __future__ import annotations

from typing import Any

import mutagent
from mutagent.config import Config
from mutagent.provider import LLMProvider


def _resolve_default_model(config: Config) -> str | None:
    """解析默认模型名。找不到时返回 None。"""
    default = config.get("default_model", default="")
    if default:
        return default
    providers = config.get("providers", default={})
    if not providers:
        return None
    for _prov_name, prov_conf in providers.items():
        models = prov_conf.get("models", [])
        if isinstance(models, list) and models:
            return models[0]
        elif isinstance(models, dict) and models:
            return next(iter(models))
    return None


@classmethod
@mutagent.impl(LLMProvider.resolve_model)
def _resolve_model_impl(cls, config: Config, name: str | None = None) -> dict | None:
    if name is None:
        name = _resolve_default_model(config)
        if name is None:
            return None
    providers = config.get("providers", default={})
    if not providers:
        return None
    for _prov_name, prov_conf in providers.items():
        models = prov_conf.get("models", [])
        if isinstance(models, list):
            if name in models:
                result = {k: v for k, v in prov_conf.items() if k != "models"}
                result["model_id"] = name
                return result
        elif isinstance(models, dict):
            if name in models:
                model_val = models[name]
                result = {k: v for k, v in prov_conf.items() if k != "models"}
                if isinstance(model_val, str):
                    result["model_id"] = model_val
                elif isinstance(model_val, dict):
                    result["model_id"] = model_val.get("model_id", name)
                    result.update({
                        k: v for k, v in model_val.items() if k != "model_id"
                    })
                else:
                    result["model_id"] = name
                return result
    return None


@classmethod
@mutagent.impl(LLMProvider.list_models)
def _list_models_impl(cls, config: Config) -> list[dict]:
    providers = config.get("providers", default={})
    result: list[dict] = []
    for prov_name, prov_conf in providers.items():
        provider_cls_path = prov_conf.get("provider", "mutagent.builtins.anthropic_provider.AnthropicProvider")
        models = prov_conf.get("models", [])
        if isinstance(models, list):
            for model_id in models:
                result.append({
                    "name": model_id,
                    "model_id": model_id,
                    "provider": provider_cls_path,
                    "provider_name": prov_name,
                })
        elif isinstance(models, dict):
            for alias, model_id in models.items():
                result.append({
                    "name": alias,
                    "model_id": model_id,
                    "provider": provider_cls_path,
                    "provider_name": prov_name,
                })
    return result
