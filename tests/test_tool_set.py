"""Tests for ToolSet and DelegateTool."""

import pytest

import mutagent
from mutagent.agent import Agent
from mutagent.client import LLMClient
from mutagent.delegate import DelegateTool
from mutagent.essential_tools import EssentialTools
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
from mutagent.tool_set import ToolEntry, ToolSet
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
        tools = EssentialTools(module_manager=mgr)
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
        assert "query_logs" in names

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
        tools = EssentialTools(module_manager=mgr)
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
        tools = EssentialTools(module_manager=mgr)
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
        tools = EssentialTools(module_manager=mgr)
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
        tools = EssentialTools(module_manager=mgr)

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
# DelegateTool Tests
# ---------------------------------------------------------------------------

class TestDelegateToolDeclaration:

    def test_inherits_from_declaration(self):
        assert issubclass(DelegateTool, mutagent.Declaration)

    def test_uses_declaration_meta(self):
        assert isinstance(DelegateTool, DeclarationMeta)

    def test_declared_methods(self):
        declared = getattr(DelegateTool, _DECLARED_METHODS, set())
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


class TestDelegateToolDelegate:

    def test_delegate_returns_sub_agent_result(self):
        sub = _make_sub_agent("Hello from sub-agent")
        dt = DelegateTool(agents={"helper": sub})
        result = dt.delegate(agent_name="helper", task="Say hello")
        assert result == "Hello from sub-agent"

    def test_delegate_unknown_agent(self):
        dt = DelegateTool(agents={})
        result = dt.delegate(agent_name="nonexistent", task="Do something")
        assert "Unknown agent" in result

    def test_delegate_clears_messages(self):
        sub = _make_sub_agent("First response")
        dt = DelegateTool(agents={"helper": sub})

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
        dt = DelegateTool(agents={"alpha": sub1, "beta": sub2})
        result = dt.delegate(agent_name="unknown", task="test")
        assert "alpha" in result
        assert "beta" in result

    def test_delegate_nesting_control(self):
        """Sub-agent without delegate tool cannot nest."""
        sub = _make_sub_agent("No nesting")
        dt = DelegateTool(agents={"helper": sub})

        # Sub-agent's ToolSet has no delegate tool
        sub_tools = sub.tool_set.get_tools()
        tool_names = {t.name for t in sub_tools}
        assert "delegate" not in tool_names


class TestDelegateToolRegistration:

    def test_register_delegate_in_tool_set(self):
        """DelegateTool's delegate method can be registered in a ToolSet."""
        sub = _make_sub_agent("Result from sub")
        dt = DelegateTool(agents={"helper": sub})

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
        dt = DelegateTool(agents={"worker": sub})

        # Create system agent with essential tools + delegate
        mgr = ModuleManager()
        essential = EssentialTools(module_manager=mgr)
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
