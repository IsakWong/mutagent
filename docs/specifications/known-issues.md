# 已知问题与改进建议

持续记录 mutagent 开发和使用中发现的问题、改进方向和设计洞察。

**维护规范**：在迭代过程中如果发现新的问题、行为异常、性能瓶颈或改进方向，直接追加到相关分类下。如果现有分类不合适，新建一个分类章节。每条记录应包含：发现日期、现象描述、根因分析（如有）、建议的改进方向。较大的改进项应提取为独立的 SDD 规范文档进行设计迭代。

---

## Agent 行为问题

### Agent 通过 define_module 重写核心模块时频繁引入语法错误

**发现**：2026-02-21，会话 `20260221_082415`，API 调用 #26-#30

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

### Agent 尝试重写框架核心模块

**发现**：2026-02-21，会话 `20260221_082415`，API 调用 #28-#30

**现象**：
- Agent 前两次尝试失败后，转而尝试直接重写 `mutagent.builtins.userio_impl`
- 触发了框架保护警告（`Redefining framework module`）
- 仍然失败（语法错误）

**建议**：
- [ ] 当前 `define_module` 对框架模块只是 warning，可考虑是否需要更强的保护
- [ ] 或者在 system prompt 中明确禁止重写 `mutagent.builtins.*` 模块