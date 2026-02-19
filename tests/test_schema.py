"""Tests for mutagent.builtins.schema -- parse_docstring, make_schema, get_declaration_method."""

import inspect

import pytest

import mutagent
from mutagent.builtins.schema import get_declaration_method, make_schema, parse_docstring
from mutagent.messages import ToolSchema

import mutagent.builtins  # noqa: F401  -- register all @impl


# ---------------------------------------------------------------------------
# parse_docstring tests
# ---------------------------------------------------------------------------

class TestParseDocstring:

    def test_none_returns_empty(self):
        desc, params = parse_docstring(None)
        assert desc == ""
        assert params == {}

    def test_empty_string_returns_empty(self):
        desc, params = parse_docstring("")
        assert desc == ""
        assert params == {}

    def test_single_line_description(self):
        desc, params = parse_docstring("Do something useful.")
        assert desc == "Do something useful."
        assert params == {}

    def test_description_and_args(self):
        doc = """Inspect the structure of a Python module.

        Args:
            module_path: Dotted module path (e.g. "mutagent.essential_tools").
                Empty string lists top-level mutagent modules.
            depth: How deep to expand sub-modules/classes. Default 2.

        Returns:
            A formatted string showing the module structure.
        """
        desc, params = parse_docstring(doc)
        assert desc == "Inspect the structure of a Python module."
        assert "module_path" in params
        assert "mutagent.essential_tools" in params["module_path"]
        assert "Empty string" in params["module_path"]
        assert "depth" in params
        assert "How deep" in params["depth"]

    def test_multiline_param_description(self):
        doc = """Do something.

        Args:
            source: Python source code for the module.
                This can be multiple lines long and should
                be properly concatenated.
            name: Simple param.
        """
        desc, params = parse_docstring(doc)
        assert "source" in params
        assert "Python source code" in params["source"]
        assert "multiple lines" in params["source"]
        assert "properly concatenated" in params["source"]
        assert params["name"] == "Simple param."

    def test_no_args_section(self):
        doc = """A tool with no parameters.

        Returns:
            Some result.
        """
        desc, params = parse_docstring(doc)
        assert desc == "A tool with no parameters."
        assert params == {}

    def test_args_with_type_annotation(self):
        doc = """A tool.

        Args:
            name (str): The person to greet.
            count (int): How many times.
        """
        desc, params = parse_docstring(doc)
        assert params["name"] == "The person to greet."
        assert params["count"] == "How many times."

    def test_query_logs_docstring(self):
        """Test with actual query_logs docstring from essential_tools.py."""
        doc = """Query log entries or configure logging.

        Args:
            pattern: Regex pattern to search in log messages. Empty matches all.
            level: Minimum log level filter (DEBUG/INFO/WARNING/ERROR).
            limit: Maximum number of entries to return.
            tool_capture: Set to "on" or "off" to enable/disable tool log
                capture (logs appended to tool output). Empty string = no change.

        Returns:
            Formatted log entries, newest first.
        """
        desc, params = parse_docstring(doc)
        assert desc == "Query log entries or configure logging."
        assert len(params) == 4
        assert "pattern" in params
        assert "level" in params
        assert "limit" in params
        assert "tool_capture" in params
        # tool_capture has continuation line
        assert "on" in params["tool_capture"]
        assert "capture" in params["tool_capture"]


# ---------------------------------------------------------------------------
# make_schema tests
# ---------------------------------------------------------------------------

class TestMakeSchema:

    def test_simple_function(self):
        def greet(name: str) -> str:
            """Say hello.

            Args:
                name: The person to greet.
            """
            return f"Hello, {name}!"

        schema = make_schema(greet)
        assert isinstance(schema, ToolSchema)
        assert schema.name == "greet"
        assert schema.description == "Say hello."
        assert "name" in schema.input_schema["properties"]
        assert schema.input_schema["properties"]["name"]["type"] == "string"
        assert schema.input_schema["properties"]["name"]["description"] == "The person to greet."
        assert schema.input_schema["required"] == ["name"]

    def test_function_with_defaults(self):
        def search(query: str, limit: int = 10) -> str:
            """Search for items.

            Args:
                query: Search query string.
                limit: Max results to return.
            """
            return ""

        schema = make_schema(search)
        assert schema.input_schema["required"] == ["query"]
        assert schema.input_schema["properties"]["limit"]["default"] == 10

    def test_type_mapping(self):
        def func(a: str, b: int, c: float, d: bool):
            """Test types."""
            pass

        schema = make_schema(func)
        props = schema.input_schema["properties"]
        assert props["a"]["type"] == "string"
        assert props["b"]["type"] == "integer"
        assert props["c"]["type"] == "number"
        assert props["d"]["type"] == "boolean"

    def test_self_excluded(self):
        class Foo:
            def bar(self, x: str) -> str:
                """Do bar."""
                return x

        schema = make_schema(Foo.bar)
        assert "self" not in schema.input_schema.get("properties", {})
        assert schema.input_schema["required"] == ["x"]

    def test_name_override(self):
        def internal_func(x: int) -> int:
            """Internal."""
            return x

        schema = make_schema(internal_func, name="public_name")
        assert schema.name == "public_name"

    def test_no_docstring(self):
        def bare(x: str) -> str:
            return x

        schema = make_schema(bare)
        assert schema.name == "bare"
        # Description falls back to function name
        assert schema.description == "bare"
        # Param description falls back to param name
        assert schema.input_schema["properties"]["x"]["description"] == "x"

    def test_no_annotations(self):
        def untyped(x, y="default"):
            """Untyped function."""
            pass

        schema = make_schema(untyped)
        assert schema.input_schema["properties"]["x"]["type"] == "string"
        assert schema.input_schema["properties"]["y"]["default"] == "default"


# ---------------------------------------------------------------------------
# get_declaration_method tests
# ---------------------------------------------------------------------------

class TestGetDeclarationMethod:

    def test_declaration_with_impl(self):
        """get_declaration_method returns the original stub, not the @impl replacement."""
        from mutagent.toolkits.module_toolkit import ModuleToolkit

        decl = get_declaration_method(ModuleToolkit, "define_module")
        # The declaration method should have the original docstring
        assert decl.__doc__ is not None
        assert "Define or redefine" in decl.__doc__

    def test_declaration_without_impl(self):
        """For classes without @impl, falls back to getattr."""
        class Plain(mutagent.Declaration):
            def my_method(self, x: str) -> str:
                """A plain method."""
                return x

        decl = get_declaration_method(Plain, "my_method")
        assert decl.__doc__ == "A plain method."

    def test_returns_original_signature(self):
        """Declaration method preserves original parameter annotations."""
        from mutagent.toolkits.module_toolkit import ModuleToolkit

        decl = get_declaration_method(ModuleToolkit, "inspect_module")
        sig = inspect.signature(decl)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "module_path" in params
        assert "depth" in params


# ---------------------------------------------------------------------------
# Integration: make_schema with get_declaration_method
# ---------------------------------------------------------------------------

class TestSchemaForToolkits:

    def test_inspect_module_schema(self):
        from mutagent.toolkits.module_toolkit import ModuleToolkit
        decl = get_declaration_method(ModuleToolkit, "inspect_module")
        schema = make_schema(decl, "inspect_module")

        assert schema.name == "inspect_module"
        assert "Inspect" in schema.description
        props = schema.input_schema["properties"]
        assert "module_path" in props
        assert "depth" in props
        # module_path has a real description from docstring, not just the param name
        assert len(props["module_path"]["description"]) > len("module_path")
        # Both have defaults, so no required params
        assert "required" not in schema.input_schema

    def test_define_module_schema(self):
        from mutagent.toolkits.module_toolkit import ModuleToolkit
        decl = get_declaration_method(ModuleToolkit, "define_module")
        schema = make_schema(decl, "define_module")

        assert schema.name == "define_module"
        props = schema.input_schema["properties"]
        assert "module_path" in props
        assert "source" in props
        assert schema.input_schema["required"] == ["module_path", "source"]

    def test_query_logs_schema(self):
        from mutagent.toolkits.log_toolkit import LogToolkit
        decl = get_declaration_method(LogToolkit, "query_logs")
        schema = make_schema(decl, "query_logs")

        assert schema.name == "query_logs"
        props = schema.input_schema["properties"]
        assert len(props) == 4
        assert "pattern" in props
        assert "level" in props
        assert "limit" in props
        assert "tool_capture" in props
        # All have defaults, no required params
        assert "required" not in schema.input_schema

    def test_module_toolkit_methods_have_rich_descriptions(self):
        """All ModuleToolkit methods should have param descriptions from docstrings."""
        from mutagent.toolkits.module_toolkit import ModuleToolkit
        for method_name in ["inspect_module", "view_source", "define_module", "save_module"]:
            decl = get_declaration_method(ModuleToolkit, method_name)
            schema = make_schema(decl, method_name)
            for pname, prop in schema.input_schema["properties"].items():
                # Description should be more than just the parameter name
                assert len(prop["description"]) > len(pname), (
                    f"{method_name}.{pname} has no rich description: {prop['description']!r}"
                )

    def test_delegate_schema(self):
        from mutagent.toolkits.agent_toolkit import AgentToolkit
        decl = get_declaration_method(AgentToolkit, "delegate")
        schema = make_schema(decl, "delegate")

        assert schema.name == "delegate"
        assert "Delegate" in schema.description
        props = schema.input_schema["properties"]
        assert "agent_name" in props
        assert "task" in props
        assert len(props["agent_name"]["description"]) > len("agent_name")
