"""End-to-end integration tests for mutagent Agent."""

import sys
from pathlib import Path
from typing import AsyncIterator

import mutobj

import pytest

import mutagent.builtins  # noqa: F401  -- register all @impl

from mutagent.agent import Agent
from mutagent.client import LLMClient
from mutagent.config import Config
from mutagent.toolkits.module_toolkit import ModuleToolkit
from mutagent.main import App
from mutagent.messages import InputEvent, Message, Response, StreamEvent, ToolCall, ToolResult, ToolSchema
from mutagent.runtime.module_manager import ModuleManager
from mutagent.tools import ToolSet


def _create_test_agent(
    api_key: str = "test-key",
    model: str = "claude-sonnet-4-20250514",
    base_url: str = "https://api.anthropic.com",
    system_prompt: str = "",
) -> Agent:
    """Create an Agent for testing via App.setup_agent()."""
    config = Config(_layers=[(Path(), {
        "providers": {"test": {
            "provider": "AnthropicProvider",
            "model_id": model,
            "auth_token": api_key,
            "base_url": base_url,
            "models": [model],
        }},
        "default_model": model,
    })])
    entry = App(config=config)
    entry.setup_agent(system_prompt=system_prompt)
    return entry.agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _single_input(text: str) -> AsyncIterator[InputEvent]:
    """Create an async iterator yielding a single user_message InputEvent."""
    yield InputEvent(type="user_message", text=text)


def _events_for(response: Response) -> list[StreamEvent]:
    """Build StreamEvents that a non-streaming send_message would yield for a Response."""
    events = []
    if response.message.content:
        events.append(StreamEvent(type="text_delta", text=response.message.content))
    for tc in response.message.tool_calls:
        events.append(StreamEvent(type="tool_use_start", tool_call=tc))
        events.append(StreamEvent(type="tool_use_end"))
    events.append(StreamEvent(type="response_done", response=response))
    return events


def _make_mock_send(responses: list[Response]):
    """Create a mock async generator send_message from a list of Responses."""
    event_lists = [_events_for(r) for r in responses]
    call_idx = 0

    async def mock_send(*args, **kwargs):
        nonlocal call_idx
        evts = event_lists[call_idx]
        call_idx += 1
        for e in evts:
            yield e

    return mock_send


async def _collect_text(aiter: AsyncIterator[StreamEvent]) -> str:
    """Collect text from text_delta events."""
    parts = []
    async for event in aiter:
        if event.type == "text_delta":
            parts.append(event.text)
    return "".join(parts)


class TestSetupAgent:

    def test_setup_agent_returns_agent(self):
        agent = _create_test_agent(api_key="test-key")
        assert isinstance(agent, Agent)
        assert agent.client.provider.api_key == "test-key"
        assert agent.client.model == "claude-sonnet-4-20250514"
        assert agent.system_prompt
        assert agent.messages == []

    def test_setup_agent_custom_params(self):
        agent = _create_test_agent(
            api_key="key",
            model="custom-model",
            system_prompt="Custom prompt",
        )
        assert agent.client.model == "custom-model"
        assert agent.system_prompt == "Custom prompt"


class TestEndToEnd:
    """Simulate full Agent workflow with mock LLM responses."""

    @pytest.fixture
    def agent(self):
        agent = _create_test_agent(api_key="test-key")
        yield agent

    async def test_inspect_then_patch_then_save(self, agent, tmp_path):
        """Simulate: Agent inspects module -> patches code -> saves."""
        # Step 1: LLM asks to inspect a module
        inspect_response = Response(
            message=Message(
                role="assistant",
                content="Let me inspect the module structure.",
                tool_calls=[ToolCall(
                    id="tc_1",
                    name="Module-inspect",
                    arguments={"module_path": "mutagent", "depth": 1},
                )],
            ),
            stop_reason="tool_use",
        )

        # Step 2: LLM patches a new module
        patch_response = Response(
            message=Message(
                role="assistant",
                content="I'll create a helper module.",
                tool_calls=[ToolCall(
                    id="tc_2",
                    name="Module-define",
                    arguments={
                        "module_path": "test_e2e.helper",
                        "source": "def add(a, b):\n    return a + b\n",
                    },
                )],
            ),
            stop_reason="tool_use",
        )

        # Step 3: LLM saves the module
        save_response = Response(
            message=Message(
                role="assistant",
                content="Saving the module.",
                tool_calls=[ToolCall(
                    id="tc_3",
                    name="Module-save",
                    arguments={
                        "module_path": "test_e2e.helper",
                        "level": "project",
                    },
                )],
            ),
            stop_reason="tool_use",
        )

        # Step 4: Final response
        final_response = Response(
            message=Message(
                role="assistant",
                content="Done! I created a helper module with an add function.",
            ),
            stop_reason="end_turn",
        )

        agent.client.send_message = _make_mock_send([
            inspect_response,
            patch_response,
            save_response,
            final_response,
        ])

        result = await _collect_text(agent.run(_single_input("Create a helper module with an add function")))

        # Verify final result
        assert "Done" in result

        # Verify the tool interactions happened
        assert len(agent.messages) == 8  # user + 3*(assistant+tool_result) + final_assistant

        # Verify inspect result was in messages
        inspect_result = agent.messages[2].tool_results[0]
        assert "mutagent" in inspect_result.content

        # Verify patch result
        patch_result = agent.messages[4].tool_results[0]
        assert "OK" in patch_result.content

        # Verify save result
        save_result = agent.messages[6].tool_results[0]
        assert "OK" in save_result.content

        # Verify file was actually saved to .mutagent/ (project level)
        saved_file = Path.cwd() / ".mutagent" / "test_e2e" / "helper.py"
        assert saved_file.exists()
        assert "def add" in saved_file.read_text()
        # Clean up
        import shutil
        shutil.rmtree(Path.cwd() / ".mutagent" / "test_e2e", ignore_errors=True)

    async def test_view_source_of_patched_module(self, agent):
        """Agent patches a module then views its source."""
        # Patch
        patch_resp = Response(
            message=Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(
                    id="tc_1",
                    name="Module-define",
                    arguments={
                        "module_path": "test_e2e.src",
                        "source": "class Greeter:\n    def greet(self):\n        return 'hi'\n",
                    },
                )],
            ),
            stop_reason="tool_use",
        )

        # View source
        view_resp = Response(
            message=Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(
                    id="tc_2",
                    name="Module-view_source",
                    arguments={"target": "test_e2e.src.Greeter"},
                )],
            ),
            stop_reason="tool_use",
        )

        final_resp = Response(
            message=Message(role="assistant", content="Here's the Greeter class."),
            stop_reason="end_turn",
        )

        agent.client.send_message = _make_mock_send([patch_resp, view_resp, final_resp])

        result = await _collect_text(agent.run(_single_input("Show me the Greeter class")))

        # Verify view_source returned the source
        view_result = agent.messages[4].tool_results[0]
        assert "class Greeter" in view_result.content
        assert "return 'hi'" in view_result.content

    async def test_simple_chat_no_tools(self, agent):
        """Agent can respond without using any tools."""
        response = Response(
            message=Message(role="assistant", content="Hello! I'm mutagent."),
            stop_reason="end_turn",
        )
        agent.client.send_message = _make_mock_send([response])

        result = await _collect_text(agent.run(_single_input("Hello")))
        assert result == "Hello! I'm mutagent."
        assert len(agent.messages) == 2


class TestSelfEvolution:
    """Verify Agent can create new tool modules, patch ToolSet, and use them."""

    @pytest.fixture
    def agent(self):
        agent = _create_test_agent(api_key="test-key")
        yield agent
        # Cleanup: unregister override impls, remove virtual modules,
        # then re-load the original tool_set impl to restore original impls.
        entries = getattr(agent.tool_set, '_entries', {})
        # Find ModuleToolkit source to get module_manager
        for entry in entries.values():
            mgr = getattr(entry.source, "module_manager", None)
            if mgr is not None:
                break
        else:
            mgr = None

        mutobj.unregister_module_impls("user_tools.tool_set_ext")
        if mgr:
            mgr.cleanup()
        # Re-execute the original tool_set impl to restore ToolSet impls
        self._reload_tool_set_impl()

    @staticmethod
    def _reload_tool_set_impl():
        """Re-execute the builtins/tool_set_impl.py to restore original @impls."""
        from pathlib import Path
        tool_set_impl_path = Path(__file__).resolve().parent.parent / "src" / "mutagent" / "builtins" / "tool_set_impl.py"
        source = tool_set_impl_path.read_text(encoding="utf-8")
        mod = sys.modules.get("mutagent.builtins.tool_set_impl")
        if mod is not None:
            code = compile(source, str(tool_set_impl_path), "exec")
            exec(code, mod.__dict__)

    async def test_create_tool_and_use_it(self, agent):
        """Self-evolution: Agent creates a new tool class, patches ToolSet, then uses it."""
        # Step 1: Agent creates a new tool class declaration
        create_decl_resp = Response(
            message=Message(
                role="assistant",
                content="I'll create a math tools module.",
                tool_calls=[ToolCall(
                    id="tc_1",
                    name="Module-define",
                    arguments={
                        "module_path": "user_tools.math_tools",
                        "source": (
                            "import mutagent\n"
                            "\n"
                            "class MathTools(mutagent.Declaration):\n"
                            "    def factorial(self, n: int) -> str:\n"
                            "        '''Compute factorial of n.'''\n"
                            "        ...\n"
                        ),
                    },
                )],
            ),
            stop_reason="tool_use",
        )

        # Step 2: Agent provides the implementation
        create_impl_resp = Response(
            message=Message(
                role="assistant",
                content="Now I'll implement the factorial method.",
                tool_calls=[ToolCall(
                    id="tc_2",
                    name="Module-define",
                    arguments={
                        "module_path": "user_tools.math_tools_impl",
                        "source": (
                            "import mutagent\n"
                            "from user_tools.math_tools import MathTools\n"
                            "\n"
                            "@mutagent.impl(MathTools.factorial)\n"
                            "def factorial(self, n: int) -> str:\n"
                            "    result = 1\n"
                            "    for i in range(2, n + 1):\n"
                            "        result *= i\n"
                            "    return str(result)\n"
                        ),
                    },
                )],
            ),
            stop_reason="tool_use",
        )

        # Step 3: Agent extends ToolSet to include the new tool
        patch_tool_set_resp = Response(
            message=Message(
                role="assistant",
                content="Now I'll extend the ToolSet to include the new tool.",
                tool_calls=[ToolCall(
                    id="tc_3",
                    name="Module-define",
                    arguments={
                        "module_path": "user_tools.tool_set_ext",
                        "source": (
                            "import mutagent\n"
                            "from mutagent.tools import ToolSet\n"
                            "from mutagent.messages import ToolResult\n"
                            "from mutagent.builtins.schema import make_schema\n"
                            "\n"
                            "@mutagent.impl(ToolSet.get_tools)\n"
                            "def get_tools(self):\n"
                            "    entries = getattr(self, '_entries', {})\n"
                            "    schemas = [entry.schema for entry in entries.values()]\n"
                            "    from user_tools.math_tools import MathTools\n"
                            "    mt = MathTools()\n"
                            "    schemas.append(make_schema(getattr(mt, 'factorial')))\n"
                            "    return schemas\n"
                            "\n"
                            "@mutagent.impl(ToolSet.dispatch)\n"
                            "async def dispatch(self, tool_call):\n"
                            "    entries = getattr(self, '_entries', {})\n"
                            "    entry = entries.get(tool_call.name)\n"
                            "    if entry is not None:\n"
                            "        try:\n"
                            "            result = entry.callable(**tool_call.arguments)\n"
                            "            return ToolResult(tool_call_id=tool_call.id, content=str(result))\n"
                            "        except Exception as e:\n"
                            "            return ToolResult(tool_call_id=tool_call.id, content=str(e), is_error=True)\n"
                            "    from user_tools.math_tools import MathTools\n"
                            "    mt = MathTools()\n"
                            "    method = getattr(mt, tool_call.name, None)\n"
                            "    if method is not None:\n"
                            "        try:\n"
                            "            result = method(**tool_call.arguments)\n"
                            "            return ToolResult(tool_call_id=tool_call.id, content=str(result))\n"
                            "        except Exception as e:\n"
                            "            return ToolResult(tool_call_id=tool_call.id, content=str(e), is_error=True)\n"
                            "    return ToolResult(tool_call_id=tool_call.id, content='Unknown tool', is_error=True)\n"
                        ),
                    },
                )],
            ),
            stop_reason="tool_use",
        )

        # Step 4: Agent uses the new tool directly (dispatched by patched ToolSet)
        use_new_tool_resp = Response(
            message=Message(
                role="assistant",
                content="Let me compute factorial(6).",
                tool_calls=[ToolCall(
                    id="tc_4",
                    name="factorial",
                    arguments={"n": 6},
                )],
            ),
            stop_reason="tool_use",
        )

        # Step 5: Final response
        final_resp = Response(
            message=Message(
                role="assistant",
                content="factorial(6) = 720. The self-evolution is complete!",
            ),
            stop_reason="end_turn",
        )

        agent.client.send_message = _make_mock_send([
            create_decl_resp,
            create_impl_resp,
            patch_tool_set_resp,
            use_new_tool_resp,
            final_resp,
        ])

        result = await _collect_text(agent.run(_single_input("Create a factorial tool and use it")))

        # Verify the full workflow completed
        assert "720" in result

        # Verify the new tool was dispatched and returned correct result
        factorial_result = agent.messages[8].tool_results[0]
        assert "720" in factorial_result.content
        assert factorial_result.is_error is False
