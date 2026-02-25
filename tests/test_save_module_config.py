"""Tests for save_module config.json auto-update when saving Toolkit modules."""

import json
import sys
from pathlib import Path

import pytest

from mutagent.toolkits.module_toolkit import ModuleToolkit
from mutagent.runtime.module_manager import ModuleManager
from mutagent.builtins.save_module_impl import _module_has_toolkit, _ensure_config_modules

import mutagent.builtins  # noqa: F401  -- register all @impl


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    mgr = ModuleManager()
    t = ModuleToolkit(module_manager=mgr)
    yield t
    mgr.cleanup()


# -- Helper: _module_has_toolkit -----------------------------------------

class TestModuleHasToolkit:

    def test_module_has_toolkit_true(self, tools):
        tools.module_manager.patch_module(
            "tk_check_yes",
            "import mutagent\n\nclass MyTK(mutagent.Toolkit):\n    pass\n",
        )
        assert _module_has_toolkit("tk_check_yes") is True

    def test_module_has_toolkit_false(self, tools):
        tools.module_manager.patch_module("tk_check_no", "val = 42\n")
        assert _module_has_toolkit("tk_check_no") is False

    def test_module_with_toolkit_import_not_subclass(self, tools):
        """Importing Toolkit without subclassing should return False."""
        tools.module_manager.patch_module(
            "tk_check_import_only",
            "from mutagent.tools import Toolkit\nval = 1\n",
        )
        assert _module_has_toolkit("tk_check_import_only") is False

    def test_nonexistent_module(self):
        assert _module_has_toolkit("nonexistent_module_xyz") is False


# -- Helper: _ensure_config_modules --------------------------------------

class TestEnsureConfigModules:

    def test_creates_config_when_not_exists(self, tmp_path):
        directory = tmp_path / "fresh"
        directory.mkdir()
        _ensure_config_modules(directory, "my_tool")
        data = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        assert data["modules"] == ["my_tool"]

    def test_idempotent(self, tmp_path):
        directory = tmp_path / "idem"
        directory.mkdir()
        _ensure_config_modules(directory, "my_tool")
        _ensure_config_modules(directory, "my_tool")
        data = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        assert data["modules"] == ["my_tool"]

    def test_preserves_existing_fields(self, tmp_path):
        directory = tmp_path / "preserve"
        directory.mkdir()
        config_path = directory / "config.json"
        config_path.write_text(
            json.dumps({"default_model": "main", "env": {"FOO": "1"}}),
            encoding="utf-8",
        )
        _ensure_config_modules(directory, "new_mod")
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["default_model"] == "main"
        assert data["env"] == {"FOO": "1"}
        assert data["modules"] == ["new_mod"]

    def test_malformed_config_handled(self, tmp_path):
        directory = tmp_path / "malformed"
        directory.mkdir()
        (directory / "config.json").write_text("NOT VALID JSON{{{", encoding="utf-8")
        _ensure_config_modules(directory, "recovered")
        data = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        assert data["modules"] == ["recovered"]


# -- Integration: save_module with config update -------------------------

class TestSaveModuleConfig:

    def test_toolkit_module_added_to_config(self, tools, tmp_path):
        source = (
            "import mutagent\n\n"
            "class SaveTK(mutagent.Toolkit):\n"
            "    def hello(self) -> str:\n"
            "        '''Say hello.'''\n"
            "        return 'hi'\n"
        )
        tools.module_manager.patch_module("save_tk_test", source)
        result = tools.save("save_tk_test")
        assert "OK" in result

        config_path = tmp_path / ".mutagent" / "config.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert "save_tk_test" in data["modules"]

    def test_non_toolkit_module_not_added(self, tools, tmp_path):
        tools.module_manager.patch_module("plain_mod", "val = 99\n")
        result = tools.save("plain_mod")
        assert "OK" in result

        config_path = tmp_path / ".mutagent" / "config.json"
        # Config should not be created for non-toolkit modules
        assert not config_path.exists()

    def test_duplicate_not_added(self, tools, tmp_path):
        source = (
            "import mutagent\n\n"
            "class DupTK(mutagent.Toolkit):\n"
            "    def ping(self) -> str:\n"
            "        '''Ping.'''\n"
            "        return 'pong'\n"
        )
        tools.module_manager.patch_module("dup_tk", source)
        tools.save("dup_tk")
        # Save again (re-patch to bump version so save_module succeeds)
        tools.module_manager.patch_module("dup_tk", source)
        tools.save("dup_tk")

        config_path = tmp_path / ".mutagent" / "config.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["modules"].count("dup_tk") == 1

    def test_preserves_existing_config_fields(self, tools, tmp_path):
        # Pre-create config with existing fields
        config_dir = tmp_path / ".mutagent"
        config_dir.mkdir(parents=True)
        config_path = config_dir / "config.json"
        config_path.write_text(
            json.dumps({"default_model": "main", "modules": ["existing_mod"]}),
            encoding="utf-8",
        )

        source = (
            "import mutagent\n\n"
            "class PreserveTK(mutagent.Toolkit):\n"
            "    def noop(self) -> str:\n"
            "        '''No-op.'''\n"
            "        return ''\n"
        )
        tools.module_manager.patch_module("preserve_tk", source)
        tools.save("preserve_tk")

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["default_model"] == "main"
        assert "existing_mod" in data["modules"]
        assert "preserve_tk" in data["modules"]

    def test_user_level_updates_user_config(self, tools, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: tmp_path))
        source = (
            "import mutagent\n\n"
            "class UserTK(mutagent.Toolkit):\n"
            "    def greet(self) -> str:\n"
            "        '''Greet.'''\n"
            "        return 'hello'\n"
        )
        tools.module_manager.patch_module("user_tk", source)
        result = tools.save("user_tk", level="user")
        assert "OK" in result

        config_path = tmp_path / ".mutagent" / "config.json"
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert "user_tk" in data["modules"]

    def test_malformed_config_handled_gracefully(self, tools, tmp_path):
        config_dir = tmp_path / ".mutagent"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text("<<<BROKEN>>>", encoding="utf-8")

        source = (
            "import mutagent\n\n"
            "class MalTK(mutagent.Toolkit):\n"
            "    def x(self) -> str:\n"
            "        '''X.'''\n"
            "        return 'x'\n"
        )
        tools.module_manager.patch_module("mal_tk", source)
        result = tools.save("mal_tk")
        assert "OK" in result

        data = json.loads(
            (config_dir / "config.json").read_text(encoding="utf-8")
        )
        assert "mal_tk" in data["modules"]
