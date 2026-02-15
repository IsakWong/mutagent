"""Default implementation for mutagent.main.Main methods."""

from __future__ import annotations

import asyncio
import sys

import mutagent
from mutagent.agent import Agent
from mutagent.client import LLMClient
from mutagent.essential_tools import EssentialTools
from mutagent.main import Main
from mutagent.messages import InputEvent
from mutagent.runtime.module_manager import ModuleManager
from mutagent.selector import ToolSelector

SYSTEM_PROMPT = """\
You are **mutagent**, a self-evolving Python AI Agent framework.

## Identity
You are built on the forwardpy declaration-implementation separation pattern. \
Your own source code is organized as declarations (.py) with implementations (.impl.py), \
and you can inspect, modify, and hot-reload any of it at runtime — including yourself.

## Core Tools
You have 5 essential tools:
- **inspect_module(module_path, depth)** — Browse Python module structure (classes, functions, attributes)
- **view_source(target)** — Read source code of any module, class, or function
- **patch_module(module_path, source)** — Inject new Python code into runtime (creates or replaces a module)
- **run_code(code)** — Execute Python code and capture output
- **save_module(module_path, file_path)** — Persist a runtime-patched module to disk

## Workflow
When modifying code, follow this cycle:
1. **inspect_module** — Understand the current structure
2. **view_source** — Read the specific code to change
3. **patch_module** — Apply changes in runtime (with @impl override=True for existing methods)
4. **run_code** — Verify the change works
5. **save_module** — Persist to file once validated

## Key Concepts
- **Declaration (.py)** = stable interface (class + stub methods). Safe to import.
- **Implementation (.impl.py)** = replaceable logic via @impl. Loaded by mutagent's ImplLoader.
- **patch = write file + restart**: patching a module completely replaces its namespace.
- **MutagentMeta**: classes that inherit mutagent.Object are updated in-place on redefinition (id preserved, isinstance works, @impl survives).
- **Module path is first-class**: everything is addressed as `package.module.Class.method`.

## Self-Evolution
You can evolve yourself:
- Override any existing tool implementation: patch a new .impl.py with @impl(Method, override=True)
- Create entirely new tool classes: define a new mutagent.Object subclass with method stubs, then provide @impl
- Extend ToolSelector: patch its get_tools/dispatch to include new tools

## Guidelines
- Always verify changes with run_code before saving.
- When patching declarations, remember MutagentMeta preserves class identity.
- When patching implementations, the old @impl is automatically unregistered.
- Use Chinese or English based on the user's language.
"""


@mutagent.impl(Main.setup_agent)
def setup_agent(self, system_prompt: str = "") -> Agent:
    model = self.config.get_model()
    module_manager = ModuleManager()
    tools = EssentialTools(module_manager=module_manager)
    selector = ToolSelector(essential_tools=tools)
    client = LLMClient(
        model=model.get("model_id", ""),
        api_key=model.get("auth_token", ""),
        base_url=model.get("base_url", ""),
    )
    if not system_prompt:
        system_prompt = (
            "You are a Python AI Agent with the ability to inspect, modify, "
            "and run Python code at runtime. Use the available tools to help "
            "the user with their tasks."
        )
    self.agent = Agent(
        client=client,
        tool_selector=selector,
        system_prompt=system_prompt,
        messages=[],
    )
    return self.agent


@mutagent.impl(Main.run)
async def run(self) -> None:
    self.setup_agent(system_prompt=SYSTEM_PROMPT)
    model = self.config.get_model()
    print(f"mutagent ready  (model: {model.get('model_id', '?')})")
    print("Type your message. Empty line or Ctrl+C to exit.\n")

    async for event in self.agent.run(_input_stream()):
        if event.type == "text_delta":
            print(event.text, end="", flush=True)
        elif event.type == "tool_exec_start":
            name = event.tool_call.name if event.tool_call else "?"
            args_summary = _summarize_args(
                event.tool_call.arguments if event.tool_call else {}
            )
            if args_summary:
                print(f"\n  [{name}({args_summary})]", flush=True)
            else:
                print(f"\n  [{name}]", flush=True)
        elif event.type == "tool_exec_end":
            if event.tool_result:
                status = "error" if event.tool_result.is_error else "done"
                summary = event.tool_result.content[:100]
                if len(event.tool_result.content) > 100:
                    summary += "..."
                print(f"  -> [{status}] {summary}", flush=True)
        elif event.type == "error":
            print(f"\n[Error: {event.error}]", file=sys.stderr, flush=True)
        elif event.type == "turn_done":
            print()

    print("Bye.")


async def _input_stream():
    """Async generator that reads user input from stdin."""
    loop = asyncio.get_event_loop()
    while True:
        try:
            user_input = await loop.run_in_executor(None, input, "> ")
        except (EOFError, KeyboardInterrupt):
            return
        if not user_input.strip():
            return
        yield InputEvent(type="user_message", text=user_input)


def _summarize_args(args: dict) -> str:
    """Create a short summary of tool call arguments."""
    if not args:
        return ""
    parts = []
    for key, value in args.items():
        s = str(value)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{key}={s}")
    return ", ".join(parts)
