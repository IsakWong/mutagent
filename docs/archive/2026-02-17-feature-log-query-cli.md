# 日志查询 CLI 工具 设计规范

**状态**：✅ 已完成
**日期**：2026-02-17
**类型**：功能设计

## 1. 背景

在完成日志系统（LogStore + ApiRecorder）后，进行了一次集成测试（session `20260217_085924`），产生了 1123 行日志和 25 条 API 记录。测试暴露了以下问题：

1. **日志文件中出现大段源码**：`define_module_impl.py` 在 DEBUG 级别记录完整源码（`Source for xxx:\n{source}`），导致日志文件中出现连续数百行的原始 Python 代码，严重影响可读性。
2. **无法从日志文件中查询历史会话**：当前 `query_logs` 工具仅查询内存中的 LogStore，无法查询磁盘上的历史日志。
3. **缺乏独立的 CLI 工具**：日志查询只能通过 AI Agent 调用工具，无法在命令行中独立使用。
4. **日志与 API 记录缺乏交叉引用**：日志中的简略信息无法关联到 API 记录中的完整参数。

### 用户需求（TODO.md）

1. 增加独立运行的 CLI 日志查询工具
2. 日志文件中仅包含单行简略内容，长文本通过关键信息交叉引用 API 记录
3. 先完成工具基础设施 + 使用说明文档（指引 AI）
4. 工具完成后，开启新迭代分析日志中的问题

## 2. 设计方案

### 2.1 整体架构

分为两层：

- **查询引擎层**（`src/mutagent/runtime/log_query.py`）：纯 Python 解析和查询逻辑，可被 CLI 和 Agent 工具共用
- **CLI 入口**（`src/mutagent/cli/`）：统一的 CLI 工具模块，基于 `argparse`

`mutagent.cli` 是一个**命名空间包**（无 `__init__.py`），每个 CLI 工具是其下的独立模块，可直接运行：

| 模块 | 说明 |
|------|------|
| `src/mutagent/cli/` | 命名空间包（无 `__init__.py`） |
| `src/mutagent/cli/log_query.py` | 日志查询工具，`python -m mutagent.cli.log_query` 运行 |

运行方式：`python -m mutagent.cli.log_query <subcommand>`。未来其他 CLI 工具以同级模块形式加入（如 `mutagent.cli.replay`），各自独立运行。

### 2.2 日志截断与交叉引用

**问题根源**：`define_module_impl.py` 中 `logger.debug("Source for %s:\n%s", module_path, source)` 将完整源码写入日志。

**解决方案**：在日志记录侧截断，同时在日志消息中嵌入交叉引用标记，指向 API 记录中的对应字段。

#### A. 日志记录侧改造

在工具实现中，将长文本替换为摘要 + 引用标记：

```python
# 改造前
logger.debug("Source for %s:\n%s", module_path, source)

# 改造后
logger.debug("Source for %s (%d lines, %d bytes)", module_path,
             source.count('\n') + 1, len(source))
```

其他产生长文本的日志点（`agent_impl.py` 中的 tool result content、`inspect_module` 输出等）也需要类似处理。

#### B. 长文本截断规则

对所有日志消息，统一应用截断策略：

- 日志中的消息字段保持单行（多行内容在 FileHandler Formatter 中处理）
- **阈值**：消息超过 200 字符时截断，附加 `...(N chars total)` 后缀
- 截断在日志记录点处理（各 `_impl.py` 中），而非 Formatter 层面

#### C. API 记录交叉引用

API 记录中已完整保存了工具参数和结果。查询工具可通过以下方式关联：

- **时间关联**：日志时间戳 ↔ API 记录的 `ts` 字段
- **工具名关联**：日志中 `Executing tool: {name}` ↔ API 记录中 `tool_use` content block
- **call 序号关联**：API JSONL 中的行号（第 N 次 API 调用）可作为引用 ID

### 2.3 日志文件格式规范

采用标准的日志格式，兼容常见日志分析工具（ELK、Grafana/Loki、`grep`/`less`/`tail -f` 等），同时支持反向解析还原为 `LogEntry`。

#### 格式定义

```
%(asctime)s %(levelname)-8s %(name)s - %(message)s
```

**示例**：
```
2026-02-17 08:59:24,301 INFO     mutagent.builtins.main_impl - Logging initialized (session=20260217_085924)
2026-02-17 08:59:30,863 DEBUG    mutagent.builtins.claude_impl - Payload size: 5002 bytes
2026-02-17 08:59:32,352 WARNING  mutagent.builtins.agent_impl - Tool call failed: timeout
```

**格式说明**：

| 字段 | 格式 | 说明 |
|------|------|------|
| timestamp | `%Y-%m-%d %H:%M:%S,%f` | Python logging 默认 asctime 格式 |
| level | `%-8s` | 左对齐 8 字符，标准级别名（DEBUG/INFO/WARNING/ERROR/CRITICAL） |
| logger_name | `%s` | 完整模块路径（如 `mutagent.builtins.agent_impl`） |
| separator | ` - ` | 标准分隔符（与 Python logging 官方文档、log4j、logback 一致） |
| message | `%s` | 日志消息 |

**与当前格式的差异**：
- 分隔符从 ` — `（em dash）改为 ` - `（ASCII hyphen），避免非 ASCII 字符导致的工具兼容性问题
- level 宽度从 5 改为 8，容纳 `WARNING`/`CRITICAL` 不截断

**反向解析正则**：
```python
_LOG_LINE_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+'  # timestamp
    r'(\w+)\s+'                                            # level
    r'(\S+)\s+-\s+'                                        # logger_name -
    r'(.*)$'                                               # message
)
```

可确定性地解析出 timestamp、level、logger_name、message 四个字段，满足反向加载到 `LogStore` 的需求。

#### 兼容性

- **grep/less/tail**：纯文本，每条日志以时间戳起始，标准 ASCII 分隔
- **ELK (Logstash)**：Grok pattern `%{TIMESTAMP_ISO8601:timestamp} %{LOGLEVEL:level}%{SPACE}%{NOTSPACE:logger} - %{GREEDYDATA:message}` 可直接解析
- **Grafana/Loki**：时间戳格式可被自动识别
- **Python logging**：格式字符串与 Python 官方文档推荐风格一致

#### 可逆加载

日志文件格式支持反向解析，还原为 `LogEntry` 对象加载回内存。这为未来的日志内存管理奠定基础：

- **近期**：`LogQueryEngine` 可将文件解析为 `LogLine`
- **未来**：支持将低优先级旧日志从内存中丢弃，需要时从文件重新加载

### 2.4 多行日志处理

对于包含换行符的日志条目（如 traceback），采用**续行前缀**方式：

- 首行为标准日志格式（以时间戳开头）
- 后续行以 `\t` (tab) 开头，表示属于上一条日志的续行
- 解析器：以时间戳正则 `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}` 判断新条目开始，非此模式的行归属上一条

**示例**：
```
2026-02-17 09:05:18,134 DEBUG    mutagent.builtins.define_module_impl - Traceback (most recent call last):
	  File "module.py", line 10, in func
	    return bad_call()
	TypeError: missing argument
```

**优势**：
- 可读性好：人眼阅读时 tab 缩进自然表示续行
- 解析简单：正则匹配行首时间戳即可分割条目，与 Logstash 的 `multiline` codec 行为一致
- 高效：逐行扫描，无需预读或回溯
- 实现位置：`SingleLineFormatter`（自定义 Formatter），将 `\n` 替换为 `\n\t`

**内存 LogStore 不受影响**：`LogStoreHandler` 使用 `%(message)s` Formatter，保持原始消息（含换行）。

### 2.5 查询引擎（LogQueryEngine）

```python
class LogQueryEngine:
    """解析和查询磁盘上的日志文件和 API 记录文件。"""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir

    def list_sessions(self) -> list[SessionInfo]:
        """列出所有会话（基于文件名时间戳去重）。"""

    def query_logs(
        self,
        session: str = "",          # 会话时间戳，空=最新
        pattern: str = "",          # 正则匹配
        level: str = "DEBUG",       # 最低级别
        limit: int = 50,            # 最大返回条数
        time_from: str = "",        # 时间范围起始 HH:MM:SS
        time_to: str = "",          # 时间范围结束
    ) -> list[LogLine]:
        """查询日志文件。逐行解析，按条件过滤。"""

    def load_to_store(
        self,
        session: str,
    ) -> LogStore:
        """将日志文件解析并加载为 LogStore 对象（用于还原到内存）。"""

    def query_api(
        self,
        session: str = "",          # 会话时间戳
        call_index: int | None = None,  # 第 N 次 API 调用
        tool_name: str = "",        # 按工具名过滤
        pattern: str = "",          # 正则搜索 response/input 内容
        limit: int = 10,
    ) -> list[ApiCall]:
        """查询 API 记录文件。"""

    def get_api_detail(
        self,
        session: str,
        call_index: int,
        field: str = "",            # 具体字段路径，如 "response.content[0].input.source"
    ) -> dict | str:
        """获取某次 API 调用的详细内容（完整 JSON 或特定字段）。"""
```

#### 数据结构

```python
@dataclass
class SessionInfo:
    timestamp: str              # "20260217_085924"
    log_file: Path | None       # log_*.log 路径
    api_file: Path | None       # api_*.jsonl 路径
    log_lines: int              # 日志行数（-1=未统计）
    api_calls: int              # API 调用次数（-1=未统计）

@dataclass
class LogLine:
    line_no: int                # 文件行号
    timestamp: str              # "2026-02-17 08:59:24,301"
    level: str                  # "INFO"
    logger_name: str            # "mutagent.builtins.agent_impl"
    message: str                # 消息内容（续行已合并，\t 前缀已移除）
    raw: str                    # 原始行文本（含续行）

@dataclass
class ApiCall:
    index: int                  # 调用序号（从 0 开始，0=session）
    type: str                   # "session" | "call"
    timestamp: str              # ISO 格式
    summary: str                # 摘要（如 "user → tool_use (3 tools)"）
    data: dict                  # 完整 JSON 数据
```

### 2.6 CLI 接口设计

入口：`python -m mutagent.cli.log_query <subcommand>`

#### 子命令

```
python -m mutagent.cli.log_query sessions                    # 列出所有会话
python -m mutagent.cli.log_query logs [options]              # 查询日志
python -m mutagent.cli.log_query api [options]               # 查询 API 记录
python -m mutagent.cli.log_query api-detail <session> <N>    # 查看第 N 次 API 调用详情
```

#### logs 子命令参数

```
--session, -s     会话时间戳（默认最新）
--pattern, -p     正则匹配消息
--level, -l       最低级别（DEBUG/INFO/WARNING/ERROR）
--limit, -n       最大返回条数（默认 50）
--from            时间范围起始（HH:MM:SS）
--to              时间范围结束（HH:MM:SS）
--dir             日志目录（默认 .mutagent/logs）
```

#### api 子命令参数

```
--session, -s     会话时间戳（默认最新）
--tool, -t        按工具名过滤
--pattern, -p     正则搜索内容
--limit, -n       最大返回条数（默认 10）
--dir             日志目录
```

#### api-detail 子命令参数

```
<session>         会话时间戳
<index>           API 调用序号
--field, -f       字段路径（如 "response.content"）
--dir             日志目录
```

#### 输出示例

**sessions**：
```
Session              Log File             API File             Logs   API Calls
20260217_085924      log_20260217_...     api_20260217_...     1123   24
```

**logs**：
```
   3 | 08:59:30 INFO     agent_impl     - User message received (9 chars)
   4 | 08:59:30 INFO     claude_impl    - Sending API request (model=ark-code-latest, messages=1)
   5 | 08:59:30 DEBUG    claude_impl    - Payload size: 5002 bytes
   6 | 08:59:32 INFO     claude_impl    - API response received (stop_reason=tool_use, duration=1483ms)
```

**api**：
```
#01 | 08:59:32 | user: "测试下你现在的功能" → tool_use (2 tools) | 1483ms | 1267→43 tokens
#02 | 08:59:34 | tool_result (2 results) → tool_use (2 tools) | 2227ms | 2004→44 tokens
```

**api-detail**：
```json
{
  "type": "call",
  "ts": "2026-02-17T00:59:32...",
  "input": {"role": "user", "content": "测试下你现在的功能"},
  "response": { ... }
}
```

### 2.7 日志格式改造

解决当前日志中长文本/多行问题的具体改造点：

| 文件 | 当前问题 | 改造方案 |
|------|---------|---------|
| `define_module_impl.py` | `logger.debug("Source for %s:\n%s")` 输出完整源码 | 改为 `logger.debug("Source for %s (%d lines, %d bytes)")` |
| `agent_impl.py` | `Tool result content:` 可能包含长文本 | 截断到 200 字符，附加 `...(N chars)` |
| `query_logs_impl.py` | 输出格式重复时间戳 | 修复：`LogStoreHandler` 使用 `%(message)s` Formatter |

### 2.8 LogStoreHandler 格式化改进

当前 `LogStoreHandler` 使用与 `FileHandler` 相同的 Formatter（含时间戳前缀）。而 `query_logs_impl.py` 在输出时又手动格式化了一次时间戳，导致双重时间戳：

```
2026-02-17 08:59:32 INFO  mutagent.builtins.agent_impl - 2026-02-17 08:59:32,352 INFO     ...
```

**修复**：`LogStoreHandler` 使用仅包含 `%(message)s` 的 Formatter，时间戳从 `LogEntry.timestamp` 获取。`FileHandler` 使用标准格式 `%(asctime)s %(levelname)-8s %(name)s - %(message)s`。

### 2.9 Agent 工具集成

**决策**：保持 `query_logs` 不变（内存查询，已有 5 个工具），CLI 工具独立运行，不注册为 Agent 工具。

### 2.10 AI 使用说明文档

在 `CLAUDE.md` 中新增日志系统说明（供 Claude Code 等外部 AI 参考），内容包括：

- 日志文件命名规范（`TIMESTAMP-log.log`、`TIMESTAMP-api.jsonl`）
- CLI 查询工具用法（`python -m mutagent.cli.log_query`）
- **重要提醒**：不要直接读取完整日志文件，使用 CLI 工具按条件查询

**注意**：此说明不放在 SYSTEM_PROMPT 中（那是给 mutagent 自身 AI 的，它通过 tool 工作），而是放在 `CLAUDE.md` 中供外部 AI 使用。

### 2.11 日志文件命名规范

采用时间戳前缀的统一命名，同一会话的文件排列在一起：

| 文件 | 命名格式 |
|------|----------|
| 日志文件 | `YYYYMMDD_HHMMSS-log.log` |
| API 录制文件 | `YYYYMMDD_HHMMSS-api.jsonl` |

**示例**：
```
20260217_085924-log.log
20260217_085924-api.jsonl
20260217_110327-log.log
20260217_110327-api.jsonl
```

**向后兼容**：查询引擎同时支持旧格式（`log_TIMESTAMP.log`、`api_TIMESTAMP.jsonl`）和新格式。

## 3. 已确认决策

- **Q1 截断阈值**：✅ 200 字符。消息超过 200 字符时截断，附加 `...(N chars total)` 后缀。
- **Q2 CLI 模块位置**：✅ `mutagent.cli` 为命名空间包（无 `__init__.py`），日志查询工具为 `mutagent.cli.log_query`，通过 `python -m mutagent.cli.log_query` 运行。查询引擎放在 `runtime/log_query.py`。
- **Q3 多行日志处理**：✅ 续行前缀方式：首行为标准格式（时间戳开头），后续行以 `\t` 开头。解析器以时间戳正则判断新条目。兼顾可读性和解析效率。
- **Q4 API 摘要格式**：✅ `#序号 | 时间 | 输入摘要 → stop_reason (工具数) | 耗时 | token用量`。

## 4. 补充需求

- **日志文件可逆加载**：（已纳入 2.3 节）日志文件格式需支持反向解析为 `LogEntry`，`LogQueryEngine.load_to_store()` 可将文件还原到内存 `LogStore`。为未来的内存日志淘汰策略做准备。
- **标准日志格式**：（已纳入 2.3 节）使用标准 Python logging 格式和 ASCII 分隔符 ` - `，兼容 ELK、Grafana/Loki 等分析工具。

## 5. 实施步骤清单

### 阶段一：日志格式修复 [✅ 已完成]

- [x] **Task 1.1**: 修复 LogStoreHandler Formatter
  - [x] `main_impl.py` 中 `LogStoreHandler` 使用 `%(message)s` Formatter（去除时间戳前缀）
  - [x] `FileHandler` 格式改为 `%(asctime)s %(levelname)-8s %(name)s - %(message)s`（标准格式）
  - [x] 验证 `query_logs` 输出不再出现双重时间戳
  - 状态：✅ 已完成

- [x] **Task 1.2**: 修复长文本日志
  - [x] `define_module_impl.py`：改为记录摘要（行数 + 字节数），不记录完整源码
  - [x] `agent_impl.py`：tool result content 截断到 200 字符（已有 `%.200s`）
  - [x] `agent_impl.py`：tool args 中的长文本截断到 200 字符
  - 状态：✅ 已完成

- [x] **Task 1.3**: 单行日志保证（FileHandler Formatter）
  - [x] 自定义 `SingleLineFormatter` 子类，将消息中的 `\n` 替换为 `\n\t`（续行前缀）
  - [x] 应用到 FileHandler（确保日志文件条目可确定性解析）
  - [x] LogStoreHandler 保持原始消息（内存查询无需单行限制）
  - 状态：✅ 已完成

### 阶段二：查询引擎 [✅ 已完成]

- [x] **Task 2.1**: 创建 `runtime/log_query.py`
  - [x] `SessionInfo` / `LogLine` / `ApiCall` 数据结构
  - [x] `LogQueryEngine.__init__(log_dir)` — 接收日志目录
  - [x] `list_sessions()` — 扫描目录，按文件名提取时间戳，去重配对
  - 状态：✅ 已完成

- [x] **Task 2.2**: 日志文件查询
  - [x] `query_logs()` — 逐行解析 log 文件，支持 pattern/level/limit/time 过滤
  - [x] 日志行解析（以时间戳正则分割条目，合并续行）
  - [x] `load_to_store()` — 将文件解析为 LogStore 对象
  - 状态：✅ 已完成

- [x] **Task 2.3**: API 记录查询
  - [x] `query_api()` — 逐行解析 JSONL，支持 tool/pattern/limit 过滤
  - [x] `get_api_detail()` — 获取单条 API 调用完整内容或特定字段
  - [x] API 调用摘要生成
  - 状态：✅ 已完成

### 阶段三：CLI 入口 [✅ 已完成]

- [x] **Task 3.1**: 创建 `mutagent.cli` 命名空间包
  - [x] `src/mutagent/cli/` 目录（无 `__init__.py`，命名空间包）
  - [x] `src/mutagent/cli/log_query.py` — 日志查询 CLI，含 argparse + `if __name__` 入口
  - 状态：✅ 已完成

- [x] **Task 3.2**: log_query 子命令实现
  - [x] `sessions` 子命令（表格输出）
  - [x] `logs` 子命令（带行号输出）
  - [x] `api` 子命令（摘要行输出）
  - [x] `api-detail` 子命令（JSON 输出，可选 `--field` 字段提取）
  - 状态：✅ 已完成

### 阶段四：文档与集成 [✅ 已完成]

- [x] **Task 4.1**: AI 使用说明
  - [x] 在 SYSTEM_PROMPT 中添加日志查询 CLI 使用指南
  - [x] 说明 query_logs（内存）与 CLI（磁盘）的区别
  - 状态：✅ 已完成

### 阶段五：测试 [✅ 已完成]

- [x] **Task 5.1**: 日志格式修复测试
  - [x] LogStoreHandler 格式不含时间戳前缀
  - [x] SingleLineFormatter 续行格式验证（3 个测试）
  - 状态：✅ 已完成

- [x] **Task 5.2**: LogQueryEngine 单元测试
  - [x] list_sessions 发现并配对文件（5 个测试）
  - [x] query_logs 解析和过滤（含续行合并）（9 个测试）
  - [x] load_to_store 还原正确性（3 个测试）
  - [x] query_api 解析和过滤（7 个测试）
  - [x] get_api_detail 字段提取（5 个测试）
  - [x] _extract_field 辅助函数（4 个测试）
  - 状态：✅ 已完成

- [x] **Task 5.3**: CLI 测试
  - [x] 各子命令基本功能（9 个测试）
  - [x] 参数组合（pattern 过滤、field 提取）
  - [x] 空目录/无文件处理
  - 状态：✅ 已完成

---

### 实施进度总结

- ✅ **阶段一：日志格式修复** — 100% 完成 (3/3 任务)
- ✅ **阶段二：查询引擎** — 100% 完成 (3/3 任务)
- ✅ **阶段三：CLI 入口** — 100% 完成 (2/2 任务)
- ✅ **阶段四：文档与集成** — 100% 完成 (1/1 任务)
- ✅ **阶段五：测试** — 100% 完成 (3/3 任务)

**核心功能完成度：100%**
**测试结果：268 passed, 2 skipped**（含 46 个新增日志查询测试 + 222 个现有测试全部通过）

## 6. 测试验证

### 单元测试
- [x] LogStoreHandler 格式修复（%(message)s only）
- [x] SingleLineFormatter 续行处理（3 个测试）
- [x] LogQueryEngine 解析和过滤（28 个测试）
- [x] API 摘要生成
- [x] load_to_store 还原
- [x] _extract_field 字段提取
- 执行结果：46 个测试全部通过

### 集成测试
- [x] CLI 各子命令端到端（9 个测试）
- [x] 使用已有日志文件（session 20260217_085924）CLI 手动验证通过
- [x] 现有测试不受影响（222 passed, 2 skipped）
- 执行结果：全部通过（268 passed, 2 skipped）
