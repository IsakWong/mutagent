"""测试多 MCPView 场景下 tool 隔离问题。

问题描述：当定义多个 MCPView 时，MCPToolSet 通过 path 或 view 属性指定归属，
但 MCPToolProvider.refresh() 没有按 view/path 过滤，导致所有 tool 都注册到每个 view。
"""

import pytest
import mutobj
from mutagent.net.mcp import MCPView, MCPToolSet
from mutagent.net._mcp_impl import MCPToolProvider


# ---------------------------------------------------------------------------
# 定义两个独立的 MCPView
# ---------------------------------------------------------------------------

class ViewA(MCPView):
    path = "/mcp-a"
    name = "server-a"
    version = "1.0"


class ViewB(MCPView):
    path = "/mcp-b"
    name = "server-b"
    version = "1.0"


# ---------------------------------------------------------------------------
# 定义归属不同 view 的 tool 集合
# ---------------------------------------------------------------------------

class ToolsForA(MCPToolSet):
    """只应该注册到 ViewA"""
    path = "/mcp-a"

    async def tool_a1(self) -> str:
        """Tool A1"""
        return "a1"

    async def tool_a2(self, name: str) -> str:
        """Tool A2"""
        return f"a2: {name}"


class ToolsForB(MCPToolSet):
    """只应该注册到 ViewB"""
    path = "/mcp-b"

    async def tool_b1(self) -> str:
        """Tool B1"""
        return "b1"

    async def tool_b2(self, count: int) -> str:
        """Tool B2"""
        return f"b2: {count}"


class ToolsForBoth(MCPToolSet):
    """应该注册到两个 view（通过 path 元组）"""
    path = ("/mcp-a", "/mcp-b")

    async def shared_tool(self) -> str:
        """Shared tool"""
        return "shared"


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

class TestMCPToolIsolation:
    """测试多 MCPView 场景下的 tool 隔离。"""

    def test_current_behavior_tools_isolated(self):
        """验证修复后的行为：tool 按 path 正确隔离。

        MCPToolProvider 接收 target_view 参数后，
        refresh() 时会根据 MCPToolSet 的 path/view 属性过滤，
        只注册匹配的 tool。
        """
        # 创建绑定到不同 view 的 provider
        provider_a = MCPToolProvider(target_view=ViewA)
        provider_b = MCPToolProvider(target_view=ViewB)

        tools_a = provider_a.list_tools()
        tools_b = provider_b.list_tools()

        tool_names_a = {t["name"] for t in tools_a}
        tool_names_b = {t["name"] for t in tools_b}

        print(f"\nProvider A tools: {sorted(tool_names_a)}")
        print(f"Provider B tools: {sorted(tool_names_b)}")

        # 期望行为：
        # - ViewA 应该只有 tool_a1, tool_a2, shared_tool
        # - ViewB 应该只有 tool_b1, tool_b2, shared_tool
        expected_a = {"tool_a1", "tool_a2", "shared_tool"}
        expected_b = {"tool_b1", "tool_b2", "shared_tool"}

        assert tool_names_a == expected_a, f"ViewA tools 不正确: {tool_names_a}"
        assert tool_names_b == expected_b, f"ViewB tools 不正确: {tool_names_b}"

    def test_expected_behavior_no_target_gets_all(self):
        """不指定 target_view 时，应该获取所有 tool（向后兼容）。"""
        provider = MCPToolProvider()
        tools = provider.list_tools()
        tool_names = {t["name"] for t in tools}

        expected = {"tool_a1", "tool_a2", "tool_b1", "tool_b2", "shared_tool"}
        assert tool_names == expected, f"All tools: {tool_names}"


class TestMCPToolProviderWithTargetView:
    """测试 MCPToolProvider 的 target_view 参数。"""

    def test_provider_with_target_view_a(self):
        """Provider 绑定到 ViewA 时，只应该看到 ViewA 的 tool。"""
        provider = MCPToolProvider(target_view=ViewA)
        tools = provider.list_tools()
        tool_names = {t["name"] for t in tools}

        expected = {"tool_a1", "tool_a2", "shared_tool"}
        assert tool_names == expected, f"ViewA tools: {tool_names}, expected: {expected}"

    def test_provider_with_target_view_b(self):
        """Provider 绑定到 ViewB 时，只应该看到 ViewB 的 tool。"""
        provider = MCPToolProvider(target_view=ViewB)
        tools = provider.list_tools()
        tool_names = {t["name"] for t in tools}

        expected = {"tool_b1", "tool_b2", "shared_tool"}
        assert tool_names == expected, f"ViewB tools: {tool_names}, expected: {expected}"

    def test_provider_without_target_view_gets_all(self):
        """不指定 target_view 时，应该获取所有 tool（向后兼容）。"""
        provider = MCPToolProvider()
        tools = provider.list_tools()
        tool_names = {t["name"] for t in tools}

        expected = {"tool_a1", "tool_a2", "tool_b1", "tool_b2", "shared_tool"}
        assert tool_names == expected, f"All tools: {tool_names}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
