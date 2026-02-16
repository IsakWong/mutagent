"""Tests for ModuleManager runtime module patching."""

import inspect
import sys

import pytest
import mutagent
from mutagent.runtime.module_manager import ModuleManager


@pytest.fixture
def mgr():
    manager = ModuleManager()
    yield manager
    manager.cleanup()


class TestPatchModule:

    def test_patch_creates_module(self, mgr):
        mod = mgr.patch_module("test_pkg.mymod", "x = 42\n")
        assert "test_pkg.mymod" in sys.modules
        assert mod.x == 42

    def test_patch_sets_module_attributes(self, mgr):
        mod = mgr.patch_module("test_pkg.attrs", "pass\n")
        assert mod.__name__ == "test_pkg.attrs"
        assert mod.__file__ == "mutagent://test_pkg.attrs"
        assert mod.__package__ == "test_pkg"

    def test_patch_executes_source(self, mgr):
        source = "a = 1\nb = 2\nc = a + b\n"
        mod = mgr.patch_module("test_pkg.calc", source)
        assert mod.c == 3

    def test_repatch_clears_old_namespace(self, mgr):
        mgr.patch_module("test_pkg.repatch", "x = 1\n")
        mod = mgr.patch_module("test_pkg.repatch", "y = 2\n")
        assert mod.y == 2
        assert not hasattr(mod, "x")

    def test_repatch_unregisters_old_impls(self, mgr):
        source1 = (
            "import mutagent\n"
            "class Svc(mutagent.Declaration):\n"
            "    def run(self) -> str: ...\n"
        )
        mgr.patch_module("test_pkg.svc_decl", source1)

        source_impl = (
            "import mutagent\n"
            "from test_pkg.svc_decl import Svc\n"
            "@mutagent.impl(Svc.run)\n"
            "def run(self) -> str:\n"
            "    return 'v1'\n"
        )
        mgr.patch_module("test_pkg.svc_impl", source_impl)

        mod_decl = sys.modules["test_pkg.svc_decl"]
        obj = mod_decl.Svc()
        assert obj.run() == "v1"

        # Repatch impl with different implementation
        source_impl2 = (
            "import mutagent\n"
            "from test_pkg.svc_decl import Svc\n"
            "@mutagent.impl(Svc.run)\n"
            "def run(self) -> str:\n"
            "    return 'v2'\n"
        )
        mgr.patch_module("test_pkg.svc_impl", source_impl2)
        assert obj.run() == "v2"

    def test_repatch_without_impl_restores_default(self, mgr):
        """When an impl module is repatched without @impl, method reverts to default impl."""
        source_decl = (
            "import mutagent\n"
            "class Worker(mutagent.Declaration):\n"
            "    def do_work(self) -> str: ...\n"
        )
        mgr.patch_module("test_pkg.worker_decl", source_decl)

        source_impl = (
            "import mutagent\n"
            "from test_pkg.worker_decl import Worker\n"
            "@mutagent.impl(Worker.do_work)\n"
            "def do_work(self) -> str:\n"
            "    return 'done'\n"
        )
        mgr.patch_module("test_pkg.worker_impl", source_impl)

        mod_decl = sys.modules["test_pkg.worker_decl"]
        obj = mod_decl.Worker()
        assert obj.do_work() == "done"

        # Repatch impl module with code that does NOT register any @impl
        mgr.patch_module("test_pkg.worker_impl", "# impl removed\n")

        # Method should revert to default impl (the original `...` body returns None)
        assert obj.do_work() is None

    def test_parent_packages_created(self, mgr):
        mgr.patch_module("a.b.c.deep", "val = True\n")
        assert "a" in sys.modules
        assert "a.b" in sys.modules
        assert "a.b.c" in sys.modules
        assert hasattr(sys.modules["a"], "__path__")

    def test_attach_to_parent(self, mgr):
        mgr.patch_module("test_pkg.child", "Z = 99\n")
        parent = sys.modules.get("test_pkg")
        assert parent is not None
        assert hasattr(parent, "child")
        assert parent.child.Z == 99


class TestInspectIntegration:

    def test_inspect_getsource_function(self, mgr):
        source = "def hello():\n    return 'world'\n"
        mod = mgr.patch_module("test_pkg.src", source)
        got = inspect.getsource(mod.hello)
        assert "return 'world'" in got

    def test_inspect_getsource_class(self, mgr):
        source = (
            "import mutagent\n"
            "class MyClass(mutagent.Declaration):\n"
            "    name: str\n"
            "    def greet(self) -> str: ...\n"
        )
        mod = mgr.patch_module("test_pkg.clsrc", source)
        got = inspect.getsource(mod.MyClass)
        assert "class MyClass" in got

    def test_loader_get_source(self, mgr):
        source = "x = 1\n"
        mod = mgr.patch_module("test_pkg.ldr", source)
        assert mod.__loader__.get_source("test_pkg.ldr") == source

    def test_virtual_filename_in_code(self, mgr):
        source = "def f():\n    pass\n"
        mod = mgr.patch_module("test_pkg.vfn", source)
        assert mod.f.__code__.co_filename == "mutagent://test_pkg.vfn"


class TestHistoryAndVersioning:

    def test_version_increments(self, mgr):
        assert mgr.get_version("test_pkg.ver") == 0
        mgr.patch_module("test_pkg.ver", "v = 1\n")
        assert mgr.get_version("test_pkg.ver") == 1
        mgr.patch_module("test_pkg.ver", "v = 2\n")
        assert mgr.get_version("test_pkg.ver") == 2

    def test_get_source_returns_latest(self, mgr):
        mgr.patch_module("test_pkg.gsrc", "a = 1\n")
        mgr.patch_module("test_pkg.gsrc", "b = 2\n")
        assert mgr.get_source("test_pkg.gsrc") == "b = 2\n"

    def test_get_source_unpatched_returns_none(self, mgr):
        assert mgr.get_source("nonexistent.module") is None

    def test_get_history(self, mgr):
        mgr.patch_module("test_pkg.hist", "v1 = True\n")
        mgr.patch_module("test_pkg.hist", "v2 = True\n")
        mgr.patch_module("test_pkg.hist", "v3 = True\n")
        history = mgr.get_history("test_pkg.hist")
        assert len(history) == 3
        assert history[0].version == 1
        assert history[2].version == 3
        assert history[2].source == "v3 = True\n"


class TestDeclarationMetaIntegration:

    def test_inplace_class_update_via_repatch(self, mgr):
        source1 = (
            "import mutagent\n"
            "class Agent(mutagent.Declaration):\n"
            "    name: str\n"
            "    def run(self) -> str: ...\n"
        )
        mod = mgr.patch_module("test_pkg.agent", source1)
        cls1 = mod.Agent
        id1 = id(cls1)
        obj = cls1(name="test")

        source2 = (
            "import mutagent\n"
            "class Agent(mutagent.Declaration):\n"
            "    name: str\n"
            "    version: int\n"
            "    def run(self) -> str: ...\n"
            "    def stop(self) -> None: ...\n"
        )
        mod = mgr.patch_module("test_pkg.agent", source2)
        cls2 = mod.Agent

        assert cls1 is cls2
        assert id(cls2) == id1
        assert isinstance(obj, cls2)


class TestCleanup:

    def test_cleanup_removes_modules(self, mgr):
        mgr.patch_module("test_pkg.clean1", "x = 1\n")
        mgr.patch_module("test_pkg.clean2", "y = 2\n")
        assert "test_pkg.clean1" in sys.modules
        assert "test_pkg.clean2" in sys.modules

        mgr.cleanup()
        assert "test_pkg.clean1" not in sys.modules
        assert "test_pkg.clean2" not in sys.modules

    def test_cleanup_resets_state(self, mgr):
        mgr.patch_module("test_pkg.rst", "z = 3\n")
        mgr.cleanup()
        assert mgr.get_version("test_pkg.rst") == 0
        assert mgr.get_source("test_pkg.rst") is None
        assert mgr.get_history("test_pkg.rst") == []


class TestUnsavedTracking:

    def test_get_unsaved_modules_initially_empty(self, mgr):
        assert mgr.get_unsaved_modules() == []

    def test_patched_module_is_unsaved(self, mgr):
        mgr.patch_module("test_pkg.unsaved1", "x = 1\n")
        assert "test_pkg.unsaved1" in mgr.get_unsaved_modules()

    def test_saved_module_removed_from_unsaved(self, mgr, tmp_path):
        mgr.patch_module("test_pkg.saved1", "x = 1\n")
        mgr.save_module("test_pkg.saved1", tmp_path)
        assert "test_pkg.saved1" not in mgr.get_unsaved_modules()

    def test_multiple_unsaved_modules(self, mgr):
        mgr.patch_module("test_pkg.ua", "a = 1\n")
        mgr.patch_module("test_pkg.ub", "b = 2\n")
        unsaved = mgr.get_unsaved_modules()
        assert "test_pkg.ua" in unsaved
        assert "test_pkg.ub" in unsaved

    def test_save_one_of_many(self, mgr, tmp_path):
        mgr.patch_module("test_pkg.sa", "a = 1\n")
        mgr.patch_module("test_pkg.sb", "b = 2\n")
        mgr.save_module("test_pkg.sa", tmp_path)
        unsaved = mgr.get_unsaved_modules()
        assert "test_pkg.sa" not in unsaved
        assert "test_pkg.sb" in unsaved

    def test_cleanup_resets_saved_paths(self, mgr, tmp_path):
        mgr.patch_module("test_pkg.cr", "x = 1\n")
        mgr.save_module("test_pkg.cr", tmp_path)
        mgr.cleanup()
        assert mgr.get_unsaved_modules() == []


class TestNamespacePackages:

    def test_search_dirs_build_package_path(self, tmp_path):
        # Create directory structure
        dir1 = tmp_path / "level1"
        dir2 = tmp_path / "level2"
        (dir1 / "utils").mkdir(parents=True)
        (dir2 / "utils").mkdir(parents=True)

        mgr = ModuleManager(search_dirs=[dir1, dir2])
        paths = mgr._build_package_path("utils")
        assert str(dir1 / "utils") in paths
        assert str(dir2 / "utils") in paths
        mgr.cleanup()

    def test_search_dirs_nonexistent_skipped(self, tmp_path):
        dir1 = tmp_path / "exists"
        (dir1 / "pkg").mkdir(parents=True)

        mgr = ModuleManager(search_dirs=[dir1, tmp_path / "nonexistent"])
        paths = mgr._build_package_path("pkg")
        assert len(paths) == 1
        assert str(dir1 / "pkg") in paths
        mgr.cleanup()

    def test_virtual_package_gets_search_dir_paths(self, tmp_path):
        dir1 = tmp_path / "level1"
        (dir1 / "mypkg").mkdir(parents=True)

        mgr = ModuleManager(search_dirs=[dir1])
        mgr.patch_module("mypkg.mod", "x = 1\n")

        pkg = sys.modules.get("mypkg")
        assert pkg is not None
        assert str(dir1 / "mypkg") in pkg.__path__
        mgr.cleanup()

    def test_no_search_dirs_empty_path(self):
        mgr = ModuleManager()
        mgr.patch_module("nsd_pkg.mod", "x = 1\n")

        pkg = sys.modules.get("nsd_pkg")
        assert pkg is not None
        assert pkg.__path__ == []
        mgr.cleanup()

    def test_nested_package_path(self, tmp_path):
        dir1 = tmp_path / "root"
        (dir1 / "a" / "b").mkdir(parents=True)

        mgr = ModuleManager(search_dirs=[dir1])
        mgr.patch_module("a.b.c", "val = 1\n")

        pkg_a = sys.modules.get("a")
        pkg_ab = sys.modules.get("a.b")
        assert pkg_a is not None
        assert str(dir1 / "a") in pkg_a.__path__
        assert pkg_ab is not None
        assert str(dir1 / "a" / "b") in pkg_ab.__path__
        mgr.cleanup()
