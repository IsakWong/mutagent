"""Tests for the Config system (DictConfig + LLMProvider.resolve_model/list_models)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import mutagent
import mutagent.builtins  # noqa: F401  -- register all @impl

from mutobj.core import DeclarationMeta, _DECLARED_METHODS
from mutagent.config import Config, ConfigChangeEvent, Disposable
from mutagent.builtins.main_impl import (
    DictConfig, _expand_env, _resolve_paths_inplace,
)
from mutagent.provider import LLMProvider


# ---------------------------------------------------------------------------
# Declaration tests
# ---------------------------------------------------------------------------

class TestConfigDeclaration:

    def test_inherits_from_mutagent_declaration(self):
        assert issubclass(Config, mutagent.Declaration)

    def test_uses_declaration_meta(self):
        assert isinstance(Config, DeclarationMeta)

    def test_declared_methods(self):
        declared = getattr(Config, _DECLARED_METHODS, set())
        assert "get" in declared
        assert "set" in declared
        assert "on_change" in declared

    def test_stub_get_returns_default(self):
        config = Config()
        assert config.get("anything") is None
        assert config.get("anything", default="fallback") == "fallback"

    def test_stub_set_does_nothing(self):
        config = Config()
        config.set("key", "value")  # should not raise

    def test_stub_on_change_returns_disposable(self):
        config = Config()
        d = config.on_change("pattern", lambda e: None)
        assert isinstance(d, Disposable)
        d.dispose()  # should not raise


# ---------------------------------------------------------------------------
# DictConfig tests
# ---------------------------------------------------------------------------

class TestDictConfigGet:

    def test_get_simple_key(self):
        config = DictConfig(_data={"foo": "bar"}, _listeners=[])
        assert config.get("foo") == "bar"

    def test_get_missing_key_returns_default(self):
        config = DictConfig(_data={"foo": "bar"}, _listeners=[])
        assert config.get("missing") is None
        assert config.get("missing", default="fallback") == "fallback"

    def test_get_dotted_path(self):
        config = DictConfig(_data={"a": {"b": {"c": 42}}}, _listeners=[])
        assert config.get("a.b.c") == 42

    def test_get_dotted_path_missing_segment(self):
        config = DictConfig(_data={"a": {"b": 1}}, _listeners=[])
        assert config.get("a.x.y", default="nope") == "nope"

    def test_empty_data(self):
        config = DictConfig(_data={}, _listeners=[])
        assert config.get("anything", default="default") == "default"


class TestDictConfigSet:

    def test_set_creates_path(self):
        config = DictConfig(_data={}, _listeners=[])
        config.set("a.b.c", 42)
        assert config.get("a.b.c") == 42

    def test_set_triggers_callback(self):
        events = []
        config = DictConfig(_data={}, _listeners=[])
        config.on_change("a.*", lambda e: events.append(e))
        config.set("a.b", 1)
        assert len(events) == 1
        assert events[0].key == "a.b"
        assert events[0].config is config

    def test_set_does_not_trigger_unrelated(self):
        events = []
        config = DictConfig(_data={}, _listeners=[])
        config.on_change("a.*", lambda e: events.append(e))
        config.set("b.c", 1)
        assert len(events) == 0


class TestDictConfigOnChange:

    def test_dispose_removes_listener(self):
        events = []
        config = DictConfig(_data={}, _listeners=[])
        d = config.on_change("a", lambda e: events.append(e))
        config.set("a", 1)
        assert len(events) == 1
        d.dispose()
        config.set("a", 2)
        assert len(events) == 1  # 不再触发

    def test_ancestor_triggers(self):
        """set("providers") 应触发 on_change("providers.anthropic.auth_token")"""
        events = []
        config = DictConfig(_data={}, _listeners=[])
        config.on_change("providers.anthropic.auth_token", lambda e: events.append(e))
        config.set("providers", {"anthropic": {"auth_token": "new"}})
        assert len(events) == 1

    def test_double_star_wildcard(self):
        events = []
        config = DictConfig(_data={}, _listeners=[])
        config.on_change("providers.**", lambda e: events.append(e))
        config.set("providers.anthropic.auth_token", "new")
        assert len(events) == 1


# ---------------------------------------------------------------------------
# Config.affects() tests
# ---------------------------------------------------------------------------

class TestConfigAffects:

    def test_exact_match(self):
        config = Config()
        assert config.affects("a.b.c", "a.b.c") is True

    def test_single_wildcard(self):
        config = Config()
        assert config.affects("providers.*", "providers.anthropic") is True
        assert config.affects("providers.*", "providers.anthropic.auth_token") is False

    def test_double_wildcard(self):
        config = Config()
        assert config.affects("providers.**", "providers.anthropic") is True
        assert config.affects("providers.**", "providers.anthropic.auth_token") is True

    def test_ancestor_match(self):
        config = Config()
        assert config.affects("providers.anthropic.auth_token", "providers") is True
        assert config.affects("providers.**", "providers") is True

    def test_no_match(self):
        config = Config()
        assert config.affects("providers.*", "agents.xxx") is False


# ---------------------------------------------------------------------------
# LLMProvider.resolve_model() tests
# ---------------------------------------------------------------------------

class TestResolveModel:

    def test_list_form(self):
        config = DictConfig(_data={
            "providers": {
                "anthropic": {
                    "provider": "AnthropicProvider",
                    "base_url": "https://api.anthropic.com",
                    "auth_token": "sk-123",
                    "models": ["claude-sonnet-4", "claude-haiku-4.5"],
                }
            },
        }, _listeners=[])
        model = LLMProvider.resolve_model(config, "claude-sonnet-4")
        assert model is not None
        assert model["model_id"] == "claude-sonnet-4"
        assert model["provider"] == "AnthropicProvider"
        assert model["auth_token"] == "sk-123"
        assert "models" not in model

    def test_dict_form_key_match(self):
        config = DictConfig(_data={
            "providers": {
                "copilot": {
                    "provider": "CopilotProvider",
                    "github_token": "ghu_xxx",
                    "models": {
                        "copilot-claude": "claude-sonnet-4",
                        "copilot-gpt": "gpt-4.1",
                    },
                }
            },
        }, _listeners=[])
        model = LLMProvider.resolve_model(config, "copilot-claude")
        assert model is not None
        assert model["model_id"] == "claude-sonnet-4"
        assert model["provider"] == "CopilotProvider"

    def test_dict_form_value_no_match(self):
        config = DictConfig(_data={
            "providers": {
                "copilot": {
                    "provider": "CopilotProvider",
                    "models": {"copilot-claude": "claude-sonnet-4"},
                }
            },
        }, _listeners=[])
        result = LLMProvider.resolve_model(config, "claude-sonnet-4")
        assert result is None  # 不再 raise SystemExit

    def test_provider_order_priority(self):
        config = DictConfig(_data={
            "providers": {
                "copilot": {
                    "provider": "CopilotProvider",
                    "github_token": "ghu_xxx",
                    "models": ["claude-sonnet-4"],
                },
                "anthropic": {
                    "provider": "AnthropicProvider",
                    "auth_token": "sk-123",
                    "models": ["claude-sonnet-4"],
                },
            },
        }, _listeners=[])
        model = LLMProvider.resolve_model(config, "claude-sonnet-4")
        assert model is not None
        assert model["provider"] == "CopilotProvider"

    def test_default_model_from_config(self):
        config = DictConfig(_data={
            "default_model": "claude-haiku-4.5",
            "providers": {
                "anthropic": {
                    "provider": "AnthropicProvider",
                    "auth_token": "k",
                    "models": ["claude-sonnet-4", "claude-haiku-4.5"],
                }
            },
        }, _listeners=[])
        model = LLMProvider.resolve_model(config)
        assert model is not None
        assert model["model_id"] == "claude-haiku-4.5"

    def test_auto_default_first_model(self):
        config = DictConfig(_data={
            "providers": {
                "openai": {
                    "provider": "OpenAIProvider",
                    "auth_token": "k",
                    "models": ["gpt-4.1", "gpt-4.1-mini"],
                }
            },
        }, _listeners=[])
        model = LLMProvider.resolve_model(config)
        assert model is not None
        assert model["model_id"] == "gpt-4.1"

    def test_not_found_returns_none(self):
        config = DictConfig(_data={
            "providers": {
                "anthropic": {
                    "provider": "AnthropicProvider",
                    "models": ["claude-sonnet-4"],
                }
            },
        }, _listeners=[])
        assert LLMProvider.resolve_model(config, "nonexistent") is None

    def test_no_providers_returns_none(self):
        config = DictConfig(_data={}, _listeners=[])
        assert LLMProvider.resolve_model(config, "anything") is None
        assert LLMProvider.resolve_model(config) is None


# ---------------------------------------------------------------------------
# LLMProvider.list_models() tests
# ---------------------------------------------------------------------------

class TestListModels:

    def test_list_form(self):
        config = DictConfig(_data={
            "providers": {
                "anthropic": {
                    "provider": "AnthropicProvider",
                    "models": ["claude-sonnet-4", "claude-haiku-4.5"],
                }
            },
        }, _listeners=[])
        models = LLMProvider.list_models(config)
        assert len(models) == 2
        assert models[0] == {
            "name": "claude-sonnet-4",
            "model_id": "claude-sonnet-4",
            "provider": "AnthropicProvider",
            "provider_name": "anthropic",
        }

    def test_dict_form(self):
        config = DictConfig(_data={
            "providers": {
                "copilot": {
                    "provider": "CopilotProvider",
                    "models": {"my-claude": "claude-sonnet-4"},
                }
            },
        }, _listeners=[])
        models = LLMProvider.list_models(config)
        assert len(models) == 1
        assert models[0]["name"] == "my-claude"
        assert models[0]["model_id"] == "claude-sonnet-4"

    def test_multiple_providers(self):
        config = DictConfig(_data={
            "providers": {
                "copilot": {
                    "provider": "CopilotProvider",
                    "models": ["claude-sonnet-4"],
                },
                "openai": {
                    "provider": "OpenAIProvider",
                    "models": {"my-gpt": "gpt-4.1"},
                },
            },
        }, _listeners=[])
        models = LLMProvider.list_models(config)
        assert len(models) == 2
        assert models[0]["provider_name"] == "copilot"
        assert models[1]["provider_name"] == "openai"

    def test_no_providers(self):
        config = DictConfig(_data={}, _listeners=[])
        assert LLMProvider.list_models(config) == []


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestHelpers:

    def test_resolve_paths_inplace_relative(self, tmp_path):
        data = {"path": ["lib", "ext"]}
        _resolve_paths_inplace(data, tmp_path)
        assert data["path"][0] == str((tmp_path / "lib").resolve())
        assert data["path"][1] == str((tmp_path / "ext").resolve())

    def test_resolve_paths_inplace_absolute(self):
        abs_path = str(Path.home() / "absolute" / "path")
        data = {"path": [abs_path]}
        _resolve_paths_inplace(data, Path("/other"))
        assert data["path"][0] == abs_path

    def test_resolve_paths_inplace_no_path_key(self):
        data = {"foo": "bar"}
        _resolve_paths_inplace(data, Path("/x"))
        assert data == {"foo": "bar"}


# ---------------------------------------------------------------------------
# Environment variable expansion tests
# ---------------------------------------------------------------------------

class TestEnvExpansion:

    def test_expand_dollar_var(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "secret123")
        config = DictConfig(_data={"auth_token": "$TEST_KEY"}, _listeners=[])
        assert config.get("auth_token") == "secret123"

    def test_expand_dollar_brace_var(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "abc")
        config = DictConfig(_data={"token": "${MY_TOKEN}"}, _listeners=[])
        assert config.get("token") == "abc"

    def test_undefined_var_preserved(self):
        config = DictConfig(_data={"key": "$UNDEFINED_VAR_XYZ"}, _listeners=[])
        assert config.get("key") == "$UNDEFINED_VAR_XYZ"

    def test_expand_nested_dict(self, monkeypatch):
        monkeypatch.setenv("NESTED_VAL", "deep")
        config = DictConfig(_data={
            "outer": {"inner": {"val": "$NESTED_VAL"}}
        }, _listeners=[])
        assert config.get("outer.inner.val") == "deep"

    def test_expand_in_list(self, monkeypatch):
        monkeypatch.setenv("LIST_VAL", "item")
        config = DictConfig(_data={"items": ["$LIST_VAL", "static"]}, _listeners=[])
        result = config.get("items")
        assert result == ["item", "static"]

    def test_non_string_values_unchanged(self):
        config = DictConfig(_data={"count": 42, "flag": True}, _listeners=[])
        assert config.get("count") == 42
        assert config.get("flag") is True

    def test_expand_mixed_text(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        config = DictConfig(_data={"url": "http://$HOST:8080/api"}, _listeners=[])
        assert config.get("url") == "http://localhost:8080/api"

    def test_expand_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("PROTO", "https")
        monkeypatch.setenv("DOMAIN", "example.com")
        config = DictConfig(_data={"url": "$PROTO://$DOMAIN"}, _listeners=[])
        assert config.get("url") == "https://example.com"
