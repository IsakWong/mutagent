"""Tests for the Config hierarchical configuration system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import mutagent
import mutagent.builtins  # noqa: F401  -- register all @impl

from mutobj.core import DeclarationMeta, _DECLARED_METHODS
from mutagent.config import Config

# Helper functions live in the impl module, not the declaration
from mutagent.builtins import config_impl as _config_impl

_load_json = _config_impl._load_json
_resolve_paths_inplace = _config_impl._resolve_paths_inplace
_expand_env = _config_impl._expand_env


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
        assert "get_model" in declared
        assert "section" in declared

    def test_has_layers_attribute(self):
        config = Config(_layers=[])
        assert config._layers == []


# ---------------------------------------------------------------------------
# Config.get() tests
# ---------------------------------------------------------------------------

class TestConfigGet:

    def test_get_simple_key(self):
        config = Config(_layers=[(Path(), {"foo": "bar"})])
        assert config.get("foo") == "bar"

    def test_get_missing_key_returns_default(self):
        config = Config(_layers=[(Path(), {"foo": "bar"})])
        assert config.get("missing") is None
        assert config.get("missing", "fallback") == "fallback"

    def test_get_dotted_path(self):
        config = Config(_layers=[(Path(), {"a": {"b": {"c": 42}}})])
        assert config.get("a.b.c") == 42

    def test_get_dotted_path_missing_segment(self):
        config = Config(_layers=[(Path(), {"a": {"b": 1}})])
        assert config.get("a.x.y", "nope") == "nope"

    def test_merge_dicts(self):
        """Dicts from multiple layers should merge (higher priority wins on conflict)."""
        config = Config(_layers=[
            (Path(), {"models": {"a": 1, "b": 2}}),
            (Path(), {"models": {"b": 3, "c": 4}}),
        ])
        result = config.get("models")
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_merge_lists(self):
        """Lists from multiple layers should concatenate with dedup."""
        config = Config(_layers=[
            (Path(), {"path": ["/a", "/b"]}),
            (Path(), {"path": ["/b", "/c"]}),
        ])
        result = config.get("path")
        assert result == ["/a", "/b", "/c"]

    def test_merge_scalars_highest_priority_wins(self):
        config = Config(_layers=[
            (Path(), {"default_model": "low"}),
            (Path(), {"default_model": "high"}),
        ])
        assert config.get("default_model") == "high"

    def test_merge_false_returns_highest_priority(self):
        config = Config(_layers=[
            (Path(), {"models": {"a": 1}}),
            (Path(), {"models": {"b": 2}}),
        ])
        result = config.get("models", merge=False)
        assert result == {"b": 2}

    def test_empty_layers(self):
        config = Config(_layers=[])
        assert config.get("anything", "default") == "default"


# ---------------------------------------------------------------------------
# Config.get_model() tests (provider-based)
# ---------------------------------------------------------------------------

class TestConfigGetModel:

    def test_get_model_list_form(self):
        """list 形式：name 匹配 model_id。"""
        config = Config(_layers=[(Path(), {
            "providers": {
                "anthropic": {
                    "provider": "AnthropicProvider",
                    "base_url": "https://api.anthropic.com",
                    "auth_token": "sk-123",
                    "models": ["claude-sonnet-4", "claude-haiku-4.5"],
                }
            },
        })])
        model = config.get_model("claude-sonnet-4")
        assert model["model_id"] == "claude-sonnet-4"
        assert model["provider"] == "AnthropicProvider"
        assert model["auth_token"] == "sk-123"
        assert "models" not in model  # models 字段不应出现在返回值中

    def test_get_model_dict_form_key_match(self):
        """dict 形式：按 key（别名）匹配。"""
        config = Config(_layers=[(Path(), {
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
        })])
        model = config.get_model("copilot-claude")
        assert model["model_id"] == "claude-sonnet-4"
        assert model["provider"] == "CopilotProvider"
        assert model["github_token"] == "ghu_xxx"

    def test_get_model_dict_form_value_no_match(self):
        """dict 形式：按 value（model_id）不匹配。"""
        config = Config(_layers=[(Path(), {
            "providers": {
                "copilot": {
                    "provider": "CopilotProvider",
                    "models": {
                        "copilot-claude": "claude-sonnet-4",
                    },
                }
            },
        })])
        with pytest.raises(SystemExit, match="not found"):
            config.get_model("claude-sonnet-4")

    def test_get_model_provider_order_priority(self):
        """同名模型：先配置的 provider 胜出。"""
        config = Config(_layers=[(Path(), {
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
        })])
        model = config.get_model("claude-sonnet-4")
        assert model["provider"] == "CopilotProvider"

    def test_get_model_default_name(self):
        """default_model 配置时直接使用。"""
        config = Config(_layers=[(Path(), {
            "default_model": "claude-haiku-4.5",
            "providers": {
                "anthropic": {
                    "provider": "AnthropicProvider",
                    "auth_token": "k",
                    "models": ["claude-sonnet-4", "claude-haiku-4.5"],
                }
            },
        })])
        model = config.get_model()
        assert model["model_id"] == "claude-haiku-4.5"

    def test_get_model_auto_default_first_provider_first_model(self):
        """无 default_model 时取首个 provider 的首个 model。"""
        config = Config(_layers=[(Path(), {
            "providers": {
                "openai": {
                    "provider": "OpenAIProvider",
                    "auth_token": "k",
                    "models": ["gpt-4.1", "gpt-4.1-mini"],
                }
            },
        })])
        model = config.get_model()
        assert model["model_id"] == "gpt-4.1"

    def test_get_model_auto_default_dict_form(self):
        """无 default_model 时 dict 形式取首个 key。"""
        config = Config(_layers=[(Path(), {
            "providers": {
                "copilot": {
                    "provider": "CopilotProvider",
                    "models": {"my-claude": "claude-sonnet-4", "my-gpt": "gpt-4.1"},
                }
            },
        })])
        model = config.get_model()
        assert model["model_id"] == "claude-sonnet-4"

    def test_get_model_not_found_exits(self):
        config = Config(_layers=[(Path(), {
            "providers": {
                "anthropic": {
                    "provider": "AnthropicProvider",
                    "models": ["claude-sonnet-4"],
                }
            },
        })])
        with pytest.raises(SystemExit, match="not found"):
            config.get_model("nonexistent")

    def test_get_model_no_providers_exits(self):
        config = Config(_layers=[(Path(), {})])
        with pytest.raises(SystemExit, match="no providers"):
            config.get_model("anything")


# ---------------------------------------------------------------------------
# Config.get_all_models() tests
# ---------------------------------------------------------------------------

class TestConfigGetAllModels:

    def test_list_form(self):
        config = Config(_layers=[(Path(), {
            "providers": {
                "anthropic": {
                    "provider": "AnthropicProvider",
                    "models": ["claude-sonnet-4", "claude-haiku-4.5"],
                }
            },
        })])
        models = config.get_all_models()
        assert len(models) == 2
        assert models[0] == {
            "name": "claude-sonnet-4",
            "model_id": "claude-sonnet-4",
            "provider": "AnthropicProvider",
            "provider_name": "anthropic",
        }

    def test_dict_form(self):
        config = Config(_layers=[(Path(), {
            "providers": {
                "copilot": {
                    "provider": "CopilotProvider",
                    "models": {"my-claude": "claude-sonnet-4"},
                }
            },
        })])
        models = config.get_all_models()
        assert len(models) == 1
        assert models[0]["name"] == "my-claude"
        assert models[0]["model_id"] == "claude-sonnet-4"

    def test_multiple_providers(self):
        config = Config(_layers=[(Path(), {
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
        })])
        models = config.get_all_models()
        assert len(models) == 2
        assert models[0]["provider_name"] == "copilot"
        assert models[1]["provider_name"] == "openai"
        assert models[1]["name"] == "my-gpt"

    def test_no_providers(self):
        config = Config(_layers=[(Path(), {})])
        assert config.get_all_models() == []


# ---------------------------------------------------------------------------
# Config.section() tests
# ---------------------------------------------------------------------------

class TestConfigSection:

    def test_section_returns_config(self):
        config = Config(_layers=[(Path(), {"my_ext": {"a": 1, "b": 2}})])
        sub = config.section("my_ext")
        assert isinstance(sub, Config)

    def test_section_get(self):
        config = Config(_layers=[(Path(), {"my_ext": {"a": 1, "b": 2}})])
        sub = config.section("my_ext")
        assert sub.get("a") == 1
        assert sub.get("b") == 2

    def test_section_missing_key(self):
        config = Config(_layers=[])
        sub = config.section("missing")
        assert isinstance(sub, Config)
        assert sub.get("anything", "default") == "default"


# ---------------------------------------------------------------------------
# Config.load() tests
# ---------------------------------------------------------------------------

class TestConfigLoad:

    def test_load_returns_config(self, tmp_path, monkeypatch):
        """Config.load() should return a Config instance even without any config files."""
        monkeypatch.chdir(tmp_path)
        config = Config.load([".mutagent/config.json"])
        assert isinstance(config, Config)

    def test_load_project_config(self, tmp_path, monkeypatch):
        """Project-level config should be loaded and have highest priority."""
        monkeypatch.chdir(tmp_path)
        project_dir = tmp_path / ".mutagent"
        project_dir.mkdir()
        (project_dir / "config.json").write_text(
            json.dumps({"custom_key": "project_value"}), encoding="utf-8"
        )
        config = Config.load([".mutagent/config.json"])
        assert config.get("custom_key") == "project_value"


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestHelpers:

    def test_load_json_valid(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text('{"a": 1}', encoding="utf-8")
        assert _load_json(f) == {"a": 1}

    def test_load_json_invalid(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json", encoding="utf-8")
        assert _load_json(f) is None

    def test_load_json_missing(self, tmp_path):
        assert _load_json(tmp_path / "nope.json") is None

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
# Config.load() with file list tests
# ---------------------------------------------------------------------------

class TestConfigLoadFileList:

    def test_load_with_explicit_list(self, tmp_path, monkeypatch):
        """Config.load([path1, path2]) loads both and merges with priority."""
        monkeypatch.chdir(tmp_path)
        d1 = tmp_path / "low"
        d1.mkdir()
        (d1 / "config.json").write_text(
            json.dumps({"key": "low", "only_low": True}), encoding="utf-8"
        )
        d2 = tmp_path / "high"
        d2.mkdir()
        (d2 / "config.json").write_text(
            json.dumps({"key": "high", "only_high": True}), encoding="utf-8"
        )
        config = Config.load([str(d1 / "config.json"), str(d2 / "config.json")])
        assert config.get("key") == "high"
        assert config.get("only_low") is True
        assert config.get("only_high") is True

    def test_load_skips_missing_files(self, tmp_path, monkeypatch):
        """Missing files in the list are silently skipped."""
        monkeypatch.chdir(tmp_path)
        d1 = tmp_path / "exists"
        d1.mkdir()
        (d1 / "config.json").write_text(
            json.dumps({"present": True}), encoding="utf-8"
        )
        config = Config.load([
            str(tmp_path / "nonexistent" / "config.json"),
            str(d1 / "config.json"),
        ])
        assert config.get("present") is True

    def test_load_tilde_expansion(self, tmp_path, monkeypatch):
        """Paths starting with ~ should expand to the home directory."""
        monkeypatch.chdir(tmp_path)
        home_dir = Path.home() / ".test_mutagent_config_load"
        home_dir.mkdir(parents=True, exist_ok=True)
        config_file = home_dir / "config.json"
        config_file.write_text(json.dumps({"from_home": True}), encoding="utf-8")
        try:
            config = Config.load([str(Path("~") / ".test_mutagent_config_load" / "config.json")])
            assert config.get("from_home") is True
        finally:
            config_file.unlink(missing_ok=True)
            home_dir.rmdir()

    def test_load_list_merge_priority(self, tmp_path, monkeypatch):
        """Later files in the list have higher priority."""
        monkeypatch.chdir(tmp_path)
        for i, name in enumerate(["a", "b", "c"]):
            d = tmp_path / name
            d.mkdir()
            (d / "config.json").write_text(
                json.dumps({"default_model": name, "models": {name: {"id": i}}}),
                encoding="utf-8",
            )
        config = Config.load([
            str(tmp_path / "a" / "config.json"),
            str(tmp_path / "b" / "config.json"),
            str(tmp_path / "c" / "config.json"),
        ])
        # default_model: highest priority (c) wins
        assert config.get("default_model") == "c"
        # models: all merged
        models = config.get("models")
        assert "a" in models and "b" in models and "c" in models


# ---------------------------------------------------------------------------
# Environment variable expansion tests
# ---------------------------------------------------------------------------

class TestEnvExpansion:

    def test_expand_dollar_var(self, monkeypatch):
        """$VAR syntax is expanded."""
        monkeypatch.setenv("TEST_KEY", "secret123")
        config = Config(_layers=[(Path(), {"auth_token": "$TEST_KEY"})])
        assert config.get("auth_token") == "secret123"

    def test_expand_dollar_brace_var(self, monkeypatch):
        """${VAR} syntax is expanded."""
        monkeypatch.setenv("MY_TOKEN", "abc")
        config = Config(_layers=[(Path(), {"token": "${MY_TOKEN}"})])
        assert config.get("token") == "abc"

    def test_undefined_var_preserved(self):
        """Undefined env vars are preserved as-is."""
        config = Config(_layers=[(Path(), {"key": "$UNDEFINED_VAR_XYZ"})])
        assert config.get("key") == "$UNDEFINED_VAR_XYZ"

    def test_expand_nested_dict(self, monkeypatch):
        """Env vars in nested dicts are expanded recursively."""
        monkeypatch.setenv("NESTED_VAL", "deep")
        config = Config(_layers=[(Path(), {
            "outer": {"inner": {"val": "$NESTED_VAL"}}
        })])
        assert config.get("outer.inner.val") == "deep"

    def test_expand_in_list(self, monkeypatch):
        """Env vars in list values are expanded."""
        monkeypatch.setenv("LIST_VAL", "item")
        config = Config(_layers=[(Path(), {"items": ["$LIST_VAL", "static"]})])
        result = config.get("items")
        assert result == ["item", "static"]

    def test_non_string_values_unchanged(self):
        """int, bool, None values are not affected."""
        config = Config(_layers=[(Path(), {"count": 42, "flag": True})])
        assert config.get("count") == 42
        assert config.get("flag") is True

    def test_expand_does_not_modify_layers(self, monkeypatch):
        """Expansion is lazy; _layers data stays unmodified."""
        monkeypatch.setenv("LAZY_VAR", "expanded")
        raw_data = {"key": "$LAZY_VAR"}
        config = Config(_layers=[(Path(), raw_data)])
        config.get("key")  # trigger expansion
        assert raw_data["key"] == "$LAZY_VAR"

    def test_expand_mixed_text(self, monkeypatch):
        """$VAR embedded in a larger string is expanded."""
        monkeypatch.setenv("HOST", "localhost")
        config = Config(_layers=[(Path(), {"url": "http://$HOST:8080/api"})])
        assert config.get("url") == "http://localhost:8080/api"

    def test_expand_multiple_vars(self, monkeypatch):
        """Multiple vars in one string are all expanded."""
        monkeypatch.setenv("PROTO", "https")
        monkeypatch.setenv("DOMAIN", "example.com")
        config = Config(_layers=[(Path(), {"url": "$PROTO://$DOMAIN"})])
        assert config.get("url") == "https://example.com"
