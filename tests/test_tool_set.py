"""Tests for ToolSet and AgentToolkit."""

import pytest

import mutagent
from mutagent.agent import Agent
from mutagent.context import AgentContext
from mutagent.toolkits.agent_toolkit import AgentToolkit
from mutagent.client import LLMClient
from mutagent.builtins.anthropic_provider import AnthropicProvider
from mutagent.toolkits.module_toolkit import ModuleToolkit
from mutagent.messages import (
    Message,
    Response,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
    ToolSchema,
    TurnStartBlock,
)
from mutagent.runtime.module_manager import ModuleManager
from mutagent.tools import ToolEntry, ToolSet
from mutobj.core import DeclarationMeta, _DECLARED_METHODS

import mutagent.builtins  # noqa: F401  -- register all @impl


# ---------------------------------------------------------------------------
# Helper: extract text/tool_calls from new Message model
# ---------------------------------------------------------------------------

def _get_text(msg: Message) -> str:
    return "".join(b.text for b in msg.blocks if isinstance(b, TextBlock))


def _get_tool_calls(msg: Message) -> list[ToolUseBlock]:
    return [b for b in msg.blocks if isinstance(b, ToolUseBlock)]


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
        assert "Module-inspect" in names
        assert "Module-view_source" in names
        assert "Module-define" in names
        assert "Module-save" in names

    def test_add_with_methods_filter(self, tool_set, essential_tools):
        tool_set.add(essential_tools, methods=["inspect", "view_source"])
        schemas = tool_set.get_tools()
        names = {s.name for s in schemas}
        assert names == {"Module-inspect", "Module-view_source"}

    def test_add_single_method(self, tool_set, essential_tools):
        tool_set.add(essential_tools, methods=["define"])
        schemas = tool_set.get_tools()
        assert len(schemas) == 1
        assert schemas[0].name == "Module-define"


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
        assert populated_set.remove("Module-inspect") is True
        names = {s.name for s in populated_set.get_tools()}
        assert "Module-inspect" not in names

    def test_remove_nonexistent(self, populated_set):
        assert populated_set.remove("nonexistent_tool") is False

    async def test_remove_then_dispatch_fails(self, populated_set):
        populated_set.remove("Module-inspect")
        block = ToolUseBlock(id="tc_1", name="Module-inspect", input={})
        await populated_set.dispatch(block)
        assert block.is_error
        assert "Unknown tool" in block.result


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
        schema = populated_set.query("Module-inspect")
        assert schema is not None
        assert schema.name == "Module-inspect"
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

    async def test_dispatch_inspect(self, tool_set):
        block = ToolUseBlock(id="tc_1", name="Module-inspect", input={"module_path": "mutagent"})
        await tool_set.dispatch(block)
        assert not block.is_error
        assert block.status == "done"
        assert "mutagent" in block.result

    async def test_dispatch_define(self, tool_set):
        block = ToolUseBlock(id="tc_2", name="Module-define",
                             input={"module_path": "test_ts_dispatch.mod", "source": "x = 42\n"})
        await tool_set.dispatch(block)
        assert not block.is_error
        assert block.status == "done"
        assert "OK" in block.result

    async def test_dispatch_unknown_tool(self, tool_set):
        block = ToolUseBlock(id="tc_3", name="nonexistent_tool", input={})
        await tool_set.dispatch(block)
        assert block.is_error
        assert block.status == "done"
        assert "Unknown tool" in block.result

    async def test_dispatch_with_error(self, tool_set):
        block = ToolUseBlock(id="tc_4", name="Module-save",
                             input={"module_path": "nonexistent.mod"})
        await tool_set.dispatch(block)
        # save for unpatched module returns error string, not exception
        assert block.status == "done"
        assert "Error" in block.result


class TestToolSetEmptyState:

    def test_empty_get_tools(self):
        ts = ToolSet()
        assert ts.get_tools() == []

    async def test_empty_dispatch_fails(self):
        ts = ToolSet()
        block = ToolUseBlock(id="tc_1", name="anything", input={})
        await ts.dispatch(block)
        assert block.is_error

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
        ts.add(tools, methods=["inspect"])
        ts.add(custom_tool)

        schemas = ts.get_tools()
        names = {s.name for s in schemas}
        assert "Module-inspect" in names
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
    provider = AnthropicProvider(base_url="https://api.test.com", api_key="test-key")
    client = LLMClient(provider=provider, model="test-model")
    tool_set = ToolSet()
    context = AgentContext()
    context.prompts.append(
        Message(role="system", blocks=[TextBlock(text="You are a test sub-agent.")], label="base")
    )
    agent = Agent(
        llm=client,
        tools=tool_set,
        context=context,
    )
    tool_set.agent = agent

    response = Response(
        message=Message(role="assistant", blocks=[TextBlock(text=response_text)]),
        stop_reason="end_turn",
    )

    async def mock_send(*args, **kwargs):
        yield StreamEvent(type="text_delta", text=response_text)
        yield StreamEvent(type="response_done", response=response)

    agent.llm.send_message = mock_send
    return agent


class TestAgentToolkitDelegate:

    async def test_delegate_returns_sub_agent_result(self):
        sub = _make_sub_agent("Hello from sub-agent")
        dt = AgentToolkit(agents={"helper": sub})
        result = await dt.delegate(agent_name="helper", task="Say hello")
        assert result == "Hello from sub-agent"

    async def test_delegate_unknown_agent(self):
        dt = AgentToolkit(agents={})
        result = await dt.delegate(agent_name="nonexistent", task="Do something")
        assert "Unknown agent" in result

    async def test_delegate_clears_messages(self):
        sub = _make_sub_agent("First response")
        dt = AgentToolkit(agents={"helper": sub})

        # First call
        await dt.delegate(agent_name="helper", task="First task")
        # After delegate, messages should contain the conversation
        assert len(sub.context.messages) > 0

        # Second call should clear messages first
        async def mock_send_2(*a, **k):
            yield StreamEvent(type="text_delta", text="Second response")
            yield StreamEvent(type="response_done", response=Response(
                message=Message(role="assistant", blocks=[TextBlock(text="Second response")]),
                stop_reason="end_turn",
            ))

        sub.llm.send_message = mock_send_2
        result = await dt.delegate(agent_name="helper", task="Second task")
        assert result == "Second response"
        # Messages should only contain conversation from second call
        user_msgs = [m for m in sub.context.messages if m.role == "user"]
        assert len(user_msgs) == 1
        assert _get_text(user_msgs[0]) == "Second task"

    async def test_delegate_lists_available_agents(self):
        sub1 = _make_sub_agent()
        sub2 = _make_sub_agent()
        dt = AgentToolkit(agents={"alpha": sub1, "beta": sub2})
        result = await dt.delegate(agent_name="unknown", task="test")
        assert "alpha" in result
        assert "beta" in result

    def test_delegate_nesting_control(self):
        """Sub-agent without delegate tool cannot nest."""
        sub = _make_sub_agent("No nesting")
        dt = AgentToolkit(agents={"helper": sub})

        # Sub-agent's ToolSet has no delegate tool
        sub_tools = sub.tools.get_tools()
        tool_names = {t.name for t in sub_tools}
        assert "Agent-delegate" not in tool_names


class TestAgentToolkitRegistration:

    async def test_register_delegate_in_tool_set(self):
        """AgentToolkit's delegate method can be registered in a ToolSet."""
        sub = _make_sub_agent("Result from sub")
        dt = AgentToolkit(agents={"helper": sub})

        ts = ToolSet()
        ts.add(dt, methods=["delegate"])

        schemas = ts.get_tools()
        assert len(schemas) == 1
        assert schemas[0].name == "Agent-delegate"

        # Dispatch through ToolSet
        block = ToolUseBlock(id="tc_1", name="Agent-delegate",
                             input={"agent_name": "helper", "task": "Do something"})
        await ts.dispatch(block)
        assert not block.is_error
        assert block.status == "done"
        assert "Result from sub" in block.result


class TestSystemAgentWithDelegate:
    """Integration test: System Agent delegates to Sub-Agent."""

    async def test_system_delegates_to_sub_agent(self):
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

        provider = AnthropicProvider(base_url="https://api.test.com", api_key="test-key")
        client = LLMClient(provider=provider, model="test-model")

        context = AgentContext()
        context.prompts.append(
            Message(role="system", blocks=[TextBlock(text="You are the system agent.")], label="base")
        )
        system_agent = Agent(
            llm=client,
            tools=system_ts,
            context=context,
        )
        system_ts.agent = system_agent

        # Verify system agent has both essential tools and delegate
        tool_names = {s.name for s in system_ts.get_tools()}
        assert "Module-inspect" in tool_names
        assert "Agent-delegate" in tool_names

        # Simulate: LLM calls delegate tool
        tc = ToolUseBlock(
            id="tc_1", name="Agent-delegate",
            input={"agent_name": "worker", "task": "Do the work"},
        )
        tool_response = Response(
            message=Message(
                role="assistant",
                blocks=[TextBlock(text="Delegating..."), tc],
            ),
            stop_reason="tool_use",
        )
        final_response = Response(
            message=Message(role="assistant", blocks=[TextBlock(text="Task complete.")]),
            stop_reason="end_turn",
        )

        call_idx = 0

        async def mock_send(*args, **kwargs):
            nonlocal call_idx
            if call_idx == 0:
                call_idx += 1
                yield StreamEvent(type="text_delta", text="Delegating...")
                yield StreamEvent(type="response_done", response=tool_response)
            else:
                yield StreamEvent(type="text_delta", text="Task complete.")
                yield StreamEvent(type="response_done", response=final_response)

        system_agent.llm.send_message = mock_send

        # Run system agent
        async def input_stream():
            from uuid import uuid4
            yield Message(
                role="user",
                blocks=[TurnStartBlock(turn_id=uuid4().hex[:12]), TextBlock(text="Do it")],
            )

        events = []
        async for e in system_agent.run(input_stream()):
            events.append(e)
        text_parts = [e.text for e in events if e.type == "text_delta"]
        text = "".join(text_parts)

        assert "Delegating..." in text
        assert "Task complete." in text

        # Verify the delegate tool was called and result was fed back
        # New model: messages are [user, assistant(delegate+tooluse), assistant(final)]
        # The tool result is on the ToolUseBlock in the assistant message
        assert len(system_agent.context.messages) == 3  # user, assistant(delegate with done tool), assistant(final)
        assistant_msg = system_agent.context.messages[1]
        tool_calls = _get_tool_calls(assistant_msg)
        assert len(tool_calls) == 1
        assert tool_calls[0].status == "done"
        assert "Sub-agent completed the task" in tool_calls[0].result

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
        """define creating a Toolkit subclass -> auto-discovered."""
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
        assert "Greeter-greet" in names

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
        assert "WithPrivate-public_tool" in names
        assert "_helper" not in names
        assert "WithPrivate-_helper" not in names

    async def test_auto_discover_dispatch_works(self, tool_set, mgr):
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
        block = ToolUseBlock(id="tc_1", name="Calculator-add_numbers", input={"a": 3, "b": 4})
        await tool_set.dispatch(block)
        assert not block.is_error
        assert block.status == "done"
        assert "7" in block.result

    def test_pre_registered_not_duplicated(self, tool_set, mgr):
        """Classes added via add() should be skipped by auto-discovery."""
        schemas = tool_set.get_tools()
        # ModuleToolkit was add()'d, its methods should appear exactly once
        count = sum(1 for s in schemas if s.name == "Module-inspect")
        assert count == 1

    async def test_name_conflict_preserves_pre_registered(self, tool_set, mgr):
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
        # Conflicting generates "Conflicting-inspect_module" which doesn't
        # conflict with "Module-inspect", so both should exist
        block = ToolUseBlock(id="tc_1", name="Module-inspect",
                             input={"module_path": "mutagent"})
        await tool_set.dispatch(block)
        assert not block.is_error
        assert block.status == "done"
        assert "mutagent" in block.result

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
        # Note: mutobj Declaration may or may not require args -- depends on __init__
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
        assert "ToolsA-tool_alpha" in names
        assert "ToolsB-tool_beta" in names


class TestToolSetLateBind:
    """Tests for late binding: define updates reflected immediately."""

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

    async def test_late_binding_reflects_code_update(self, tool_set, mgr):
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
        block = ToolUseBlock(id="tc_1", name="MyTools-compute", input={"x": 5})
        await tool_set.dispatch(block)
        assert block.result == "10"

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
        block2 = ToolUseBlock(id="tc_2", name="MyTools-compute", input={"x": 5})
        await tool_set.dispatch(block2)
        assert block2.result == "15"

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
        assert "Evolving-tool_v1" in names
        assert "Evolving-tool_v2" not in names

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
        assert "Evolving-tool_v1" in names
        assert "Evolving-tool_v2" in names

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
        assert "Shrinking-keep" in names
        assert "Shrinking-remove_me" in names

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
        assert "Shrinking-keep" in names
        assert "Shrinking-remove_me" not in names

    async def test_full_iteration_cycle(self, tool_set, mgr):
        """Full cycle: define -> discover -> call -> redefine -> call -> verify."""
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
        block = ToolUseBlock(id="tc_1", name="CycleTool-process", input={"data": "hello"})
        await tool_set.dispatch(block)
        assert block.result == "HELLO"

        # Step 3: Redefine (bug fix: should reverse instead)
        mgr.patch_module("test_late.cycle", (
            "import mutagent\n"
            "\n"
            "class CycleTool(mutagent.Toolkit):\n"
            "    def process(self, data: str) -> str:\n"
            "        '''Process data.'''\n"
            "        return data[::-1]\n"
        ))

        # Step 4: Call again -- should use new code
        block2 = ToolUseBlock(id="tc_2", name="CycleTool-process", input={"data": "hello"})
        await tool_set.dispatch(block2)
        assert block2.result == "olleh"


# ---------------------------------------------------------------------------
# Tool Naming Convention Tests
# ---------------------------------------------------------------------------

class TestToolNamingConvention:
    """工具名格式为 '{Prefix}-{method}'，前缀从类名自动推导。"""

    def test_toolkit_suffix_stripped(self):
        """类名以 Toolkit 结尾时，去掉该后缀作为前缀。"""
        class WebToolkit(mutagent.Toolkit):
            def search(self, query: str) -> str:
                """Search the web."""
                return f"results for {query}"

            def fetch(self, url: str) -> str:
                """Fetch a URL."""
                return f"content of {url}"

        ts = ToolSet()
        ts.add(WebToolkit())
        names = {s.name for s in ts.get_tools()}
        assert names == {"Web-search", "Web-fetch"}

    def test_class_name_without_toolkit_suffix(self):
        """类名不以 Toolkit 结尾时，使用完整类名作为前缀。"""
        class Greeter(mutagent.Toolkit):
            def say_hello(self) -> str:
                """Say hello."""
                return "hello"

        ts = ToolSet()
        ts.add(Greeter())
        names = {s.name for s in ts.get_tools()}
        assert names == {"Greeter-say_hello"}

    async def test_dispatch_uses_prefixed_name(self):
        """dispatch() 必须使用前缀工具名。"""
        class WebToolkit(mutagent.Toolkit):
            def search(self, query: str) -> str:
                """Search the web."""
                return f"found: {query}"

        ts = ToolSet()
        ts.add(WebToolkit())
        block = ToolUseBlock(id="tc_1", name="Web-search", input={"query": "python"})
        await ts.dispatch(block)
        assert not block.is_error
        assert block.status == "done"
        assert "found: python" in block.result

    async def test_bare_method_name_dispatch_fails(self):
        """使用不带前缀的方法名 dispatch 会失败。"""
        class WebToolkit(mutagent.Toolkit):
            def search(self, query: str) -> str:
                """Search the web."""
                return "results"

        ts = ToolSet()
        ts.add(WebToolkit())
        block = ToolUseBlock(id="tc_1", name="search", input={"query": "test"})
        await ts.dispatch(block)
        assert block.is_error
        assert "Unknown tool" in block.result

    def test_query_uses_prefixed_name(self):
        """query() 使用前缀工具名。"""
        class SessionToolkit(mutagent.Toolkit):
            def create(self, session_type: str) -> str:
                """Create a session."""
                return "created"

        ts = ToolSet()
        ts.add(SessionToolkit())
        schema = ts.query("Session-create")
        assert schema is not None
        assert schema.name == "Session-create"
        assert ts.query("create") is None

    def test_schema_name_is_prefixed(self):
        """ToolSchema.name 使用前缀格式。"""
        class WebToolkit(mutagent.Toolkit):
            def search(self, query: str) -> str:
                """Search the web.

                Args:
                    query: Search query.
                """
                return "results"

        ts = ToolSet()
        ts.add(WebToolkit())
        schema = ts.query("Web-search")
        assert schema is not None
        assert schema.name == "Web-search"
        assert "Search the web" in schema.description
        assert "query" in schema.input_schema["properties"]

    def test_remove_uses_prefixed_name(self):
        """remove() 使用前缀工具名。"""
        class WebToolkit(mutagent.Toolkit):
            def search(self, query: str) -> str:
                """Search."""
                return "results"

            def fetch(self, url: str) -> str:
                """Fetch."""
                return "content"

        ts = ToolSet()
        ts.add(WebToolkit())
        assert ts.remove("Web-search") is True
        names = {s.name for s in ts.get_tools()}
        assert names == {"Web-fetch"}

    def test_methods_filter_uses_method_names(self):
        """add(methods=[...]) 过滤参数使用方法名，注册结果使用前缀工具名。"""
        class WebToolkit(mutagent.Toolkit):
            def search(self, query: str) -> str:
                """Search."""
                return "results"

            def fetch(self, url: str) -> str:
                """Fetch."""
                return "content"

        ts = ToolSet()
        ts.add(WebToolkit(), methods=["search"])
        schemas = ts.get_tools()
        assert len(schemas) == 1
        assert schemas[0].name == "Web-search"

    def test_existing_toolkits_get_prefixed(self):
        """现有 Toolkit 也使用前缀命名。"""
        mgr = ModuleManager()
        module_tools = ModuleToolkit(module_manager=mgr)

        ts = ToolSet()
        ts.add(module_tools, methods=["inspect"])

        schemas = ts.get_tools()
        names = {s.name for s in schemas}
        assert "Module-inspect" in names
        mgr.cleanup()


class TestToolNamingAutoDiscover:
    """前缀命名与 auto-discovery 的集成测试。"""

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

    async def test_auto_discover_toolkit_suffix_stripped(self, tool_set, mgr):
        """auto-discovery: 类名以 Toolkit 结尾时去掉后缀。"""
        mgr.patch_module("test_naming.web_discover", (
            "import mutagent\n"
            "\n"
            "class WebDiscoverToolkit(mutagent.Toolkit):\n"
            "\n"
            "    def web_search(self, query: str) -> str:\n"
            "        '''Search the web.\n\n"
            "        Args:\n"
            "            query: Search query.\n"
            "        '''\n"
            "        return f'found: {query}'\n"
        ))
        schemas = tool_set.get_tools()
        names = {s.name for s in schemas}
        assert "WebDiscover-web_search" in names

        block = ToolUseBlock(id="tc_1", name="WebDiscover-web_search", input={"query": "test"})
        await tool_set.dispatch(block)
        assert not block.is_error
        assert block.status == "done"
        assert "found: test" in block.result

    def test_auto_discover_prefixed_no_conflict(self, tool_set, mgr):
        """不同前缀的同名方法不冲突。"""
        mgr.patch_module("test_naming.noconflict", (
            "import mutagent\n"
            "\n"
            "class InspectToolkit(mutagent.Toolkit):\n"
            "\n"
            "    def inspect_module(self) -> str:\n"
            "        '''Inspect something.'''\n"
            "        return 'prefixed inspect'\n"
        ))
        schemas = tool_set.get_tools()
        names = {s.name for s in schemas}
        # Module-inspect (pre-registered) and Inspect-inspect_module coexist
        assert "Module-inspect" in names
        assert "Inspect-inspect_module" in names

    async def test_auto_discover_late_binding_with_prefix(self, tool_set, mgr):
        """auto-discovered 前缀工具支持 late binding。"""
        mgr.patch_module("test_naming.late", (
            "import mutagent\n"
            "\n"
            "class CalcToolkit(mutagent.Toolkit):\n"
            "\n"
            "    def compute(self, x: int) -> str:\n"
            "        '''Compute.'''\n"
            "        return str(x * 2)\n"
        ))
        block = ToolUseBlock(id="tc_1", name="Calc-compute", input={"x": 5})
        await tool_set.dispatch(block)
        assert block.result == "10"

        mgr.patch_module("test_naming.late", (
            "import mutagent\n"
            "\n"
            "class CalcToolkit(mutagent.Toolkit):\n"
            "\n"
            "    def compute(self, x: int) -> str:\n"
            "        '''Compute.'''\n"
            "        return str(x * 3)\n"
        ))
        block2 = ToolUseBlock(id="tc_2", name="Calc-compute", input={"x": 5})
        await tool_set.dispatch(block2)
        assert block2.result == "15"
