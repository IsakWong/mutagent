"""Tests for the Config hierarchical configuration system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import mutagent
import mutagent.builtins  # noqa: F401  -- register all @impl

from mutagent.base import MutagentMeta
from mutagent.config import Config

# Helper functions live in the impl module, not the declaration
import sys
_config_impl = sys.modules.get("mutagent.builtins.config")
if _config_impl is None:
    from pathlib import Path as _Path
    import importlib.util as _ilu
    _impl_path = (
        _Path(__file__).resolve().parent.parent
        / "src" / "mutagent" / "builtins" / "config.impl.py"
    )
    _spec = _ilu.spec_from_file_location("mutagent.builtins.config_impl", str(_impl_path))
    _config_impl = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_config_impl)
_load_json = _config_impl._load_json
_resolve_paths_inplace = _config_impl._resolve_paths_inplace
from forwardpy.core import _DECLARED_METHODS


# ---------------------------------------------------------------------------
# Declaration tests
# ---------------------------------------------------------------------------

class TestConfigDeclaration:

    def test_inherits_from_mutagent_object(self):
        assert issubclass(Config, mutagent.Object)

    def test_uses_mutagent_meta(self):
        assert isinstance(Config, MutagentMeta)

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
# Config.get_model() tests
# ---------------------------------------------------------------------------

class TestConfigGetModel:

    def test_get_model_by_name(self):
        config = Config(_layers=[(Path(), {
            "models": {"gpt": {"model_id": "gpt-4", "auth_token": "sk-123", "base_url": "https://api.openai.com"}},
        })])
        model = config.get_model("gpt")
        assert model["model_id"] == "gpt-4"
        assert model["auth_token"] == "sk-123"

    def test_get_model_default_name(self):
        config = Config(_layers=[(Path(), {
            "models": {"claude": {"model_id": "claude-3", "auth_token": "key"}},
            "default_model": "claude",
        })])
        model = config.get_model()
        assert model["model_id"] == "claude-3"

    def test_get_model_single_model_auto_default(self):
        """When only one model exists and no default_model, it should be auto-selected."""
        config = Config(_layers=[(Path(), {
            "models": {"only": {"model_id": "m", "auth_token": "k"}},
        })])
        model = config.get_model()
        assert model["model_id"] == "m"

    def test_get_model_not_found_exits(self):
        config = Config(_layers=[(Path(), {"models": {}})])
        with pytest.raises(SystemExit, match="not found"):
            config.get_model("nonexistent")

    def test_get_model_empty_auth_token_exits(self):
        config = Config(_layers=[(Path(), {
            "models": {"test": {"model_id": "m", "auth_token": ""}},
            "default_model": "test",
        })])
        with pytest.raises(SystemExit, match="auth_token"):
            config.get_model("test")

    def test_get_model_no_default_multiple_models_exits(self):
        config = Config(_layers=[(Path(), {
            "models": {"a": {"auth_token": "k"}, "b": {"auth_token": "k"}},
        })])
        with pytest.raises(SystemExit, match="no default_model"):
            config.get_model()

    def test_get_model_returns_copy(self):
        """Returned dict should be a copy, not a reference to internal data."""
        config = Config(_layers=[(Path(), {
            "models": {"x": {"model_id": "m", "auth_token": "k"}},
            "default_model": "x",
        })])
        m1 = config.get_model("x")
        m1["extra"] = True
        m2 = config.get_model("x")
        assert "extra" not in m2


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
        config = Config.load(".mutagent/config.json")
        assert isinstance(config, Config)

    def test_load_project_config(self, tmp_path, monkeypatch):
        """Project-level config should be loaded and have highest priority."""
        monkeypatch.chdir(tmp_path)
        project_dir = tmp_path / ".mutagent"
        project_dir.mkdir()
        (project_dir / "config.json").write_text(
            json.dumps({"custom_key": "project_value"}), encoding="utf-8"
        )
        config = Config.load(".mutagent/config.json")
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
