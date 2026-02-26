"""Tests for WebToolkit declaration, schema, and Jina implementation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import mutagent
from mutagent.config import Config
from mutagent.messages import ToolCall, ToolSchema
from mutagent.toolkits.web_toolkit import WebToolkit
from mutagent.tools import ToolSet
from mutagent.builtins.schema import get_declaration_method, make_schema
from mutobj.core import DeclarationMeta, _DECLARED_METHODS

import mutagent.builtins  # noqa: F401  -- register all @impl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    """无 API key 的空配置。"""
    return Config(_layers=[])


@pytest.fixture
def config_with_key():
    """包含 Jina API key 的配置。"""
    return Config(
        _layers=[(Path(), {"WebToolkit": {"jina_api_key": "test-key-123"}})]
    )


@pytest.fixture
def toolkit(config):
    return WebToolkit(config=config)


@pytest.fixture
def toolkit_with_key(config_with_key):
    return WebToolkit(config=config_with_key)


@pytest.fixture
def tool_set(toolkit):
    ts = ToolSet()
    ts.add(toolkit)
    return ts


# ---------------------------------------------------------------------------
# httpx mock helpers
# ---------------------------------------------------------------------------

def _make_mock_client(response: MagicMock) -> AsyncMock:
    """创建模拟 httpx.AsyncClient 作为异步上下文管理器。"""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=response)
    return mock_client


def _make_mock_client_with_error(error: Exception) -> AsyncMock:
    """创建模拟 httpx.AsyncClient，其 get 方法抛出异常。"""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=error)
    return mock_client


# ---------------------------------------------------------------------------
# Declaration Tests
# ---------------------------------------------------------------------------

class TestWebToolkitDeclaration:

    def test_inherits_from_toolkit(self):
        assert issubclass(WebToolkit, mutagent.Toolkit)

    def test_inherits_from_declaration(self):
        assert issubclass(WebToolkit, mutagent.Declaration)

    def test_uses_declaration_meta(self):
        assert isinstance(WebToolkit, DeclarationMeta)

    def test_declared_methods(self):
        declared = getattr(WebToolkit, _DECLARED_METHODS, set())
        assert "search" in declared
        assert "fetch" in declared

    def test_has_config_attribute(self, toolkit):
        assert hasattr(toolkit, "config")


# ---------------------------------------------------------------------------
# Tool Registration Tests
# ---------------------------------------------------------------------------

class TestWebToolkitRegistration:

    def test_tool_names(self, tool_set):
        names = {s.name for s in tool_set.get_tools()}
        assert names == {"Web-search", "Web-fetch"}

    def test_tool_count(self, tool_set):
        assert len(tool_set.get_tools()) == 2

    def test_query_search(self, tool_set):
        schema = tool_set.query("Web-search")
        assert schema is not None
        assert isinstance(schema, ToolSchema)

    def test_query_fetch(self, tool_set):
        schema = tool_set.query("Web-fetch")
        assert schema is not None
        assert isinstance(schema, ToolSchema)

    def test_add_with_methods_filter(self, toolkit):
        ts = ToolSet()
        ts.add(toolkit, methods=["search"])
        names = {s.name for s in ts.get_tools()}
        assert names == {"Web-search"}


# ---------------------------------------------------------------------------
# Schema Tests
# ---------------------------------------------------------------------------

class TestWebToolkitSchema:

    def test_search_schema(self):
        decl = get_declaration_method(WebToolkit, "search")
        schema = make_schema(decl, "Web-search")
        assert schema.name == "Web-search"
        assert schema.description
        props = schema.input_schema["properties"]
        assert "query" in props
        assert "max_results" in props
        assert "query" in schema.input_schema["required"]
        assert "max_results" not in schema.input_schema.get("required", [])

    def test_fetch_schema(self):
        decl = get_declaration_method(WebToolkit, "fetch")
        schema = make_schema(decl, "Web-fetch")
        assert schema.name == "Web-fetch"
        assert schema.description
        props = schema.input_schema["properties"]
        assert "url" in props
        assert "url" in schema.input_schema["required"]

    def test_search_params_have_descriptions(self):
        decl = get_declaration_method(WebToolkit, "search")
        schema = make_schema(decl, "Web-search")
        for pname, prop in schema.input_schema["properties"].items():
            assert "description" in prop, f"Missing description for {pname}"
            assert len(prop["description"]) > 0

    def test_fetch_params_have_descriptions(self):
        decl = get_declaration_method(WebToolkit, "fetch")
        schema = make_schema(decl, "Web-fetch")
        for pname, prop in schema.input_schema["properties"].items():
            assert "description" in prop, f"Missing description for {pname}"
            assert len(prop["description"]) > 0


# ---------------------------------------------------------------------------
# Config Tests
# ---------------------------------------------------------------------------

class TestWebToolkitConfig:

    def test_no_api_key(self, toolkit):
        """没有配置 API key 时，get 返回 None。"""
        assert toolkit.config.get("WebToolkit.jina_api_key") is None

    def test_with_api_key(self, toolkit_with_key):
        """配置了 API key 时，get 能正确读取。"""
        assert toolkit_with_key.config.get("WebToolkit.jina_api_key") == "test-key-123"


# ---------------------------------------------------------------------------
# Search Implementation Tests (mocked)
# ---------------------------------------------------------------------------

def _mock_search_response(items):
    """构造 Jina Search API 的模拟 JSON 响应。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "code": 200,
        "status": 20000,
        "data": [
            {
                "title": item["title"],
                "url": item["url"],
                "description": item.get("description", ""),
                "content": item.get("content", ""),
            }
            for item in items
        ],
    }
    resp.raise_for_status = MagicMock()
    return resp


class TestSearchImpl:

    async def test_search_returns_results(self, tool_set):
        mock_resp = _mock_search_response([
            {"title": "Python", "url": "https://python.org", "description": "Official site"},
            {"title": "W3Schools", "url": "https://w3schools.com", "description": "Tutorials"},
        ])
        mock_client = _make_mock_client(mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool_set.dispatch(
                ToolCall(id="t1", name="Web-search", arguments={"query": "python"})
            )
        assert not result.is_error
        assert "Python" in result.content
        assert "https://python.org" in result.content
        assert "W3Schools" in result.content

    async def test_search_respects_max_results(self, tool_set):
        mock_resp = _mock_search_response([
            {"title": f"Result {i}", "url": f"https://example.com/{i}", "description": ""}
            for i in range(10)
        ])
        mock_client = _make_mock_client(mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool_set.dispatch(
                ToolCall(id="t1", name="Web-search", arguments={"query": "test", "max_results": 3})
            )
        assert not result.is_error
        assert "Result 0" in result.content
        assert "Result 2" in result.content
        assert "Result 3" not in result.content

    async def test_search_empty_results(self, tool_set):
        mock_resp = _mock_search_response([])
        mock_client = _make_mock_client(mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool_set.dispatch(
                ToolCall(id="t1", name="Web-search", arguments={"query": "xyzzy123"})
            )
        assert not result.is_error
        assert "没有找到" in result.content

    async def test_search_timeout(self, tool_set):
        mock_client = _make_mock_client_with_error(httpx.TimeoutException("timeout"))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool_set.dispatch(
                ToolCall(id="t1", name="Web-search", arguments={"query": "test"})
            )
        assert not result.is_error  # 错误信息作为正常文本返回
        assert "超时" in result.content

    async def test_search_request_error(self, tool_set):
        mock_client = _make_mock_client_with_error(
            httpx.ConnectError("connection refused")
        )
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool_set.dispatch(
                ToolCall(id="t1", name="Web-search", arguments={"query": "test"})
            )
        assert not result.is_error
        assert "搜索失败" in result.content

    async def test_search_sends_api_key(self, toolkit_with_key):
        mock_resp = _mock_search_response([])
        mock_client = _make_mock_client(mock_resp)
        ts = ToolSet()
        ts.add(toolkit_with_key)
        with patch("httpx.AsyncClient", return_value=mock_client):
            await ts.dispatch(ToolCall(id="t1", name="Web-search", arguments={"query": "test"}))
        call_kwargs = mock_client.get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer test-key-123"


# ---------------------------------------------------------------------------
# Fetch Implementation Tests (mocked)
# ---------------------------------------------------------------------------

def _mock_fetch_response(title, content):
    """构造 Jina Reader API 的模拟 JSON 响应。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "code": 200,
        "status": 20000,
        "data": {
            "title": title,
            "url": "https://example.com",
            "content": content,
        },
    }
    resp.raise_for_status = MagicMock()
    return resp


class TestFetchImpl:

    async def test_fetch_returns_content(self, tool_set):
        mock_resp = _mock_fetch_response("Example", "Hello, world!")
        mock_client = _make_mock_client(mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool_set.dispatch(
                ToolCall(id="t1", name="Web-fetch", arguments={"url": "https://example.com"})
            )
        assert not result.is_error
        assert "Example" in result.content
        assert "Hello, world!" in result.content

    async def test_fetch_includes_title(self, tool_set):
        mock_resp = _mock_fetch_response("My Page", "Page content here")
        mock_client = _make_mock_client(mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool_set.dispatch(
                ToolCall(id="t1", name="Web-fetch", arguments={"url": "https://example.com"})
            )
        assert "# My Page" in result.content

    async def test_fetch_truncates_long_content(self, tool_set):
        long_content = "x" * 60000
        mock_resp = _mock_fetch_response("Long", long_content)
        mock_client = _make_mock_client(mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool_set.dispatch(
                ToolCall(id="t1", name="Web-fetch", arguments={"url": "https://example.com"})
            )
        assert not result.is_error
        assert "截断" in result.content
        assert len(result.content) < 60000

    async def test_fetch_empty_content(self, tool_set):
        mock_resp = _mock_fetch_response("Empty", "")
        mock_client = _make_mock_client(mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool_set.dispatch(
                ToolCall(id="t1", name="Web-fetch", arguments={"url": "https://example.com"})
            )
        assert "无法提取" in result.content

    async def test_fetch_timeout(self, tool_set):
        mock_client = _make_mock_client_with_error(httpx.TimeoutException("timeout"))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool_set.dispatch(
                ToolCall(id="t1", name="Web-fetch", arguments={"url": "https://example.com"})
            )
        assert "超时" in result.content

    async def test_fetch_sends_api_key(self, toolkit_with_key):
        mock_resp = _mock_fetch_response("Test", "content")
        mock_client = _make_mock_client(mock_resp)
        ts = ToolSet()
        ts.add(toolkit_with_key)
        with patch("httpx.AsyncClient", return_value=mock_client):
            await ts.dispatch(ToolCall(id="t1", name="Web-fetch", arguments={"url": "https://example.com"}))
        call_kwargs = mock_client.get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer test-key-123"

    async def test_fetch_no_api_key_no_auth_header(self, tool_set):
        mock_resp = _mock_fetch_response("Test", "content")
        mock_client = _make_mock_client(mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            await tool_set.dispatch(
                ToolCall(id="t1", name="Web-fetch", arguments={"url": "https://example.com"})
            )
        call_kwargs = mock_client.get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert "Authorization" not in headers
