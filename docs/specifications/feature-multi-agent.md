# 多 Agent 架构设计规范

**状态**：🔄 进行中
**日期**：2026-02-17
**类型**：功能设计

## 1. 背景

### 1.1 需求来源

在 `feature-dynamic-tool-registration.md` 的设计迭代中，工具动态选择的方案逐步演进：
1. 预配置工具列表 → 不够灵活
2. `request_tool` 函数 → LLM 描述需求，系统匹配
3. Sub-Agent 委派 → 专门的 Agent 负责工具发现/创建

第 3 步揭示了一个更大的架构需求：**mutagent 需要多 Agent 协作能力**，而非仅仅解决工具发现问题。

### 1.2 参考

Claude Code 通过 `Task` 工具派生 Explore、Plan、Bash 等专门的 Sub-Agent，每种有自己的工具集和上下文。mutagent 需要类似但更灵活的机制——因为 mutagent 的 Agent 和工具本身是可演化的。

### 1.3 目标

设计多 Agent 协作框架，支持：
- 单一 Agent 类，通过不同配置（system_prompt、工具集）区分角色
- ToolSet 抽象，管理 Agent 可用工具的增删查改
- delegate 工具，管理预创建的 Sub-Agent 实例列表
- 嵌套控制，通过工具集组合限制 Agent 能力边界

## 2. 设计方案

### 2.1 核心设计：单一 Agent 类

**不引入 AgentType 子类**。所有 Agent 都是同一个 `Agent` 类的实例，差异仅在构造参数：

```python
class Agent(mutagent.Declaration):
    client: LLMClient
    tool_set: ToolSet        # 替代原 tool_selector
    system_prompt: str
    messages: list

    def run(self, input_stream, stream=True) -> Iterator[StreamEvent]: ...
    def step(self, stream=True) -> Iterator[StreamEvent]: ...
    def handle_tool_calls(self, tool_calls) -> list[ToolResult]: ...
```

**关键原则**：
- **不需要派生**：不同角色的 Agent 只是配置不同（system_prompt 描述角色，tool_set 决定能力），行为（run/step 循环）完全相同
- **统一流式接口**：所有 Agent 包括 Sub-Agent 都使用相同的流式 `run()` 方法
- **预创建实例**：Sub-Agent 在系统初始化时创建好，delegate 工具持有这些实例的引用

**角色通过配置表达**：

| 角色 | system_prompt | tool_set 内容 |
|------|---------------|---------------|
| system（入口） | 通用系统提示 | 核心工具 + delegate |
| tool_provider | "你是工具匹配专家..." | 无工具（纯推理） |
| tool_creator | "你是工具开发者..." | define_module, save_module 等 |
| researcher | "你是研究员..." | web_search 等 |

**与现有架构的变更**：
- `Agent.tool_selector: ToolSelector` → `Agent.tool_set: ToolSet`
- `Agent.run()` / `step()` 的核心循环不变，仅将 `tool_selector.get_tools()` / `tool_selector.dispatch()` 替换为 `tool_set.get_tools()` / `tool_set.dispatch()`
- 不同 Agent 可使用不同的 LLMClient（如 system 用高能力模型，简单 Sub-Agent 用低成本模型）

### 2.2 ToolSet：工具集管理

ToolSet 替代当前的 ToolSelector，作为 Agent 可用工具的统一管理器。相比 ToolSelector 的静态工具列表，ToolSet 提供动态的增删查改能力。

```python
class ToolSet(mutagent.Declaration):
    """Agent 的工具集管理器。"""
    agent: Agent | None      # 所属 Agent 的引用（绑定后设置）

    def add(self, source, methods: list[str] | None = None) -> None:
        """添加工具。

        Args:
            source: 工具来源，可以是：
                - 对象实例：注册其公开方法作为工具
                - 单个可调用对象：注册为一个工具
            methods: 当 source 是对象时，指定要注册的方法名列表。
                     为 None 时注册所有公开方法。
        """
        ...

    def remove(self, tool_name: str) -> bool:
        """移除指定名称的工具。"""
        ...

    def query(self, tool_name: str) -> ToolSchema | None:
        """查询指定工具的 Schema。"""
        ...

    def get_tools(self) -> list[ToolSchema]:
        """获取所有可用工具的 Schema 列表（供 LLM API 调用）。"""
        ...

    def dispatch(self, tool_call: ToolCall) -> ToolResult:
        """分发工具调用到对应的实现方法。"""
        ...
```

**内部存储**：每个工具注册为一个 ToolEntry 条目：

```python
@dataclass
class ToolEntry:
    name: str               # 工具名称（唯一标识）
    callable: Callable      # 实际的可调用对象（bound method 或函数）
    schema: ToolSchema      # LLM API 所需的 Schema
    source: Any             # 来源对象引用（用于按来源批量移除）
```

`add(obj)` 时，遍历 `type(obj).__dict__` 中不以 `_` 开头的方法（只取当前类直接定义的方法，不含基类继承的方法），为每个方法生成 `ToolEntry`。`add(obj, methods=["foo"])` 时只注册指定方法。如果基类也有需要注册的工具方法，单独注册基类实例即可。Schema 生成机制见独立文档 `feature-tool-schema-annotation.md`。

**ToolSet 持有 Agent 引用**：ToolSet 可以访问所属 Agent 的状态（如对话历史、client 信息）。绑定关系在 Agent 构造时建立：

```python
agent = Agent(
    client=client,
    tool_set=tool_set,
    system_prompt="...",
    messages=[],
)
tool_set.agent = agent  # 反向绑定
```

**使用示例**：

```python
# 创建工具集并注册工具
system_tools = ToolSet()
system_tools.add(essential_tools)                          # 注册 EssentialTools 的所有方法
system_tools.add(delegate_tool, methods=["delegate"])      # 注册 delegate 方法

# 为 Sub-Agent 创建不同的工具集
creator_tools = ToolSet()
creator_tools.add(essential_tools, methods=["define_module", "save_module", "inspect_module"])

researcher_tools = ToolSet()
researcher_tools.add(web_search_tools)
```

### 2.3 delegate 工具与多 Agent 协作

delegate 管理一组**预创建的 Agent 实例**。每个 Sub-Agent 在系统初始化时创建好，拥有自己的 ToolSet 和 system_prompt。每次 delegate 调用前清空 Sub-Agent 的 messages（无跨任务记忆，每次调用是独立任务）。

```python
class DelegateTool:
    """delegate 工具实现。持有可委派的 Sub-Agent 实例。"""
    agents: dict[str, Agent]    # 预创建的 Sub-Agent 实例

    def delegate(self, agent_name: str, task: str) -> str:
        '''将任务委派给指定的 Sub-Agent。

        Args:
            agent_name: Sub-Agent 名称
            task: 要执行的任务描述

        Returns:
            Sub-Agent 的执行结果
        '''
        agent = self.agents.get(agent_name)
        if agent is None:
            return f"Unknown agent: {agent_name}. Available: {list(self.agents.keys())}"

        # 清空消息历史（每次调用独立）
        agent.messages.clear()

        # 构造输入流，调用 Sub-Agent 的标准 run()
        input_stream = self._make_input_stream(task)
        result_text = self._collect_result(agent.run(input_stream))
        return result_text
```

**嵌套控制**：
- Sub-Agent 的 ToolSet 中不包含 delegate → 自然无法嵌套
- 如需允许嵌套，为 Sub-Agent 的 ToolSet 注册一个范围更小的 DelegateTool（只含部分 Agent）
- 通过 ToolSet 的工具组合精确控制每个 Agent 的能力边界

#### 执行模式：同步阻塞 → 流式透传

**初始版本：同步阻塞**

delegate 作为普通工具调用，内部完整运行 Sub-Agent 的 `run()`，收集所有 StreamEvent，提取最终文本作为工具结果字符串返回。与现有工具调用模型一致。

**演进版本：流式透传**

当 Sub-Agent 产生大量输出时，同步阻塞会导致用户长时间无反馈。流式透传让 Sub-Agent 的输出实时呈现给用户。

核心思路：`dispatch` 支持返回 Iterator（流式结果）或 ToolResult（普通结果），Agent 循环根据返回类型分别处理：

```python
# ToolSet.dispatch 扩展：支持流式返回
def dispatch(self, tool_call: ToolCall) -> ToolResult | Iterator:
    result = method(**tool_call.arguments)
    if isinstance(result, Iterator):
        return result   # 流式：交给 Agent 循环处理
    else:
        return ToolResult(tool_call_id=tool_call.id, content=str(result))
```

```python
# Agent 循环中的流式处理（agent_impl.py）
for call in response.message.tool_calls:
    result = self.tool_set.dispatch(call)
    if isinstance(result, ToolResult):
        # 普通工具：直接收集结果
        results.append(result)
    else:
        # 流式工具（如 delegate）：转发事件，收集最终文本
        collected = []
        for event in result:
            if isinstance(event, StreamEvent):
                yield event                     # 转发给父 Agent 输出流
                if event.type == "text_delta":
                    collected.append(event.text)
            elif isinstance(event, ToolResult):
                results.append(event)           # 最终结果
                break
        else:
            # Iterator 耗尽，用收集的文本作为结果
            text = "".join(collected)
            results.append(ToolResult(tool_call_id=call.id, content=text))
```

```python
# DelegateTool 流式版本
def delegate(self, agent_name: str, task: str) -> Iterator:
    agent = self.agents[agent_name]
    agent.messages.clear()
    input_stream = self._make_input_stream(task)
    # 直接 yield Sub-Agent 的流式事件
    for event in agent.run(input_stream):
        yield event
    # Agent 循环会收集文本并生成 ToolResult
```

这个设计的优点：
- **向后兼容**：普通工具仍返回 ToolResult，无需修改
- **按需流式**：只有 delegate 等需要流式的工具返回 Iterator
- **透明传递**：Sub-Agent 的 StreamEvent 直接传递到父 Agent 的输出流，用户实时看到 Sub-Agent 的输出

**完整初始化示例**：

```python
# 1. 创建共享资源
client = LLMClient(model=..., api_key=..., base_url=...)
essential_tools = EssentialTools(module_manager=mm, log_store=ls)

# 2. 创建 Sub-Agent（可用不同 client/模型）
researcher = Agent(
    client=LLMClient(model="haiku", ...),  # 低成本模型
    tool_set=ToolSet(),
    system_prompt="你是一个研究员。搜索和整理信息。",
    messages=[],
)

tool_creator = Agent(
    client=client,  # 与 system 共享
    tool_set=ToolSet(),
    system_prompt="你是一个工具开发者。根据需求创建、测试并保存新工具。",
    messages=[],
)

# 3. 创建 delegate 工具
delegate_tool = DelegateTool(agents={
    "researcher": researcher,
    "tool_creator": tool_creator,
})

# 4. 创建 System Agent
system_tool_set = ToolSet()
system_tool_set.add(essential_tools)
system_tool_set.add(delegate_tool, methods=["delegate"])

system_agent = Agent(
    client=client,
    tool_set=system_tool_set,
    system_prompt="...",
    messages=[],
)
```

### 2.4 延伸设计（独立文档）

以下设计在本文的 Agent 架构确定后，分别在独立文档中迭代：

- **变量空间与结构化数据传递**：Agent 间通过 repr 风格标记传递结构化数据，作用域化变量存储。→ 新建 `feature-variable-space.md`
- **工具系统重设计**：ToolSet 替代 ToolSelector 的具体实现，EssentialTools 迁移路径。→ 更新 `feature-dynamic-tool-registration.md`
- **工具 Schema 生成**：annotation + docstring 替代 AST 解析的可行性。→ 新建 `feature-tool-schema-annotation.md`

## 3. 设计决策记录

本节记录迭代过程中已确认的关键设计决策：

- **单一 Agent 类**：不引入 AgentType 子类，所有 Agent 是同一类的不同配置实例
- **ToolSet 替代 ToolSelector**：动态增删查改，持有 Agent 引用
- **工具方法识别**：`add(obj)` 只注册 `type(obj).__dict__` 中当前类直接定义的公开方法，不含基类继承的方法
- **delegate 管理实例**：持有预创建的 Agent 实例，非类型模板
- **消息历史**：每次 delegate 调用前清空 Sub-Agent 的 messages
- **执行模式**：初始版本同步阻塞，演进版本流式透传（dispatch 返回 `ToolResult | Iterator`）
- **LLMClient**：不同 Agent 可使用不同 client/模型
- **ToolEntry 表示**：`(name, callable, schema, source)` 四元组

## 4. 实施步骤清单

### 阶段一：ToolSet 基础 [✅ 已完成]

- [x] **Task 1.1**: 定义 ToolSet 声明和 ToolEntry 数据类
  - [x] 在 `src/mutagent/` 中添加 ToolSet 声明（Declaration 子类）
  - [x] 定义 ToolEntry dataclass
  - 状态：✅ 已完成

- [x] **Task 1.2**: 实现 ToolSet 的 add/remove/query/get_tools/dispatch
  - [x] add：从对象方法或可调用对象生成 ToolEntry 并注册
  - [x] remove：按名称移除工具
  - [x] query：按名称查找 ToolSchema
  - [x] get_tools：返回所有工具的 Schema 列表
  - [x] dispatch：按 tool_call.name 查找并调用工具
  - 状态：✅ 已完成

- [x] **Task 1.3**: 迁移 Agent 从 ToolSelector 到 ToolSet
  - [x] 修改 Agent 声明：`tool_selector: ToolSelector` → `tool_set: ToolSet`
  - [x] 更新 agent_impl.py：使用 tool_set.get_tools() 和 tool_set.dispatch()
  - [x] 更新 main_impl.py：构造 ToolSet 并注册 EssentialTools
  - [x] 确保现有测试通过
  - 状态：✅ 已完成

### 阶段二：delegate 与多 Agent [✅ 已完成]

- [x] **Task 2.1**: 实现 DelegateTool（同步阻塞版）
  - [x] DelegateTool 类：持有 agents dict
  - [x] delegate 方法：清空消息 → 构造输入 → 运行 Sub-Agent → 收集结果
  - [x] 使用 Agent.run() 标准接口执行
  - 状态：✅ 已完成

- [x] **Task 2.2**: 更新 main_impl.py 支持多 Agent 初始化
  - [x] 从配置读取 Sub-Agent 定义（system_prompt、工具列表、模型）
  - [x] 创建 Sub-Agent 实例和对应的 ToolSet
  - [x] 创建 DelegateTool 并注册到 System Agent 的 ToolSet
  - 状态：✅ 已完成

- [x] **Task 2.3**: 测试验证
  - [x] 单元测试：DelegateTool 的 delegate 方法
  - [x] 集成测试：System Agent 通过 delegate 调用 Sub-Agent
  - 状态：✅ 已完成

### 阶段三：流式透传 [待开始]

- [ ] **Task 3.1**: 扩展 dispatch 支持 Iterator 返回
  - [ ] ToolSet.dispatch 检测流式返回
  - [ ] agent_impl.py 循环中处理流式事件转发
  - 状态：⏸️ 待开始

- [ ] **Task 3.2**: DelegateTool 流式版本
  - [ ] delegate 返回 Iterator，yield Sub-Agent 的 StreamEvent
  - [ ] 测试流式输出正确传递到父 Agent 输出
  - 状态：⏸️ 待开始

---

### 实施进度总结
- ✅ **阶段一：ToolSet 基础** - 100% 完成 (3/3任务)
- ✅ **阶段二：delegate 与多 Agent** - 100% 完成 (3/3任务)
- ⏸️ **阶段三：流式透传** - 0% (待开始)

**核心功能完成度：75%** (6/8核心任务)
**单元测试覆盖：303个测试全部通过**（其中30个为新增 ToolSet/DelegateTool 测试）

## 5. 测试验证

### 单元测试
- [x] ToolSet add/remove/query/get_tools/dispatch
- [x] ToolEntry 生成（从对象方法、从可调用对象）
- [x] DelegateTool 同步阻塞调用
- [x] Sub-Agent 消息清空验证
- 执行结果：30/30 通过

### 集成测试
- [x] System Agent → delegate → Sub-Agent 完整流程
- [x] 嵌套控制：Sub-Agent 无法调用 delegate
- [x] 不同 Agent 使用不同 LLMClient/模型（配置支持已实现）
- [ ] 流式透传（阶段三）
- 执行结果：全量303个测试通过
