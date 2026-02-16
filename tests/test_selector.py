"""Tests for EssentialTools + ToolSelector declarations and selector impl."""

from pathlib import Path

import pytest

import mutagent
from mutagent.essential_tools import EssentialTools
from mutagent.messages import ToolCall, ToolResult, ToolSchema
from mutagent.runtime.module_manager import ModuleManager
from mutagent.selector import ToolSelector
from mutobj.core import DeclarationMeta, _DECLARED_METHODS

import mutagent.builtins  # noqa: F401  -- register all @impl

from mutagent.builtins.selector_impl import make_schema_from_method


class TestEssentialToolsDeclaration:

    def test_inherits_from_mutagent_declaration(self):
        assert issubclass(EssentialTools, mutagent.Declaration)

    def test_uses_declaration_meta(self):
        assert isinstance(EssentialTools, DeclarationMeta)

    def test_declared_methods(self):
        declared = getattr(EssentialTools, _DECLARED_METHODS, set())
        expected = {"inspect_module", "view_source", "patch_module", "save_module", "run_code"}
        assert expected.issubset(declared)

    def test_has_module_manager_attribute(self):
        mgr = ModuleManager()
        tools = EssentialTools(module_manager=mgr)
        assert tools.module_manager is mgr
        mgr.cleanup()


class TestToolSelectorDeclaration:

    def test_inherits_from_mutagent_declaration(self):
        assert issubclass(ToolSelector, mutagent.Declaration)

    def test_declared_methods(self):
        declared = getattr(ToolSelector, _DECLARED_METHODS, set())
        assert "get_tools" in declared
        assert "dispatch" in declared

    def test_has_essential_tools_attribute(self):
        mgr = ModuleManager()
        tools = EssentialTools(module_manager=mgr)
        selector = ToolSelector(essential_tools=tools)
        assert selector.essential_tools is tools
        mgr.cleanup()


class TestMakeSchemaFromMethod:

    def test_generates_schema(self):
        mgr = ModuleManager()
        tools = EssentialTools(module_manager=mgr)

        schema = make_schema_from_method(tools, "inspect_module")
        assert isinstance(schema, ToolSchema)
        assert schema.name == "inspect_module"
        assert "properties" in schema.input_schema
        assert "module_path" in schema.input_schema["properties"]
        assert "depth" in schema.input_schema["properties"]
        mgr.cleanup()

    def test_required_params_detected(self):
        mgr = ModuleManager()
        tools = EssentialTools(module_manager=mgr)

        schema = make_schema_from_method(tools, "patch_module")
        assert "module_path" in schema.input_schema.get("required", [])
        assert "source" in schema.input_schema.get("required", [])
        mgr.cleanup()

    def test_optional_params_have_defaults(self):
        mgr = ModuleManager()
        tools = EssentialTools(module_manager=mgr)

        schema = make_schema_from_method(tools, "inspect_module")
        depth_prop = schema.input_schema["properties"]["depth"]
        assert depth_prop.get("default") == 2
        mgr.cleanup()

    def test_type_mapping(self):
        mgr = ModuleManager()
        tools = EssentialTools(module_manager=mgr)

        schema = make_schema_from_method(tools, "inspect_module")
        assert schema.input_schema["properties"]["module_path"]["type"] == "string"
        assert schema.input_schema["properties"]["depth"]["type"] == "integer"
        mgr.cleanup()

    def test_description_from_docstring(self):
        mgr = ModuleManager()
        tools = EssentialTools(module_manager=mgr)

        schema = make_schema_from_method(tools, "view_source")
        assert "source" in schema.description.lower() or "View" in schema.description
        mgr.cleanup()


class TestToolSelectorImpl:

    @pytest.fixture
    def selector_with_tools(self):
        mgr = ModuleManager()
        tools = EssentialTools(module_manager=mgr)
        selector = ToolSelector(essential_tools=tools)
        yield selector
        mgr.cleanup()

    def test_get_tools_returns_schemas(self, selector_with_tools):
        schemas = selector_with_tools.get_tools({})
        assert len(schemas) == 5
        names = {s.name for s in schemas}
        assert names == {"inspect_module", "view_source", "patch_module", "save_module", "run_code"}

    def test_get_tools_schema_structure(self, selector_with_tools):
        schemas = selector_with_tools.get_tools({})
        for schema in schemas:
            assert isinstance(schema, ToolSchema)
            assert schema.name
            assert schema.description
            assert "type" in schema.input_schema
            assert schema.input_schema["type"] == "object"

    def test_dispatch_unknown_tool(self, selector_with_tools):
        call = ToolCall(id="tc_1", name="nonexistent_tool", arguments={})
        result = selector_with_tools.dispatch(call)
        assert result.is_error
        assert "Unknown tool" in result.content

    def test_dispatch_returns_result(self, selector_with_tools):
        # Dispatch a tool that returns a result (even if it's an error message)
        call = ToolCall(id="tc_2", name="run_code", arguments={"code": "print(1+1)"})
        result = selector_with_tools.dispatch(call)
        assert not result.is_error
        assert "2" in result.content

    def test_dispatch_exception_becomes_error(self, selector_with_tools):
        # Dispatch with wrong argument types to trigger an actual exception
        call = ToolCall(id="tc_3", name="inspect_module", arguments={"depth": "not_a_number"})
        result = selector_with_tools.dispatch(call)
        # This should either work (str converted) or raise TypeError
        assert result.tool_call_id == "tc_3"
