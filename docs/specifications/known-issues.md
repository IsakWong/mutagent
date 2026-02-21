# 已知问题与改进建议

**日期**：2026-02-21
**类型**：问题记录

## 1. Agent 行为问题

### 1.1 Agent 通过 define_module 重写核心模块时频繁引入语法错误

**会话**：`20260221_082415`，API 调用 #26-#30

**现象**：
- Agent 尝试通过 `define_module` 重写整个 `userio_impl.py`（~300 行）
- 5 次尝试均失败，分别引入不同的语法错误：
  - #26: 行 152 出现 stray 引号 `'            ps['handler'] = None`（单引号意外出现在行首）
  - #27: 行 186 `bold_red('[Error: ' ' + event.error + ']')` 引号嵌套错误
  - #28: 行 269 `IndentationError: unexpected indent`
  - #29: 行 100 `SyntaxError: invalid syntax`
  - #30: 行 100 同上
- 每次 define_module 输出 ~2000-2600 tokens，浪费大量资源

**根因分析**：
- LLM 在 JSON 字符串中编写大段 Python 源码时，需要对引号、反斜杠进行双重转义
- 300 行代码在 JSON string 中极易出现转义错误
- Agent 没有采用增量修改策略（如只修改 2 个函数），而是每次重写整个模块

**建议**：
- [ ] 后续考虑为 Agent 提供增量代码修改工具（类似 patch/diff），减少全量重写
- [ ] 考虑添加 `define_module` 的 source 行数限制警告
- [ ] system prompt 中引导 Agent 优先使用 `@impl` 覆盖单个方法，而非重写整个模块

### 1.2 Agent 尝试重写框架核心模块

**会话**：`20260221_082415`，API 调用 #28-#30

**现象**：
- Agent 前两次尝试失败后，转而尝试直接重写 `mutagent.builtins.userio_impl`
- 触发了框架保护警告（`Redefining framework module`）
- 仍然失败（语法错误）

**建议**：
- [ ] 当前 `define_module` 对框架模块只是 warning，可考虑是否需要更强的保护
- [ ] 或者在 system prompt 中明确禁止重写 `mutagent.builtins.*` 模块

## 2. 日志分析工具改进建议

### 2.1 当前工具评估

在本次日志分析过程中（作为 Claude Code 分析 mutagent 会话日志），使用了以下命令：

| 命令 | 用途 | 效率评估 |
|------|------|----------|
| `sessions` | 列出会话 | ✅ 直观有效 |
| `logs -s SID -l WARNING` | 按级别过滤 | ✅ 快速定位错误 |
| `api -s SID` | 列出 API 调用 | ✅ 时间线清晰 |
| `api -s SID --tool NAME` | 按工具名过滤 | ✅ 有用 |
| `api-detail SID N` | 查看完整 API 调用 | ⚠️ 输出过大 |
| `api-detail SID N -f FIELD` | 按字段提取 | ✅ 但字段路径不直观 |
| `logs -s SID -p PATTERN` | 正则搜索 | ✅ 灵活 |

### 2.2 具体改进建议

#### P1: API 调用摘要视图增强

**问题**：`api` 命令的摘要只显示 `user: "text" → tool_use (N tools)` 或 `user: [tool_result] → end_turn`，无法快速看到：
- 工具调用的名称和参数摘要
- 工具结果是否出错
- end_turn 时 LLM 输出的文本摘要

**建议**：增加 `--verbose` / `-v` 模式显示更多信息：
```
#26 | 00:28:03 | user: [tool_result] → tool_use | 36s | 18431→2598 tok
     define_module(module_path="userio_impl_fix", source="...300 lines...")
#27 | 00:28:38 | user: [tool_result:error] → tool_use | 35s | 21069→2587 tok
     define_module(module_path="userio_impl_fix", source="...300 lines...")
```

#### P2: 工具错误快速查看

**问题**：当前无法直接列出所有工具调用的成功/失败状态。需要先 `logs -p "tool_result"` 找日志，或逐个 `api-detail` 查看。

**建议**：添加 `tools` 子命令，列出所有工具调用及结果状态：
```bash
python -m mutagent.cli.log_query tools -s 20260221_082415
# 输出：
#  #01 inspect_module(module_path="") → ok (1308 chars)
#  #02 view_source(target="mutagent.userio.BlockHandler") → ok (800 chars)
#  ...
#  #26 define_module(module_path="userio_impl_fix") → error: SyntaxError
#  #27 define_module(module_path="userio_impl_fix") → error: SyntaxError
```

#### P3: API 调用链路追踪

**问题**：`api` 输出的 `user: [tool_result]` 没有关联是哪个工具的结果。需要根据序号推断上下文。

**建议**：在 tool_result 行显示对应的工具名：
```
#27 | 00:28:38 | user: [tool_result:define_module] → tool_use (1 tools) | ...
```

#### P4: 会话级别统计

**建议**：在 `sessions` 命令输出中添加工具成功/失败统计：
```
Session          Logs  API  Tools(ok/err)  Duration
20260221_082415  444   35   25/4           7m44s
```
