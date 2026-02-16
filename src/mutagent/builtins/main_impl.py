"""Default implementation for mutagent.main.App methods."""

from __future__ import annotations

import importlib
import os
import sys

import mutagent
from mutagent.config import Config
from mutagent.agent import Agent
from mutagent.client import LLMClient
from mutagent.essential_tools import EssentialTools
from mutagent.main import App
from mutagent.messages import InputEvent
from mutagent.runtime.module_manager import ModuleManager
from mutagent.selector import ToolSelector

SYSTEM_PROMPT = """\
You are **mutagent**, a self-evolving Python AI Agent framework.

## Identity
You are built on the mutobj declaration-implementation separation pattern. \
Your own source code is organized as declarations (.py) with implementations (_impl.py), \
and you can inspect, modify, and hot-reload any of it at runtime — including yourself.

## Core Tools
You have 4 essential tools:
- **inspect_module(module_path, depth)** — Browse module structure. Call with no arguments to see unsaved modules.
- **view_source(target)** — Read source code of any module, class, or function.
- **define_module(module_path, source)** — Define or redefine a Python module in memory (not persisted until saved).
- **save_module(module_path, level)** — Persist a module to disk. level="project" (default, ./.mutagent/) or "user" (~/.mutagent/).

## Workflow
When modifying code, follow this cycle:
1. **inspect_module** — Understand the current structure
2. **view_source** — Read the specific code to change
3. **define_module** — Apply changes in runtime (module is in memory only)
4. **inspect_module** — Check unsaved modules list (call with no arguments)
5. **save_module** — Persist to disk once validated

## Module Naming
- New modules should use **functional names** based on their purpose (e.g. "web_search", "file_utils", "math_tools").
- Do NOT place new modules under the "mutagent" namespace. The mutagent namespace is for the framework itself.
- Modules are saved to .mutagent/ directories which are automatically in sys.path.

## Key Concepts
- **Declaration (.py)** = stable interface (class + stub methods). Safe to import.
- **Implementation (_impl.py)** = replaceable logic via @impl. Loaded at startup via direct import.
- **define_module = write + restart**: defining a module completely replaces its namespace.
- **DeclarationMeta**: classes that inherit mutagent.Declaration are updated in-place on redefinition (id preserved, isinstance works, @impl survives).
- **Module path is first-class**: everything is addressed as `package.module.Class.method`.
- **Namespace packages**: submodules of the same package can live in different .mutagent/ directories (project-level and user-level).

## Self-Evolution
You can evolve yourself:
- Override any existing tool implementation: define a new _impl.py with @impl(Method) — later registrations auto-override
- Create entirely new tool classes: define a new mutagent.Declaration subclass with method stubs, then provide @impl
- Extend ToolSelector: define its get_tools/dispatch to include new tools

## Guidelines
- When redefining declarations, remember DeclarationMeta preserves class identity.
- When redefining implementations, the old @impl is automatically unregistered.
- Use Chinese or English based on the user's language.
"""

@mutagent.impl(App.load_config)
def load_config(self, config_path) -> None:
    self.config = Config.load(config_path)

    # Set environment variables from config (later layers override earlier ones)
    for key, value in self.config.get("env", {}).items():
        os.environ[key] = value

    # Auto-register .mutagent/ directories to sys.path
    # User-level first (lower priority), then project-level (higher priority)
    from pathlib import Path
    for mutagent_dir in [
        str(Path.home() / ".mutagent"),
        str(Path.cwd() / ".mutagent"),
    ]:
        if mutagent_dir not in sys.path:
            sys.path.insert(0, mutagent_dir)

    # Extend sys.path from config (paths already resolved to absolute in Config.load)
    for p in self.config.get("path", []):
        if p not in sys.path:
            sys.path.insert(0, p)

    # Load extension modules (may override @impl)
    for module_name in self.config.get("modules", []):
        importlib.import_module(module_name)


@mutagent.impl(App.setup_agent)
def setup_agent(self, system_prompt: str = "") -> Agent:
    model = self.config.get_model()
    from pathlib import Path
    search_dirs = [
        Path.home() / ".mutagent",
        Path.cwd() / ".mutagent",
    ]
    module_manager = ModuleManager(search_dirs=search_dirs)
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


@mutagent.impl(App.handle_stream_event)
def handle_stream_event(self, event):
    """Default event handler that prints to console."""
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


@mutagent.impl(App.run)
def run(self) -> None:
    self.setup_agent(system_prompt=SYSTEM_PROMPT)
    model = self.config.get_model()
    print(f"mutagent ready  (model: {model.get('model_id', '?')})")
    print("Type your message. Ctrl+C to exit.\n")

    while True:
        try:
            for event in self.agent.run(self.input_stream()):
                self.handle_stream_event(event)
            # End session
            break
        except KeyboardInterrupt:
            print("\n[User interrupted]")
        except Exception as e:
            print(f"\n[Error: {e}]", file=sys.stderr, flush=True)


@mutagent.impl(App.input_stream)
def input_stream(self):
    """Generator that reads user input from stdin."""
    while True:
        try:
            while True:
               user_input = input("> ").strip()
               if user_input:
                    break
            yield InputEvent(type="user_message", text=user_input)
        except KeyboardInterrupt:
            if self.confirm_exit():
                print("Bye.")
                return
        except EOFError:
            return


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


@mutagent.impl(App.confirm_exit)
def confirm_exit(self) -> bool:
    """Ask user to confirm exit after an interruption."""
    for _ in range(3):  # allow up to 3 attempts to confirm
        try:
            choice = input("\nDo you want to exit? (Y/n) ").strip().lower()
        except KeyboardInterrupt:
            continue
        if choice in ("y", "yes", ""):
            return True
        elif choice in ("n", "no"):
            return False
    print("")
    return True
