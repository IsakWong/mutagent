"""Default implementation for mutagent.main.App methods."""

from __future__ import annotations

import importlib
import logging
import os
import sys

import mutagent
from mutagent.config import Config
from mutagent.agent import Agent
from mutagent.toolkits.agent_toolkit import AgentToolkit
from mutagent.client import LLMClient
from mutagent.toolkits.log_toolkit import LogToolkit
from mutagent.main import App
from mutagent.toolkits.module_toolkit import ModuleToolkit
from mutagent.runtime.module_manager import ModuleManager
from mutagent.runtime.log_store import (
    LogStore, LogStoreHandler, SingleLineFormatter, ToolLogCaptureHandler,
)
from mutagent.runtime.api_recorder import ApiRecorder
from mutagent.tools import ToolSet
from mutagent.userio import UserIO

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are **mutagent**, a self-evolving Python AI Agent framework.

## Identity
You are built on the mutobj declaration-implementation separation pattern. \
Your own source code is organized as declarations (.py) with implementations (_impl.py), \
and you can inspect, modify, and hot-reload any of it at runtime — including yourself.

## Core Tools
- **inspect(module_path, depth)** — Browse module structure. Call with no arguments to see unsaved modules.
- **view_source(target)** — Read source code of any module, class, or function.
- **define(module_path, source)** — Define or redefine a Python module in memory (not persisted until saved).
- **save(module_path, level)** — Persist a module to disk. level="project" (default, ./.mutagent/) or "user" (~/.mutagent/).
- **query(pattern, level, limit, tool_capture)** — Search logs or configure logging. \
Use tool_capture="on" to include logs in tool output for debugging.

## Tool Development
To create a new tool, define a Toolkit subclass with `define_module`:

    define_module("my_tools", \"\"\"
    import mutagent

    class MyTools(mutagent.Toolkit):
        def my_tool(self, arg: str, count: int = 1) -> str:
            '''Tool description.

            Args:
                arg: Argument description.
                count: How many times. Default 1.

            Returns:
                Result description.
            '''
            return arg * count
    \"\"\")

The tool is **automatically available** after define — no registration needed. \
Test it by calling it directly. If the result is wrong, redefine the module — changes take effect immediately. \
Once validated, use save to persist.

Rules:
- Every tool method MUST have type annotations and a Google-style docstring with Args section.
- The docstring is shown to you as the tool description — write it clearly.
- Test with diverse inputs before saving.
- Keep one Toolkit class per module for clarity.
- To create test functions, define them as methods on a Toolkit subclass (e.g. test_my_tool).

## Workflow
When modifying code, follow this cycle:
1. **inspect** — Understand the current structure
2. **view_source** — Read the specific code to change
3. **define** — Apply changes in runtime (module is in memory only)
4. **inspect** — Verify the module structure is correct
5. **view_source** — Verify the code was applied as expected
6. **save** — Persist to disk once validated

Do NOT create throwaway test modules. Validate changes by inspecting and viewing source.

## Module Naming
- New modules should use **functional names** based on their purpose (e.g. "web_search", "file_utils", "math_tools").
- Do NOT place new modules under the "mutagent" namespace. The mutagent namespace is for the framework itself.
- NEVER redefine existing mutagent.* modules with define — this replaces the entire module. \
To change a specific behavior, create a new _impl module and use @impl to override just that method.
- Modules are saved to .mutagent/ directories which are automatically in sys.path.

## Key Concepts
- **Declaration (.py)** = stable interface (class + stub methods). Safe to import.
- **Implementation (_impl.py)** = replaceable logic via @impl. Loaded at startup via direct import.
- **define_module = write + restart**: defining a module completely replaces its namespace.
- **DeclarationMeta**: classes that inherit mutagent.Declaration are updated in-place on redefinition (id preserved, isinstance works, @impl survives).
- **Toolkit**: classes that inherit mutagent.Toolkit have their public methods auto-discovered as tools.
- **Module path is first-class**: everything is addressed as `package.module.Class.method`.
- **Namespace packages**: submodules of the same package can live in different .mutagent/ directories (project-level and user-level).

## Debugging
- All internal logs (DEBUG level) are captured in memory.
- Use query() to view recent activity or search for specific events.
- Use query(tool_capture="on") to attach logs to tool results — useful for diagnosing issues.
- API calls are automatically recorded to .mutagent/logs/ for session replay.

## Self-Evolution
You can evolve yourself:
- Override any existing tool implementation: create a NEW module (e.g. "my_agent_impl") with @impl(Agent.run), \
then define + save. Do NOT redefine mutagent.agent or mutagent.builtins.* — later @impl registrations auto-override.
- Create entirely new tool classes: define a new mutagent.Toolkit subclass — its methods become tools automatically.
- Extend ToolSet: add new tools to the Agent's tool set.

## Task Discipline
- Complete the CORE task first. Do NOT create additional versions, documentation modules, \
demos, guides, or summaries unless explicitly requested.
- After completing the core implementation, STOP and report results to the user. \
Let the user decide if further work is needed.
- define is for CODE only. Do NOT use it to create documentation, READMEs, \
guides, or text content. If the user needs documentation, describe it in your response text.
- Keep module names lowercase_with_underscores. Do NOT use ALL_CAPS module names.
- Before calling define, carefully review your source code for:
  - Indentation errors (Python is whitespace-sensitive)
  - Full-width characters in code (use ASCII punctuation only)
  - Import errors (verify the library is available)

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
    from pathlib import Path
    from datetime import datetime

    model = self.config.get_model()

    # --- UserIO setup ---
    import mutagent.builtins.block_handlers  # noqa: F401  -- register BlockHandler subclasses
    from mutagent.builtins.userio_impl import discover_block_handlers
    self.userio = UserIO(block_handlers=discover_block_handlers())

    # --- Logging setup ---
    session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(self.config.get("logging.log_dir", ".mutagent/logs"))

    # 1. Create LogStore (in-memory, no capacity limit)
    log_store = LogStore()

    # 2. Configure Python logging
    root_logger = logging.getLogger("mutagent")
    root_logger.setLevel(logging.DEBUG)

    # Memory handler — message only (timestamp stored in LogEntry.timestamp)
    mem_handler = LogStoreHandler(log_store)
    mem_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(mem_handler)

    # 3. File handler (default on)
    if self.config.get("logging.file_log", True):
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_dir / f"{session_ts}-log.log", encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(SingleLineFormatter(
            "%(asctime)s %(levelname)-8s %(name)s - %(message)s"
        ))
        root_logger.addHandler(file_handler)

    # 4. Tool log capture handler (always installed, activated via flag)
    capture_handler = ToolLogCaptureHandler()
    capture_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s - %(message)s"
    ))
    root_logger.addHandler(capture_handler)

    logger.info("Logging initialized (session=%s)", session_ts)

    # --- API Recorder ---
    api_recorder = None
    if self.config.get("logging.api_record", True):
        log_dir.mkdir(parents=True, exist_ok=True)
        api_mode = self.config.get("logging.api_record_mode", "incremental")
        api_recorder = ApiRecorder(log_dir, mode=api_mode, session_ts=session_ts)
        logger.info("API recorder started (mode=%s)", api_mode)

    # --- Components ---
    search_dirs = [
        Path.home() / ".mutagent",
        Path.cwd() / ".mutagent",
    ]
    module_manager = ModuleManager(search_dirs=search_dirs)
    module_tools = ModuleToolkit(module_manager=module_manager)
    log_tools = LogToolkit(log_store=log_store)
    tool_set = ToolSet(auto_discover=True)
    tool_set.add(module_tools)
    tool_set.add(log_tools)
    client = LLMClient(
        model=model.get("model_id", ""),
        api_key=model.get("auth_token", ""),
        base_url=model.get("base_url", ""),
        api_recorder=api_recorder,
    )

    # --- Sub-Agents & AgentToolkit ---
    agents_config = self.config.get("agents", {})
    if agents_config:
        sub_agents = {}
        for agent_name, agent_conf in agents_config.items():
            # Create sub-agent ToolSet with specified tools
            sub_tool_set = ToolSet()
            sub_tool_methods = agent_conf.get("tools", [])
            if sub_tool_methods:
                sub_tool_set.add(module_tools, methods=sub_tool_methods)
                # Add log tools if query is requested
                if "query" in sub_tool_methods:
                    sub_tool_set.add(log_tools, methods=["query"])
            else:
                sub_tool_set.add(module_tools)
                sub_tool_set.add(log_tools)

            # Use specified model or share the main client
            sub_model_name = agent_conf.get("model")
            if sub_model_name:
                sub_model = self.config.get_model(sub_model_name)
                sub_client = LLMClient(
                    model=sub_model.get("model_id", ""),
                    api_key=sub_model.get("auth_token", ""),
                    base_url=sub_model.get("base_url", ""),
                    api_recorder=api_recorder,
                )
            else:
                sub_client = client

            sub_prompt = agent_conf.get("system_prompt", f"You are a sub-agent named '{agent_name}'.")
            sub_agent = Agent(
                client=sub_client,
                tool_set=sub_tool_set,
                system_prompt=sub_prompt,
                messages=[],
            )
            sub_tool_set.agent = sub_agent
            sub_agents[agent_name] = sub_agent
            logger.info("Sub-agent '%s' created (tools=%s)", agent_name,
                        [t.name for t in sub_tool_set.get_tools()])

        agent_toolkit = AgentToolkit(agents=sub_agents)
        tool_set.add(agent_toolkit, methods=["delegate"])
        logger.info("AgentToolkit registered with %d sub-agents", len(sub_agents))

    # Record session metadata
    if api_recorder is not None:
        effective_prompt = system_prompt or SYSTEM_PROMPT
        tool_schemas = tool_set.get_tools()
        api_recorder.start_session(
            model=client.model,
            system_prompt=effective_prompt,
            tools=[{"name": t.name, "description": t.description} for t in tool_schemas],
        )

    if not system_prompt:
        system_prompt = (
            "You are a Python AI Agent with the ability to inspect, modify, "
            "and run Python code at runtime. Use the available tools to help "
            "the user with their tasks."
        )
    self.agent = Agent(
        client=client,
        tool_set=tool_set,
        system_prompt=system_prompt,
        messages=[],
    )
    tool_set.agent = self.agent
    return self.agent


@mutagent.impl(App.run)
def run(self) -> None:
    self.setup_agent(system_prompt=SYSTEM_PROMPT)
    model = self.config.get_model()
    print(f"mutagent ready  (model: {model.get('model_id', '?')})")
    print("Type your message. Ctrl+C to exit.\n")

    while True:
        try:
            for event in self.agent.run(self.userio.input_stream()):
                self.userio.render_event(event)
            # End session
            break
        except KeyboardInterrupt:
            print("\n[User interrupted]")
        except Exception as e:
            print(f"\n[Error: {e}]", file=sys.stderr, flush=True)
