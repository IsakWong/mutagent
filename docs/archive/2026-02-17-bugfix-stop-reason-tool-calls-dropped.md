# stop_reason=end_turn 时工具调用被静默丢弃 Bug 修复

**状态**：✅ 已完成
**日期**：2026-02-17
**类型**：Bug修复

## 1. 背景

### 问题现象

在会话 `20260217_085924` 中，使用模型 `ark-code-latest` 测试 mutagent 功能时，Agent 在调用工具（特别是 `inspect_module`）时出现"卡住"现象——Agent 输出了半截文字（如"现在查看未保存的模块："）后停止响应，等待用户输入。

### 日志证据

两次连续的工具调用丢弃：

```
609 | 09:05:57 INFO claude_impl - API response received (stop_reason=end_turn, duration=2111ms)
611 | 09:05:57 INFO agent_impl  - LLM stop_reason=end_turn, tool_calls=1    ← 工具未执行

615 | 09:06:25 INFO claude_impl - API response received (stop_reason=end_turn, duration=2184ms)
617 | 09:06:25 INFO agent_impl  - LLM stop_reason=end_turn, tool_calls=1    ← 工具再次未执行
```

API #10 的完整响应：
```json
{
  "response": {
    "content": [
      {"type": "text", "text": "现在查看未保存的模块："},
      {"type": "tool_use", "id": "call_5n0hr29tio4ibsobas4ndar7", "name": "inspect_module", "input": {}}
    ],
    "stop_reason": "end_turn"
  }
}
```

LLM 返回了 `stop_reason: "end_turn"`，但 content 中包含 `tool_use` 块。框架以 `stop_reason` 为准，静默丢弃了工具调用。

### 根因

`agent_impl.py:61` 的判断逻辑：

```python
if response.stop_reason == "tool_use" and response.message.tool_calls:
    # 执行工具
else:
    break  # 结束本轮 ← tool_calls 被丢弃
```

当 `stop_reason == "end_turn"` 但 `tool_calls` 非空时，走入 `else` 分支，工具调用被丢弃，轮次提前结束。

### 影响范围

- 不同 LLM provider 对 `stop_reason` 的设置行为不一致，`ark-code-latest` 会在包含 tool_use 的响应中返回 `end_turn`
- 所有依赖 `stop_reason == "tool_use"` 判断的逻辑都受影响
- 用户体验表现为 Agent "卡住"或"退出会话"

## 2. 设计方案

### 2.1 核心修复

修改 `agent_impl.py` 中的工具调用判断逻辑，**以 `tool_calls` 的存在性为主要依据**，而非仅依赖 `stop_reason`：

```python
# 修改前
if response.stop_reason == "tool_use" and response.message.tool_calls:

# 修改后
if response.message.tool_calls:
```

理由：
- `tool_calls` 是从 response content 中实际解析出的工具调用，是确定性事实
- `stop_reason` 是 LLM 的元信息标注，不同 provider 行为不一致
- Claude API 规范中，`stop_reason="tool_use"` 与 content 中存在 tool_use 块通常一致，但非所有兼容 API 都保证这一点

### 2.2 增加警告日志

当 `stop_reason` 与 `tool_calls` 存在性不一致时，记录 WARNING 日志辅助排查：

```python
if response.message.tool_calls and response.stop_reason != "tool_use":
    logger.warning(
        "stop_reason=%s but %d tool_calls found in response, executing tools anyway",
        response.stop_reason, len(response.message.tool_calls),
    )
```

### 2.3 变更范围

| 文件 | 变更 |
|------|------|
| `src/mutagent/builtins/agent_impl.py` | 修改第 61 行判断条件，新增 warning 日志 |
| `tests/test_agent.py` | 新增测试用例覆盖 `end_turn + tool_calls` 场景 |

## 3. 待定问题

（无。修复方案明确，无需额外决策。）

## 4. 实施步骤清单

### 阶段一：代码修复 [✅ 已完成]
- [x] **Task 1.1**: 修改 `agent_impl.py` 判断逻辑
  - [x] 将第 61 行 `if response.stop_reason == "tool_use" and response.message.tool_calls:` 改为 `if response.message.tool_calls:`
  - [x] 在判断前新增 `stop_reason` 与 `tool_calls` 不一致时的 WARNING 日志
  - 状态：✅ 已完成

### 阶段二：测试验证 [✅ 已完成]
- [x] **Task 2.1**: 新增测试用例
  - [x] 测试 `stop_reason=end_turn` 且 `tool_calls` 非空时，工具被正确执行
  - [x] 测试 `stop_reason=end_turn` 且 `tool_calls` 为空时，正常结束轮次（回归）
  - [x] 测试 `stop_reason=tool_use` 且 `tool_calls` 非空时，行为不变（回归）
  - 状态：✅ 已完成

- [x] **Task 2.2**: 运行全量测试
  - [x] `pytest tests/test_agent.py` 17/17 通过
  - [x] `pytest` 271 passed, 2 skipped
  - 状态：✅ 已完成

## 5. 测试验证

### 单元测试

新增测试用例：

| 测试用例 | 场景 | 预期行为 |
|----------|------|----------|
| `test_end_turn_with_tool_calls_executes_tools` | `stop_reason=end_turn`, `tool_calls=[inspect_module]` | 工具被执行，消息历史包含 tool_result |
| `test_end_turn_without_tool_calls_ends_turn` | `stop_reason=end_turn`, `tool_calls=[]` | 正常结束轮次（回归验证） |
| `test_tool_use_with_tool_calls_still_works` | `stop_reason=tool_use`, `tool_calls=[inspect_module]` | 行为不变（回归验证） |

### 回归测试
- 执行结果：271 passed, 2 skipped (0.93s)
