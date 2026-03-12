# MCP Resource/Prompt Declaration 模式设计

**状态**：📝 设计中
**日期**：2026-03-12
**类型**：功能设计

## 背景

`mutagent.net` 重构（refactor-net-layer）完成后，MCPServer 通过 MCPToolSet Declaration 自动发现 tool，零注册。但 MCP 协议还包含 resource 和 prompt 两种能力，目前已从 MCPServer 中移除。

### 现状

- **Tool**：MCPToolSet + MCPToolProvider，方法名 = tool name，函数签名 → input schema，完全契合 Declaration 自动发现
- **Resource**：未实现。MCP resource 以 URI 为标识（如 `file:///readme`、`config://app`），有 mimeType 属性，与"方法名即名称"的模式不匹配
- **Prompt**：未实现。MCP prompt 以 name 为标识，带 arguments，返回 messages list。看似和 tool 类似，但返回值语义不同（结构化 messages 而非自由文本）

### 设计挑战

Resource 和 prompt 与 tool 的差异：

| | Tool | Resource | Prompt |
|--|------|----------|--------|
| 标识 | name（方法名） | URI（含 scheme/path） | name（方法名） |
| 输入 | 自由参数 | URI | 自由参数 |
| 输出 | 文本/ToolResult | 内容（text/blob + mimeType） | messages list |
| 元数据 | description, inputSchema | uri, name, description, mimeType | name, description, arguments |

Resource 的 URI 标识方式不适合"方法名即名称"的路由模式，需要额外的元数据机制。

## 待定问题

### QUEST Q1: 是否有实际使用场景
**问题**：当前 mutagent/mutbot 中是否有具体的 resource 或 prompt 使用需求？
**建议**：暂无。如果没有实际消费者，可以推迟到有需求时再设计，避免过早抽象。

### QUEST Q2: Resource 的 URI 表达方式
**问题**：Declaration 子类如何表达 URI → handler 的映射？方法名不适合做 URI。
**建议**：待有具体场景后再决定。初步候选方案：
- 一个类一个 resource（类属性声明 URI）
- 方法级装饰器标注 URI
- prefix + 方法名拼接

### QUEST Q3: Prompt 是否值得做 Declaration 模式
**问题**：Prompt 看似和 tool 类似（方法名 = prompt name），但返回值是结构化 messages，是否适合同样的自动发现模式？
**建议**：待有具体场景后评估。

## 关键参考

### 源码
- `mutagent/src/mutagent/net/mcp.py` — MCPToolSet + MCPToolProvider 实现
- `mutagent/src/mutagent/net/server.py` — MCPServer（当前仅支持 tools）
- `mutagent/src/mutagent/net/_mcp_proto.py` — ResourceDef, PromptDef 类型定义（已存在但未使用）

### 相关规范
- `mutagent/docs/specifications/refactor-net-layer.md` — net 层下沉重构（已完成）
