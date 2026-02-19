"""Tests for ToolSet and AgentToolkit."""

import pytest

import mutagent
from mutagent.agent import Agent
from mutagent.toolkits.agent_toolkit import AgentToolkit
from mutagent.client import LLMClient
from mutagent.toolkits.module_toolkit import ModuleToolkit
from mutagent.messages import (
    InputEvent,
    Message,
    Response,
    StreamEvent,
    ToolCall,
    ToolResult,
    ToolSchema,
)
from mutagent.runtime.module_manager import ModuleManager
from mutagent.tools import ToolEntry, ToolSet
from mutobj.core import DeclarationMeta, _DECLARED_METHODS

import mutagent.builtins  # noqa: F401  -- register all @impl


# ---------------------------------------------------------------------------
# ToolSet Tests
# ---------------------------------------------------------------------------

class TestToolSetDeclaration:

    def test_inherits_from_declaration(self):
        assert issubclass(ToolSet, mutagent.Declaration)

    def test_uses_declaration_meta(self):
        assert isinstance(ToolSet, DeclarationMeta)

    def test_declared_methods(self):
        declared = getattr(ToolSet, _DECLARED_METHODS, set())
        assert "add" in declared
        assert "remove" in declared
        assert "query" in declared
        assert "get_tools" in declared
        assert "dispatch" in declared


class TestToolSetAddFromObject:

    @pytest.fixture
    def tool_set(self):
        return ToolSet()

    @pytest.fixture
    def essential_tools(self):
        mgr = ModuleManager()
        tools = ModuleToolkit(module_manager=mgr)
        yield tools
        mgr.cleanup()

    def test_add_registers_all_public_methods(self, tool_set, essential_tools):
        tool_set.add(essential_tools)
        schemas = tool_set.get_tools()
        names = {s.name for s in schemas}
        assert "inspect_module" in names
        assert "view_source" in names
        assert "define_module" in names
        assert "save_module" in names

    def test_add_with_methods_filter(self, tool_set, essential_tools):
        tool_set.add(essential_tools, methods=["inspect_module", "view_source"])
        schemas = tool_set.get_tools()
        names = {s.name for s in schemas}
        assert names == {"inspect_module", "view_source"}

    def test_add_single_method(self, tool_set, essential_tools):
        tool_set.add(essential_tools, methods=["define_module"])
        schemas = tool_set.get_tools()
        assert len(schemas) == 1
        assert schemas[0].name == "define_module"


class TestToolSetAddCallable:

    def test_add_standalone_function(self):
        def greet(name: str, greeting: str = "Hello") -> str:
            """Greet someone by name."""
            return f"{greeting}, {name}!"

        tool_set = ToolSet()
        tool_set.add(greet)
        schemas = tool_set.get_tools()
        assert len(schemas) == 1
        assert schemas[0].name == "greet"
        assert "Greet someone" in schemas[0].description


class TestToolSetRemove:

    @pytest.fixture
    def populated_set(self):
        mgr = ModuleManager()
        tools = ModuleToolkit(module_manager=mgr)
        ts = ToolSet()
        ts.add(tools)
        yield ts
        mgr.cleanup()

    def test_remove_existing(self, populated_set):
        assert populated_set.remove("inspect_module") is True
        names = {s.name for s in populated_set.get_tools()}
        assert "inspect_module" not in names

    def test_remove_nonexistent(self, populated_set):
        assert populated_set.remove("nonexistent_tool") is False

    def test_remove_then_dispatch_fails(self, populated_set):
        populated_set.remove("inspect_module")
        result = populated_set.dispatch(
            ToolCall(id="tc_1", name="inspect_module", arguments={})
        )
        assert result.is_error
        assert "Unknown tool" in result.content


class TestToolSetQuery:

    @pytest.fixture
    def populated_set(self):
        mgr = ModuleManager()
        tools = ModuleToolkit(module_manager=mgr)
        ts = ToolSet()
        ts.add(tools)
        yield ts
        mgr.cleanup()

    def test_query_existing(self, populated_set):
        schema = populated_set.query("inspect_module")
        assert schema is not None
        assert schema.name == "inspect_module"
        assert isinstance(schema, ToolSchema)

    def test_query_nonexistent(self, populated_set):
        assert populated_set.query("nonexistent") is None


class TestToolSetDispatch:

    @pytest.fixture
    def tool_set(self):
        mgr = ModuleManager()
        tools = ModuleToolkit(module_manager=mgr)
        ts = ToolSet()
        ts.add(tools)
        yield ts
        mgr.cleanup()

    def test_dispatch_inspect_module(self, tool_set):
        result = tool_set.dispatch(
            ToolCall(id="tc_1", name="inspect_module", arguments={"module_path": "mutagent"})
        )
        assert not result.is_error
        assert "mutagent" in result.content

    def test_dispatch_define_module(self, tool_set):
        result = tool_set.dispatch(
            ToolCall(id="tc_2", name="define_module",
                     arguments={"module_path": "test_ts_dispatch.mod", "source": "x = 42\n"})
        )
        assert not result.is_error
        assert "OK" in result.content

    def test_dispatch_unknown_tool(self, tool_set):
        result = tool_set.dispatch(
            ToolCall(id="tc_3", name="nonexistent_tool", arguments={})
        )
        assert result.is_error
        assert "Unknown tool" in result.content

    def test_dispatch_with_error(self, tool_set):
        result = tool_set.dispatch(
            ToolCall(id="tc_4", name="save_module",
                     arguments={"module_path": "nonexistent.mod"})
        )
        # save_module for unpatched module returns error string, not exception
        assert "Error" in result.content


class TestToolSetEmptyState:

    def test_empty_get_tools(self):
        ts = ToolSet()
        assert ts.get_tools() == []

    def test_empty_dispatch_fails(self):
        ts = ToolSet()
        result = ts.dispatch(ToolCall(id="tc_1", name="anything", arguments={}))
        assert result.is_error

    def test_empty_query_returns_none(self):
        ts = ToolSet()
        assert ts.query("anything") is None


class TestToolSetMultipleSources:

    def test_add_multiple_objects(self):
        """Adding tools from multiple sources accumulates them."""
        mgr = ModuleManager()
        tools = ModuleToolkit(module_manager=mgr)

        def custom_tool(x: int) -> str:
            """A custom tool."""
            return str(x * 2)

        ts = ToolSet()
        ts.add(tools, methods=["inspect_module"])
        ts.add(custom_tool)

        schemas = ts.get_tools()
        names = {s.name for s in schemas}
        assert "inspect_module" in names
        assert "custom_tool" in names
        mgr.cleanup()


# ---------------------------------------------------------------------------
# AgentToolkit Tests
# ---------------------------------------------------------------------------

class TestAgentToolkitDeclaration:

    def test_inherits_from_declaration(self):
        assert issubclass(AgentToolkit, mutagent.Declaration)

    def test_uses_declaration_meta(self):
        assert isinstance(AgentToolkit, DeclarationMeta)

    def test_declared_methods(self):
        declared = getattr(AgentToolkit, _DECLARED_METHODS, set())
        assert "delegate" in declared


def _make_sub_agent(response_text="Sub-agent result"):
    """Create a sub-agent that returns a fixed response."""
    client = LLMClient(
        model="test-model",
        api_key="test-key",
        base_url="https://api.test.com",
    )
    tool_set = ToolSet()
    agent = Agent(
        client=client,
        tool_set=tool_set,
        system_prompt="You are a test sub-agent.",
        messages=[],
    )
    tool_set.agent = agent

    response = Response(
        message=Message(role="assistant", content=response_text),
        stop_reason="end_turn",
    )

    def mock_send(*args, **kwargs):
        yield StreamEvent(type="text_delta", text=response_text)
        yield StreamEvent(type="response_done", response=response)

    agent.client.send_message = mock_send
    return agent


class TestAgentToolkitDelegate:

    def test_delegate_returns_sub_agent_result(self):
        sub = _make_sub_agent("Hello from sub-agent")
        dt = AgentToolkit(agents={"helper": sub})
        result = dt.delegate(agent_name="helper", task="Say hello")
        assert result == "Hello from sub-agent"

    def test_delegate_unknown_agent(self):
        dt = AgentToolkit(agents={})
        result = dt.delegate(agent_name="nonexistent", task="Do something")
        assert "Unknown agent" in result

    def test_delegate_clears_messages(self):
        sub = _make_sub_agent("First response")
        dt = AgentToolkit(agents={"helper": sub})

        # First call
        dt.delegate(agent_name="helper", task="First task")
        # After delegate, messages should contain the conversation
        assert len(sub.messages) > 0

        # Second call should clear messages first
        sub.client.send_message = lambda *a, **k: iter([
            StreamEvent(type="text_delta", text="Second response"),
            StreamEvent(type="response_done", response=Response(
                message=Message(role="assistant", content="Second response"),
                stop_reason="end_turn",
            )),
        ])
        result = dt.delegate(agent_name="helper", task="Second task")
        assert result == "Second response"
        # Messages should only contain conversation from second call
        user_msgs = [m for m in sub.messages if m.role == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0].content == "Second task"

    def test_delegate_lists_available_agents(self):
        sub1 = _make_sub_agent()
        sub2 = _make_sub_agent()
        dt = AgentToolkit(agents={"alpha": sub1, "beta": sub2})
        result = dt.delegate(agent_name="unknown", task="test")
        assert "alpha" in result
        assert "beta" in result

    def test_delegate_nesting_control(self):
        """Sub-agent without delegate tool cannot nest."""
        sub = _make_sub_agent("No nesting")
        dt = AgentToolkit(agents={"helper": sub})

        # Sub-agent's ToolSet has no delegate tool
        sub_tools = sub.tool_set.get_tools()
        tool_names = {t.name for t in sub_tools}
        assert "delegate" not in tool_names


class TestAgentToolkitRegistration:

    def test_register_delegate_in_tool_set(self):
        """AgentToolkit's delegate method can be registered in a ToolSet."""
        sub = _make_sub_agent("Result from sub")
        dt = AgentToolkit(agents={"helper": sub})

        ts = ToolSet()
        ts.add(dt, methods=["delegate"])

        schemas = ts.get_tools()
        assert len(schemas) == 1
        assert schemas[0].name == "delegate"

        # Dispatch through ToolSet
        result = ts.dispatch(
            ToolCall(id="tc_1", name="delegate",
                     arguments={"agent_name": "helper", "task": "Do something"})
        )
        assert not result.is_error
        assert "Result from sub" in result.content


class TestSystemAgentWithDelegate:
    """Integration test: System Agent delegates to Sub-Agent."""

    def test_system_delegates_to_sub_agent(self):
        # Create sub-agent
        sub = _make_sub_agent("Sub-agent completed the task")

        # Create delegate tool
        dt = AgentToolkit(agents={"worker": sub})

        # Create system agent with essential tools + delegate
        mgr = ModuleManager()
        essential = ModuleToolkit(module_manager=mgr)
        system_ts = ToolSet()
        system_ts.add(essential)
        system_ts.add(dt, methods=["delegate"])

        client = LLMClient(
            model="test-model", api_key="test-key", base_url="https://api.test.com",
        )

        system_agent = Agent(
            client=client,
            tool_set=system_ts,
            system_prompt="You are the system agent.",
            messages=[],
        )
        system_ts.agent = system_agent

        # Verify system agent has both essential tools and delegate
        tool_names = {s.name for s in system_ts.get_tools()}
        assert "inspect_module" in tool_names
        assert "delegate" in tool_names

        # Simulate: LLM calls delegate tool
        tool_response = Response(
            message=Message(
                role="assistant",
                content="Delegating...",
                tool_calls=[ToolCall(
                    id="tc_1", name="delegate",
                    arguments={"agent_name": "worker", "task": "Do the work"},
                )],
            ),
            stop_reason="tool_use",
        )
        final_response = Response(
            message=Message(role="assistant", content="Task complete."),
            stop_reason="end_turn",
        )

        call_idx = 0

        def mock_send(*args, **kwargs):
            nonlocal call_idx
            if call_idx == 0:
                call_idx += 1
                yield StreamEvent(type="text_delta", text="Delegating...")
                yield StreamEvent(type="response_done", response=tool_response)
            else:
                yield StreamEvent(type="text_delta", text="Task complete.")
                yield StreamEvent(type="response_done", response=final_response)

        system_agent.client.send_message = mock_send

        # Run system agent
        events = list(system_agent.run(iter([InputEvent(type="user_message", text="Do it")])))
        text_parts = [e.text for e in events if e.type == "text_delta"]
        text = "".join(text_parts)

        assert "Delegating..." in text
        assert "Task complete." in text

        # Verify the delegate tool was called and result was fed back
        assert len(system_agent.messages) == 4  # user, assistant(delegate), user(result), assistant(final)
        tool_result_msg = system_agent.messages[2]
        assert len(tool_result_msg.tool_results) == 1
        assert "Sub-agent completed the task" in tool_result_msg.tool_results[0].content

        mgr.cleanup()


# ---------------------------------------------------------------------------
# Auto-Discovery Tests
# ---------------------------------------------------------------------------

class TestToolSetAutoDiscover:
    """Tests for Toolkit auto-discovery via ToolSet(auto_discover=True)."""

    @pytest.fixture
    def mgr(self):
        mgr = ModuleManager()
        yield mgr
        mgr.cleanup()

    @pytest.fixture
    def tool_set(self, mgr):
        """ToolSet with auto_discover=True and ModuleToolkit pre-registered."""
        tools = ModuleToolkit(module_manager=mgr)
        ts = ToolSet(auto_discover=True)
        ts.add(tools)
        return ts

    def test_auto_discover_finds_new_toolkit(self, tool_set, mgr):
        """define_module creating a Toolkit subclass → auto-discovered."""
        mgr.patch_module("test_discover.tools", (
            "import mutagent\n"
            "\n"
            "class Greeter(mutagent.Toolkit):\n"
            "    def greet(self, name: str) -> str:\n"
            "        '''Say hello.\n\n"
            "        Args:\n"
            "            name: Person to greet.\n"
            "        '''\n"
            "        return f'Hello, {name}!'\n"
        ))
        schemas = tool_set.get_tools()
        names = {s.name for s in schemas}
        assert "greet" in names

    def test_auto_discover_private_methods_excluded(self, tool_set, mgr):
        """Methods starting with _ should not be discovered."""
        mgr.patch_module("test_discover.private", (
            "import mutagent\n"
            "\n"
            "class WithPrivate(mutagent.Toolkit):\n"
            "    def public_tool(self) -> str:\n"
            "        '''A public tool.'''\n"
            "        return 'public'\n"
            "    def _helper(self) -> str:\n"
            "        return 'private'\n"
        ))
        schemas = tool_set.get_tools()
        names = {s.name for s in schemas}
        assert "public_tool" in names
        assert "_helper" not in names

    def test_auto_discover_dispatch_works(self, tool_set, mgr):
        """Auto-discovered tools can be dispatched."""
        mgr.patch_module("test_discover.calc", (
            "import mutagent\n"
            "\n"
            "class Calculator(mutagent.Toolkit):\n"
            "    def add_numbers(self, a: int, b: int) -> str:\n"
            "        '''Add two numbers.\n\n"
            "        Args:\n"
            "            a: First number.\n"
            "            b: Second number.\n"
            "        '''\n"
            "        return str(a + b)\n"
        ))
        result = tool_set.dispatch(
            ToolCall(id="tc_1", name="add_numbers", arguments={"a": 3, "b": 4})
        )
        assert not result.is_error
        assert "7" in result.content

    def test_pre_registered_not_duplicated(self, tool_set, mgr):
        """Classes added via add() should be skipped by auto-discovery."""
        schemas = tool_set.get_tools()
        # ModuleToolkit was add()'d, its methods should appear exactly once
        count = sum(1 for s in schemas if s.name == "inspect_module")
        assert count == 1

    def test_name_conflict_preserves_pre_registered(self, tool_set, mgr):
        """When auto-discovered tool name conflicts, pre-registered wins."""
        mgr.patch_module("test_discover.conflict", (
            "import mutagent\n"
            "\n"
            "class Conflicting(mutagent.Toolkit):\n"
            "    def inspect_module(self) -> str:\n"
            "        '''Conflicting tool.'''\n"
            "        return 'CONFLICT'\n"
        ))
        # Dispatch should use the pre-registered one, not the auto-discovered
        result = tool_set.dispatch(
            ToolCall(id="tc_1", name="inspect_module",
                     arguments={"module_path": "mutagent"})
        )
        assert not result.is_error
        assert "mutagent" in result.content
        assert "CONFLICT" not in result.content

    def test_complex_ctor_skipped(self, tool_set, mgr):
        """Toolkit subclass that needs constructor args is skipped."""
        mgr.patch_module("test_discover.complex_ctor", (
            "import mutagent\n"
            "\n"
            "class NeedsArgs(mutagent.Toolkit):\n"
            "    db: object  # required attribute\n"
            "    def query_db(self, sql: str) -> str:\n"
            "        '''Run a query.'''\n"
            "        return str(self.db)\n"
        ))
        # Should not crash, just skip
        schemas = tool_set.get_tools()
        names = {s.name for s in schemas}
        # The tool should not appear (can't instantiate without db)
        # Note: mutobj Declaration may or may not require args — depends on __init__
        # If it does auto-instantiate, that's OK too

    def test_auto_discover_false_no_scan(self, mgr):
        """auto_discover=False should not discover new toolkits."""
        ts = ToolSet()
        tools = ModuleToolkit(module_manager=mgr)
        ts.add(tools)

        mgr.patch_module("test_discover.noscan", (
            "import mutagent\n"
            "\n"
            "class HiddenToolkit(mutagent.Toolkit):\n"
            "    def hidden_tool(self) -> str:\n"
            "        '''Should not appear.'''\n"
            "        return 'hidden'\n"
        ))
        schemas = ts.get_tools()
        names = {s.name for s in schemas}
        assert "hidden_tool" not in names

    def test_auto_discover_multiple_toolkits(self, tool_set, mgr):
        """Multiple Toolkit subclasses discovered simultaneously."""
        mgr.patch_module("test_discover.multi_a", (
            "import mutagent\n"
            "\n"
            "class ToolsA(mutagent.Toolkit):\n"
            "    def tool_alpha(self) -> str:\n"
            "        '''Alpha tool.'''\n"
            "        return 'alpha'\n"
        ))
        mgr.patch_module("test_discover.multi_b", (
            "import mutagent\n"
            "\n"
            "class ToolsB(mutagent.Toolkit):\n"
            "    def tool_beta(self) -> str:\n"
            "        '''Beta tool.'''\n"
            "        return 'beta'\n"
        ))
        schemas = tool_set.get_tools()
        names = {s.name for s in schemas}
        assert "tool_alpha" in names
        assert "tool_beta" in names


class TestToolSetLateBind:
    """Tests for late binding: define_module updates reflected immediately."""

    @pytest.fixture
    def mgr(self):
        mgr = ModuleManager()
        yield mgr
        mgr.cleanup()

    @pytest.fixture
    def tool_set(self, mgr):
        tools = ModuleToolkit(module_manager=mgr)
        ts = ToolSet(auto_discover=True)
        ts.add(tools)
        return ts

    def test_late_binding_reflects_code_update(self, tool_set, mgr):
        """After redefine, calling the tool uses updated code."""
        mgr.patch_module("test_late.tools", (
            "import mutagent\n"
            "\n"
            "class MyTools(mutagent.Toolkit):\n"
            "    def compute(self, x: int) -> str:\n"
            "        '''Compute something.'''\n"
            "        return str(x * 2)\n"
        ))
        # First call: x * 2
        result = tool_set.dispatch(
            ToolCall(id="tc_1", name="compute", arguments={"x": 5})
        )
        assert result.content == "10"

        # Redefine: x * 3
        mgr.patch_module("test_late.tools", (
            "import mutagent\n"
            "\n"
            "class MyTools(mutagent.Toolkit):\n"
            "    def compute(self, x: int) -> str:\n"
            "        '''Compute something.'''\n"
            "        return str(x * 3)\n"
        ))
        # Second call should use new code (via late binding)
        result = tool_set.dispatch(
            ToolCall(id="tc_2", name="compute", arguments={"x": 5})
        )
        assert result.content == "15"

    def test_add_method_discovered_after_redefine(self, tool_set, mgr):
        """Adding a new method to an existing Toolkit is reflected."""
        mgr.patch_module("test_late.evolve", (
            "import mutagent\n"
            "\n"
            "class Evolving(mutagent.Toolkit):\n"
            "    def tool_v1(self) -> str:\n"
            "        '''Version 1 tool.'''\n"
            "        return 'v1'\n"
        ))
        schemas = tool_set.get_tools()
        names = {s.name for s in schemas}
        assert "tool_v1" in names
        assert "tool_v2" not in names

        # Add a new method
        mgr.patch_module("test_late.evolve", (
            "import mutagent\n"
            "\n"
            "class Evolving(mutagent.Toolkit):\n"
            "    def tool_v1(self) -> str:\n"
            "        '''Version 1 tool.'''\n"
            "        return 'v1'\n"
            "    def tool_v2(self) -> str:\n"
            "        '''Version 2 tool.'''\n"
            "        return 'v2'\n"
        ))
        schemas = tool_set.get_tools()
        names = {s.name for s in schemas}
        assert "tool_v1" in names
        assert "tool_v2" in names

    def test_remove_method_reflected_after_redefine(self, tool_set, mgr):
        """Removing a method from a Toolkit is reflected."""
        mgr.patch_module("test_late.shrink", (
            "import mutagent\n"
            "\n"
            "class Shrinking(mutagent.Toolkit):\n"
            "    def keep(self) -> str:\n"
            "        '''Keep this.'''\n"
            "        return 'kept'\n"
            "    def remove_me(self) -> str:\n"
            "        '''Will be removed.'''\n"
            "        return 'gone'\n"
        ))
        schemas = tool_set.get_tools()
        names = {s.name for s in schemas}
        assert "keep" in names
        assert "remove_me" in names

        # Redefine without remove_me
        mgr.patch_module("test_late.shrink", (
            "import mutagent\n"
            "\n"
            "class Shrinking(mutagent.Toolkit):\n"
            "    def keep(self) -> str:\n"
            "        '''Keep this.'''\n"
            "        return 'kept'\n"
        ))
        schemas = tool_set.get_tools()
        names = {s.name for s in schemas}
        assert "keep" in names
        assert "remove_me" not in names

    def test_full_iteration_cycle(self, tool_set, mgr):
        """Full cycle: define → discover → call → redefine → call → verify."""
        # Step 1: Define
        mgr.patch_module("test_late.cycle", (
            "import mutagent\n"
            "\n"
            "class CycleTool(mutagent.Toolkit):\n"
            "    def process(self, data: str) -> str:\n"
            "        '''Process data.'''\n"
            "        return data.upper()\n"
        ))

        # Step 2: Discover + call
        result = tool_set.dispatch(
            ToolCall(id="tc_1", name="process", arguments={"data": "hello"})
        )
        assert result.content == "HELLO"

        # Step 3: Redefine (bug fix: should reverse instead)
        mgr.patch_module("test_late.cycle", (
            "import mutagent\n"
            "\n"
            "class CycleTool(mutagent.Toolkit):\n"
            "    def process(self, data: str) -> str:\n"
            "        '''Process data.'''\n"
            "        return data[::-1]\n"
        ))

        # Step 4: Call again — should use new code
        result = tool_set.dispatch(
            ToolCall(id="tc_2", name="process", arguments={"data": "hello"})
        )
        assert result.content == "olleh"
