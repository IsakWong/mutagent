# Log Query CLI 增强 设计规范

**状态**：✅ 已完成
**日期**：2026-02-21
**类型**：功能设计
**来源**：known-issues.md 日志分析工具改进建议

## 背景

在使用 `python -m mutagent.cli.log_query` 分析 mutagent 会话日志时，现有子命令（`sessions`、`logs`、`api`、`api-detail`）在基本查询方面已满足需求，但在以下场景中效率不足：

- **工具调用诊断**：无法一次性看到工具调用名称、参数摘要和成功/失败状态，需逐个 `api-detail` 查看
- **错误定位**：`api` 摘要中 `user: [tool_result]` 不显示对应工具名，需手动推断上下文
- **会话概览**：`sessions` 命令不含工具统计和耗时，难以快速定位问题会话

### 当前工具评估

| 命令 | 用途 | 效率评估 |
|------|------|----------|
| `sessions` | 列出会话 | ✅ 直观有效 |
| `logs -s SID -l WARNING` | 按级别过滤 | ✅ 快速定位错误 |
| `api -s SID` | 列出 API 调用 | ✅ 时间线清晰 |
| `api -s SID --tool NAME` | 按工具名过滤 | ✅ 有用 |
| `api-detail SID N` | 查看完整 API 调用 | ⚠️ 输出过大 |
| `api-detail SID N -f FIELD` | 按字段提取 | ✅ 但字段路径不直观 |
| `logs -s SID -p PATTERN` | 正则搜索 | ✅ 灵活 |

## 设计方案

### `api` 子命令 verbose 模式（P1）

为 `api` 子命令增加 `--verbose` / `-v` 选项，在现有摘要行下方追加工具调用详情。

**数据来源**：API 记录（JSONL）中 `response.content` 里的 `tool_use` 块和下一条记录的 `input` 中的 `tool_result`。

**显示规则**：
- 当 response 的 `stop_reason` 为 `tool_use` 时，在摘要行下方缩进显示每个工具调用
- 格式：`     tool_name(param1="value1", param2="...N lines...")`
- 参数值超过 40 字符时截断为摘要（如 `"...300 lines..."`）
- 当 input 包含 `tool_result` 且 `is_error=true` 时，摘要行中标注 `:error`

**输出示例**：
```
#26 | 00:28:03 | user: [tool_result] → tool_use | 36s | 18431→2598 tok
     define_module(module_path="userio_impl_fix", source="...300 lines...")
#27 | 00:28:38 | user: [tool_result:error] → tool_use | 35s | 21069→2587 tok
     define_module(module_path="userio_impl_fix", source="...300 lines...")
```

**实现位置**：
- CLI 层：`src/mutagent/cli/log_query.py` — `api` 子命令增加 `-v` 参数
- 引擎层：`src/mutagent/runtime/log_query.py` — `_make_api_summary()` 扩展或新增 `_make_verbose_lines()`

### `tools` 子命令（P2）

新增 `tools` 子命令，专注于工具调用的列表与状态查看。

**功能**：从 API 记录中提取所有工具调用，显示每次调用的工具名、关键参数和执行结果状态。

**数据提取逻辑**：
1. 遍历 API JSONL 中 `type="call"` 的记录
2. 从 `response.content` 中提取 `type="tool_use"` 块：获取 `name` 和 `input` 字段
3. 从下一条记录的 `input.content`（`tool_result` 块）中获取 `is_error` 状态和结果长度
4. 工具调用编号按出现顺序从 1 开始（跨 API 调用连续编号）

**参数摘要规则**：
- 选取工具 input 中的前 2 个 key 作为参数摘要
- 字符串值超过 30 字符时截断为 `"value..."`
- 多行字符串显示为 `"...N lines..."`

**结果状态**：
- `ok (N chars)` — 正常返回，显示结果字符串长度
- `error: MESSAGE` — `is_error=true`，显示错误消息首行（截断至 60 字符）

**输出示例**：
```
python -m mutagent.cli.log_query tools -s 20260221_082415

 #01 inspect_module(module_path="") → ok (1308 chars)
 #02 view_source(target="mutagent.userio.BlockHandler") → ok (800 chars)
 ...
 #26 define_module(module_path="userio_impl_fix") → error: SyntaxError line 152
 #27 define_module(module_path="userio_impl_fix") → error: SyntaxError line 186
```

**CLI 参数**：
- `-s, --session`：会话时间戳（默认最新）
- `-t, --tool`：按工具名过滤
- `--errors`：仅显示失败的调用
- `-n, --limit`：最大显示数（默认无限制）

**实现位置**：
- CLI 层：`src/mutagent/cli/log_query.py` — 新增 `tools` 子命令
- 引擎层：`src/mutagent/runtime/log_query.py` — 新增 `query_tools()` 方法和 `ToolCallInfo` 数据类

### `api` 摘要中的 tool_result 关联（P3）

在 `api` 命令的摘要行中，当 input 包含 `tool_result` 时，显示对应的工具名。

**数据来源**：`tool_result` 块中的 `tool_use_id` 关联上一条记录 `response.content` 中同 `id` 的 `tool_use` 块。

**显示格式**：
- 单工具结果：`user: [tool_result:tool_name] → ...`
- 多工具结果：`user: [tool_result:tool1,tool2,...] → ...`
- 包含错误时：`user: [tool_result:tool_name:error] → ...`

**实现方式**：
- 在 `_make_api_summary()` 中，检查 input 是否包含 `tool_result` 类型的 content 块
- 如果包含，从 `tool_use_id` 反查上一条记录的工具名
- 需要在遍历时维护前一条记录的 `response.content`（工具调用的 id → name 映射）

**输出示例**：
```
#27 | 00:28:38 | user: [tool_result:define_module:error] → tool_use (1 tools) | 35s | 21069→2587 tok
```

### `sessions` 统计增强（P4）

在 `sessions` 命令输出中增加工具调用统计和会话持续时间。

**新增列**：
- `Tools(ok/err)` — 工具调用成功/失败数
- `Duration` — 会话持续时间（首条到末条 API 记录的时间差）

**数据提取**：遍历 API JSONL 文件，统计：
- 工具调用总数（`tool_use` 块计数）
- 工具错误数（下一条记录中 `is_error=true` 的 `tool_result` 计数）
- 首末记录时间戳差值

**性能考虑**：
- 统计需要读取完整 API JSONL 文件
- 文件通常不大（<1MB），可接受
- 如果会话很多，可考虑添加 `--stats` 选项控制是否计算统计（默认开启）

**输出示例**：
```
Session          Logs  API  Tools(ok/err)  Duration
20260221_082415  444   35   25/4           7m44s
20260221_143000  120   12   10/0           2m15s
```

**实现位置**：
- 引擎层：`src/mutagent/runtime/log_query.py` — `SessionInfo` 扩充字段，`list_sessions()` 增加统计逻辑
- CLI 层：`src/mutagent/cli/log_query.py` — sessions 输出格式调整

## 待定问题

### Q1: verbose 模式中参数截断阈值
**问题**：工具参数值超过多少字符时进行截断？
**建议**：40 字符，多行字符串直接显示行数摘要

确认

### Q2: tools 子命令的结果匹配策略
**问题**：tool_result 与 tool_use 的匹配方式——是通过 `tool_use_id` 精确匹配，还是通过位置推断？
**建议**：优先用 `tool_use_id` 匹配（JSONL 记录中 `tool_use` 块有 `id` 字段，`tool_result` 块也有 `tool_use_id` 字段）。如果增量模式下 `tool_result` 在下一条记录的 `input` 中，需要跨记录关联。

确认

### Q3: sessions 统计的性能开关
**问题**：是否需要 `--no-stats` 参数来跳过统计计算？
**建议**：暂不需要。会话 API 文件通常不大，默认计算统计。如果后续出现性能问题再添加。

确认

## 实施步骤清单

### 阶段一：数据层扩展 [✅ 已完成]
- [x] **新增 `ToolCallInfo` 数据类**
  - [x] 字段：index, api_index, tool_name, input_summary, is_error, result_summary, result_length
  - 状态：✅ 已完成
- [x] **新增 `query_tools()` 方法**
  - [x] 遍历 API JSONL，提取 tool_use 块
  - [x] 跨记录关联 tool_result（通过 tool_use_id）
  - [x] 支持 tool_name 过滤和 errors_only 过滤
  - 状态：✅ 已完成
- [x] **扩展 `SessionInfo` 数据类**
  - [x] 新增字段：tool_ok_count, tool_err_count, duration_seconds
  - 状态：✅ 已完成
- [x] **扩展 `list_sessions()` 统计逻辑**
  - [x] 遍历 API 文件计算工具统计和持续时间
  - 状态：✅ 已完成
- [x] **扩展 `_make_api_summary()`**
  - [x] 支持 tool_result 关联工具名（需维护前一条记录上下文）
  - [x] 支持 verbose 输出（返回额外的详情行）
  - 状态：✅ 已完成

### 阶段二：CLI 层适配 [✅ 已完成]
- [x] **`api` 子命令增加 `-v` 参数**
  - [x] 解析参数并传递给引擎
  - [x] 输出格式适配 verbose 行
  - 状态：✅ 已完成
- [x] **新增 `tools` 子命令**
  - [x] 参数：-s, -t, --errors, -n
  - [x] 格式化输出
  - 状态：✅ 已完成
- [x] **`sessions` 输出格式扩展**
  - [x] 显示 Tools(ok/err) 和 Duration 列
  - 状态：✅ 已完成

### 阶段三：测试 [✅ 已完成]
- [x] **`query_tools()` 单元测试**
  - [x] 正常工具调用提取
  - [x] 错误工具调用识别
  - [x] 跨记录 tool_result 关联
  - [x] 工具名过滤
  - 状态：✅ 已完成
- [x] **`sessions` 统计测试**
  - [x] 统计数值正确性
  - [x] 持续时间计算
  - 状态：✅ 已完成
- [x] **CLI 集成测试**
  - [x] verbose 输出格式验证
  - [x] tools 子命令端到端
  - 状态：✅ 已完成

## 测试验证

### 单元测试
- [x] `query_tools()` 基本提取
- [x] `query_tools()` 错误工具识别
- [x] `query_tools()` 跨记录关联
- [x] `list_sessions()` 统计计算
- [x] `_make_api_summary()` verbose 输出
- [x] `_make_api_summary()` tool_result 关联

### 集成测试
- [x] 使用真实会话 JSONL 文件测试 `tools` 子命令
- [x] 使用真实会话测试 `api -v` 输出
- [x] 使用多会话目录测试 `sessions` 统计

---

### 实施进度总结
- ✅ **阶段一：数据层扩展** - 100% 完成 (5/5任务)
- ✅ **阶段二：CLI 层适配** - 100% 完成 (3/3任务)
- ✅ **阶段三：测试** - 100% 完成 (3/3任务)

**核心功能完成度：100%** (11/11核心任务)
**测试覆盖：85个测试全部通过**（46个已有 + 39个新增）
