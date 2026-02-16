"""Tests for EssentialTools method implementations."""

import sys
from pathlib import Path

import pytest

from mutagent.essential_tools import EssentialTools
from mutagent.runtime.module_manager import ModuleManager

import mutagent.builtins  # noqa: F401  -- register all @impl


@pytest.fixture
def tools():
    mgr = ModuleManager()
    t = EssentialTools(module_manager=mgr)
    yield t
    mgr.cleanup()


class TestInspectModule:

    def test_inspect_mutagent(self, tools):
        result = tools.inspect_module("mutagent")
        assert "mutagent/" in result

    def test_inspect_default_module(self, tools):
        result = tools.inspect_module()
        assert "mutagent/" in result

    def test_inspect_nonexistent_module(self, tools):
        result = tools.inspect_module("nonexistent.module.xyz")
        assert "not found" in result.lower()

    def test_inspect_shows_classes(self, tools):
        result = tools.inspect_module("mutagent.essential_tools", depth=2)
        assert "EssentialTools" in result

    def test_inspect_depth_limits_output(self, tools):
        result1 = tools.inspect_module("mutagent", depth=1)
        result2 = tools.inspect_module("mutagent", depth=3)
        # Deeper inspection should have more content
        assert len(result2) >= len(result1)


class TestViewSource:

    def test_view_module_source(self, tools):
        result = tools.view_source("mutagent.messages")
        assert "class Message" in result or "class ToolCall" in result

    def test_view_class_source(self, tools):
        result = tools.view_source("mutagent.essential_tools.EssentialTools")
        assert "class EssentialTools" in result

    def test_view_patched_module(self, tools):
        tools.module_manager.patch_module(
            "test_view.patched", "def hello():\n    return 'world'\n"
        )
        result = tools.view_source("test_view.patched.hello")
        assert "return 'world'" in result

    def test_view_nonexistent_target(self, tools):
        result = tools.view_source("nonexistent.module.xyz")
        assert "Error" in result


class TestDefineModule:

    def test_define_creates_module(self, tools):
        result = tools.define_module("test_tool_patch.mod1", "x = 42\n")
        assert "OK" in result
        assert "test_tool_patch.mod1" in result
        assert sys.modules["test_tool_patch.mod1"].x == 42

    def test_define_reports_version(self, tools):
        tools.define_module("test_tool_patch.ver", "v = 1\n")
        result = tools.define_module("test_tool_patch.ver", "v = 2\n")
        assert "v2" in result

    def test_define_syntax_error(self, tools):
        result = tools.define_module("test_tool_patch.bad", "def f(\n")
        assert "Error" in result


class TestSaveModule:

    def test_save_module_project_level(self, tools, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        tools.module_manager.patch_module("test_tool_save.mod", "val = 99\n")
        result = tools.save_module("test_tool_save.mod")
        assert "OK" in result

        # Verify file was written to .mutagent/ under project dir
        saved_file = tmp_path / ".mutagent" / "test_tool_save" / "mod.py"
        assert saved_file.exists()
        assert saved_file.read_text() == "val = 99\n"

    def test_save_module_user_level(self, tools, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: tmp_path))
        tools.module_manager.patch_module("test_tool_save.umod", "val = 42\n")
        result = tools.save_module("test_tool_save.umod", level="user")
        assert "OK" in result

        saved_file = tmp_path / ".mutagent" / "test_tool_save" / "umod.py"
        assert saved_file.exists()
        assert saved_file.read_text() == "val = 42\n"

    def test_save_unpatched_module(self, tools):
        result = tools.save_module("nonexistent.module")
        assert "Error" in result

    def test_save_unknown_level(self, tools):
        tools.module_manager.patch_module("test_tool_save.bad_level", "x = 1\n")
        result = tools.save_module("test_tool_save.bad_level", level="invalid")
        assert "Error" in result
        assert "unknown level" in result

    def test_save_does_not_create_init_py(self, tools, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        tools.module_manager.patch_module("pkg.sub.mod", "x = 1\n")
        tools.save_module("pkg.sub.mod")

        # Parent dirs should exist but no __init__.py
        assert (tmp_path / ".mutagent" / "pkg" / "sub").is_dir()
        assert not (tmp_path / ".mutagent" / "pkg" / "__init__.py").exists()
        assert not (tmp_path / ".mutagent" / "pkg" / "sub" / "__init__.py").exists()

    def test_save_auto_creates_mutagent_dir(self, tools, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert not (tmp_path / ".mutagent").exists()

        tools.module_manager.patch_module("auto_create.mod", "y = 2\n")
        result = tools.save_module("auto_create.mod")
        assert "OK" in result
        assert (tmp_path / ".mutagent").is_dir()


class TestUnsavedModules:

    def test_inspect_shows_unsaved_modules(self, tools):
        tools.define_module("unsaved_test.mod1", "x = 1\n")
        tools.define_module("unsaved_test.mod2", "y = 2\n")

        result = tools.inspect_module()
        assert "[Unsaved modules]" in result
        assert "unsaved_test.mod1" in result
        assert "unsaved_test.mod2" in result

    def test_inspect_no_unsaved_when_all_saved(self, tools, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        tools.define_module("saved_test.mod1", "x = 1\n")
        tools.save_module("saved_test.mod1")

        result = tools.inspect_module()
        assert "[Unsaved modules]" not in result

    def test_define_then_save_removes_from_unsaved(self, tools, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        tools.define_module("track_test.mod", "z = 3\n")

        # Before save: should be unsaved
        unsaved = tools.module_manager.get_unsaved_modules()
        assert "track_test.mod" in unsaved

        # After save: should no longer be unsaved
        tools.save_module("track_test.mod")
        unsaved = tools.module_manager.get_unsaved_modules()
        assert "track_test.mod" not in unsaved

    def test_inspect_shows_version_for_unsaved(self, tools):
        tools.define_module("ver_test.mod", "v = 1\n")
        tools.define_module("ver_test.mod", "v = 2\n")

        result = tools.inspect_module()
        assert "ver_test.mod (v2)" in result


