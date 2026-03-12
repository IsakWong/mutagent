"""mutagent.net — 网络层（ASGI Server / HTTP Client / MCP / View 路由）。"""

# 加载 impl 模块（注册 @impl 实现）
import mutagent.net._server_impl as _server_impl  # noqa: F401
import mutagent.net._client_impl as _client_impl  # noqa: F401
import mutagent.net._mcp_impl as _mcp_impl  # noqa: F401
