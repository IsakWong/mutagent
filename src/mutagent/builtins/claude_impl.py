"""mutagent.builtins.claude_impl -- Claude API (legacy bridge).

此模块仅保留向后兼容的 import 桥接。实际实现已迁移到：
- mutagent.builtins.anthropic_provider (AnthropicProvider)
- mutagent.builtins.client_impl (LLMClient.send_message)

保留此文件是因为 client.py 最后一行 import 了它。
"""

# 从 anthropic_provider 重新导出，保持旧有 import 路径兼容
from mutagent.builtins.anthropic_provider import (  # noqa: F401
    _messages_to_claude,
    _tools_to_claude,
    _response_from_claude,
    _response_to_dict,
)
