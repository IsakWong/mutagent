# 日志查询增强与日志隔离 设计规范

**状态**：✅ 已完成
**日期**：2026-02-26
**类型**：功能设计

## 1. 背景

两个独立的改进需求，合并为一个设计文档：

**需求 A — 日志查询工具改进**（来源：TASKS.md P3）：
- `log_query logs` 对多行日志（含 traceback）只显示首行和 `(+N lines)` 摘要，无法查看完整内容
- 调试启动错误时需要直接 grep 日志文件，查询工具形同虚设
- 缺少按 logger name 过滤、实时跟踪等调试常用功能

**需求 B — 日志与存储隔离**（来源：TODO.md）：
- mutbot 当前将日志写入 `.mutagent/logs/`，应该使用自己的日志目录
- mutbot 的 session、workspace 文件应存储在 `~/.mutbot/` 目录下
- mutbot 是 web 服务，会产生多个 session，每个 session 的日志和 API 应按 session 隔离存储

## 2. 设计方案

### 2.1 日志查询增强（CLI + 引擎）

#### 2.1.1 `--expand` / `-e` 展开多行日志

在 `logs` 子命令增加 `--expand` / `-e` 选项，展开多行日志的完整内容。

**当前行为**（`_cmd_logs`，log_query.py:138）：
```
  42 | 08:59:24 ERROR    main_impl            - ModuleNotFoundError (+12 lines)
```

**改进后行为**：

默认模式（无 `--expand`）：显示前 3 行 + 摘要
```
  42 | 08:59:24 ERROR    main_impl            - ModuleNotFoundError: No module named 'foo'
     |          Traceback (most recent call last):
     |            File "/path/to/module.py", line 10, in load
     |          (+9 lines, use -e to expand)
```

`--expand` 模式：完整展开
```
  42 | 08:59:24 ERROR    main_impl            - ModuleNotFoundError: No module named 'foo'
     |          Traceback (most recent call last):
     |            File "/path/to/module.py", line 10, in load
     |            ...（完整内容）
```

**实现**：
- CLI 增加 `--expand` / `-e` 参数（`store_true`）
- `_cmd_logs` 根据 expand 标志决定输出策略：
  - `expand=False`（默认）：显示前 3 行（`PREVIEW_LINES = 3`），剩余行数提示 `(+N lines, use -e to expand)`
  - `expand=True`：完整输出所有行，续行用 `     |          ` 前缀对齐

#### 2.1.2 `--logger` 按 logger name 过滤

在 `logs` 子命令增加 `--logger` 参数，按 logger name 前缀匹配过滤日志条目。

**用法**：
```bash
# 前缀匹配：匹配 mutbot.web.server 及其子 logger
python -m mutagent.cli.log_query logs --logger mutbot.web.server
```

**实现**：
- CLI 增加 `--logger` 参数（字符串）
- `LogQueryEngine.query_logs()` 增加 `logger_name: str = ""` 参数
- 过滤逻辑：`entry.logger_name == logger_name or entry.logger_name.startswith(logger_name + ".")`
- `LogStore.query()` 同步增加 `logger_name` 参数

#### 2.1.3 `--tail` / `-f` 实时跟踪日志

在 `logs` 子命令增加 `--tail` / `-f` 选项，实时跟踪日志文件输出。

**用法**：
```bash
# 实时跟踪最新 session 日志
python -m mutagent.cli.log_query logs -f

# 结合过滤条件
python -m mutagent.cli.log_query logs -f -l ERROR --logger mutbot.web
```

**实现**：
- CLI 增加 `--tail` / `-f` 参数（`store_true`）
- 进入 tail 模式时：
  1. 先输出最近 `--limit` 条（默认 10）匹配日志
  2. seek 到文件末尾，每 0.5 秒轮询新内容
  3. 新日志条目经过过滤后（level、pattern、logger）输出
  4. Ctrl+C 退出

#### 2.1.4 改进默认输出格式

- 单行日志保持当前紧凑格式不变
- 多行日志默认显示前 3 行，而非仅首行 + `(+N lines)`
- 常量 `PREVIEW_LINES = 3` 定义在 `cli/log_query.py` 模块级别

### 2.2 日志与存储隔离

#### 2.2.1 mutagent vs mutbot 日志架构差异

两个项目的日志模型根本不同：

**mutagent**（单 agent）：
- 一次 `python -m mutagent` 运行 = 一个 session = 一组日志文件
- 文件命名：`<session_ts>-log.log` + `<session_ts>-api.jsonl`
- session_ts = 进程启动时间戳（`YYYYMMDD_HHMMSS`）
- 日志目录：`.mutagent/logs/`（项目级）

**mutbot**（web 服务，多 session）：
- 一个服务器进程持续运行，服务多个用户 session
- 当前问题：所有 session 共享服务器启动时的 `session_ts`，API 录制混在一个 JSONL 中
- 期望：每个 session 独立一组日志文件，方便按 session 分析

#### 2.2.2 mutbot 日志目录独立

**改动**：`server.py:146` 的 `log_dir = Path(".mutagent/logs")` 改为 `~/.mutbot/logs`。

- 服务器级日志：`~/.mutbot/logs/server-<server_ts>-log.log`
  - 捕获 `mutbot.*` 和 `mutagent.*` 两个 logger 层级
  - server_ts = 服务器启动时间戳
  - 包含所有 session 的 Python 日志输出（web 请求、启动、错误等）

- 每个 session 的 API 录制独立文件：`~/.mutbot/logs/<session_id>-api.jsonl`
  - session_id = session 的 UUID hex（如 `a1b2c3d4e5f6`）
  - `SessionManager.start()` 中为每个 session 创建独立 `ApiRecorder`
  - 使用 `session_id` 而非 `session_ts` 作为文件标识，与 session 管理保持一致

**实现改动**：

1. `server.py` 中日志初始化：
   - `log_dir` 改为 `Path.home() / ".mutbot" / "logs"`
   - 服务器级日志文件名加 `server-` 前缀
   - `SessionManager` 只传 `log_dir`，不再传 `session_ts`

2. `SessionManager.start()` 中：
   - 为每个 session 创建独立的 `ApiRecorder`，使用 `session_id` 作为标识
   - 不再使用共享的 `self.session_ts`

3. `create_llm_client()` 签名调整：
   - `session_ts` 参数改为 `session_id`（或通用的 `log_prefix`）
   - `ApiRecorder` 文件命名：`<session_id>-api.jsonl`

4. `log_query` 的 session 发现逻辑需要兼容 session_id 命名：
   - 当前 `_SESSION_TS_RE` 仅匹配 `YYYYMMDD_HHMMSS` 格式
   - 新增对 `<session_id>-api.jsonl` 格式的支持
   - 可以把 session 标识从正则匹配改为通用的文件名前缀提取

#### 2.2.3 mutbot session/workspace 存储路径

**决定**：将默认存储路径从项目级 `.mutbot/` 迁移到用户级 `~/.mutbot/`。不需要处理已有数据的兼容和迁移。

**改动**：
- `storage.py:15` 的 `MUTBOT_DIR = ".mutbot"` 改为 `MUTBOT_DIR = str(Path.home() / ".mutbot")`
- 所有基于 `_mutbot_path()` 的存储操作自动指向 `~/.mutbot/`
- 项目级 `.mutbot/` 仅保留配置文件（`.mutbot/config.json`）

**影响范围**：
- `~/.mutbot/sessions/` — session 元数据 + 消息历史
- `~/.mutbot/workspaces/` — workspace 配置
- `~/.mutbot/logs/` — 日志文件（含 server log + 各 session API 录制）
- `~/.mutbot/config.json` — 用户级配置（已有，不变）

### 2.3 CLAUDE.md 日志指引更新

更新 `D:\ai\CLAUDE.md` 的"日志系统"章节，区分 mutagent 和 mutbot 的日志查询方式。

**改后内容**：

```markdown
## 日志系统

**不要直接读取完整日志文件**，使用 CLI 工具查询。

### mutagent 日志

日志目录：`.mutagent/logs/`，每次运行产生一个 session。

​```bash
python -m mutagent.cli.log_query sessions
python -m mutagent.cli.log_query logs -s <session> -p "error" -l WARNING -n 20
python -m mutagent.cli.log_query logs --logger mutagent.runtime -e
python -m mutagent.cli.log_query logs -f -l ERROR
python -m mutagent.cli.log_query api -s <session> --tool define_module
python -m mutagent.cli.log_query api-detail <session> <index>
​```

### mutbot 日志

日志目录：`~/.mutbot/logs/`，服务器级日志 + 每个 session 独立的 API 录制。

​```bash
python -m mutagent.cli.log_query --dir ~/.mutbot/logs sessions
python -m mutagent.cli.log_query --dir ~/.mutbot/logs logs -p "error" -l WARNING
python -m mutagent.cli.log_query --dir ~/.mutbot/logs logs --logger mutbot.web -e
python -m mutagent.cli.log_query --dir ~/.mutbot/logs logs -f -l ERROR
​```
```

## 3. 待定问题

（全部已确认，无待定问题）

## 4. 实施步骤清单

### 阶段一：日志查询增强 [✅ 已完成]

- [x] **Task 1.1**: 改进多行日志默认输出 + `--expand`
  - [x] 新增 `PREVIEW_LINES = 3` 常量
  - [x] CLI 增加 `--expand` / `-e` 参数
  - [x] `_cmd_logs` 实现预览/展开两种输出模式
  - [x] 续行使用 `     |          ` 前缀对齐
  - 状态：✅ 已完成

- [x] **Task 1.2**: 实现 `--logger` 过滤
  - [x] CLI 增加 `--logger` 参数
  - [x] `LogQueryEngine.query_logs()` 增加 `logger_name` 参数和前缀匹配逻辑
  - [x] `LogStore.query()` 同步增加 `logger_name` 参数
  - 状态：✅ 已完成

- [x] **Task 1.3**: 实现 `--tail` / `-f` 实时跟踪
  - [x] CLI 增加 `--tail` / `-f` 参数
  - [x] 实现文件 tail + 解析 + 过滤逻辑（0.5 秒轮询）
  - [x] 处理 Ctrl+C 中断退出
  - 状态：✅ 已完成

- [x] **Task 1.4**: 编写单元测试
  - [x] 测试多行日志预览格式（默认 3 行 + expand 完整）
  - [x] 测试 logger name 过滤（精确匹配 + 前缀匹配）
  - [x] 测试 tail 模式基本逻辑
  - 状态：✅ 已完成

### 阶段二：日志与存储隔离 [✅ 已完成]

- [x] **Task 2.1**: mutbot 存储路径迁移
  - [x] `storage.py` 的 `MUTBOT_DIR` 改为 `~/.mutbot`
  - [x] 验证 session/workspace 读写正常
  - 状态：✅ 已完成

- [x] **Task 2.2**: mutbot 日志目录独立
  - [x] `server.py` 中 `log_dir` 改为 `~/.mutbot/logs`
  - [x] 服务器级日志文件名加 `server-` 前缀
  - [x] `SessionManager` 不再共享 `session_ts`
  - 状态：✅ 已完成

- [x] **Task 2.3**: Per-session API 录制
  - [x] `SessionManager.start()` 中为每个 session 创建独立 `ApiRecorder`（使用 session_id）
  - [x] `log_query` session 发现逻辑泛化为文件名前缀提取（兼容 session_id）
  - 状态：✅ 已完成

- [x] **Task 2.4**: 编写测试
  - [x] 测试 session_id 格式文件发现
  - [x] 测试 server- 前缀文件发现
  - [x] 测试混合格式目录
  - 状态：✅ 已完成

### 阶段三：文档更新 [✅ 已完成]

- [x] **Task 3.1**: 更新 CLAUDE.md 日志指引
  - [x] 区分 mutagent 和 mutbot 日志查询方式
  - [x] 补充新增 CLI 选项用法
  - 状态：✅ 已完成

- [x] **Task 3.2**: 清理 TASKS.md 和 TODO.md
  - [x] 删除 TASKS.md 中 P3 日志查询工具改进条目
  - 状态：✅ 已完成

---

### 实施进度总结
- ✅ **阶段一：日志查询增强** — 100% (4/4 任务)
- ✅ **阶段二：日志与存储隔离** — 100% (4/4 任务)
- ✅ **阶段三：文档更新** — 100% (2/2 任务)

**单元测试：101 个测试全部通过（mutagent）+ 250 个测试全部通过（mutbot）**

## 5. 测试验证

### 单元测试（mutagent: 101 通过）
- [x] 多行日志输出格式（预览模式 vs 展开模式）
- [x] `--logger` 前缀匹配过滤
- [x] `--tail` 文件追踪逻辑
- [x] `log_query` session 发现兼容性（timestamp + session_id + server- 前缀）

### mutbot 测试（250 通过）
- [x] `MUTBOT_DIR` 路径正确性
- [x] 全部现有测试无回归
