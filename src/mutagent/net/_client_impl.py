"""client.py Declaration 实现 — @impl + Extension。"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import httpx

import mutagent
import mutobj

from mutagent.net.client import HttpClient, MCPClient, MCPError
from mutagent.net._mcp_proto import PROTOCOL_VERSION

logger = logging.getLogger("mutagent.net.client")


# ---------------------------------------------------------------------------
# HttpClient @impl
# ---------------------------------------------------------------------------


@mutobj.impl(HttpClient.create)
def _create(**kwargs: Any) -> httpx.AsyncClient:
    headers: dict[str, str] = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("user-agent", f"mutagent/{mutagent.__version__}")
    kwargs["headers"] = headers
    return httpx.AsyncClient(**kwargs)


# ---------------------------------------------------------------------------
# MCPClient Extension — 承载运行时状态
# ---------------------------------------------------------------------------


class _MCPClientExt(mutobj.Extension[MCPClient]):
    """MCPClient 的运行时私有状态。"""
    _http: httpx.AsyncClient | None = None
    _session_id: str | None = None
    _request_id: int = 0


def _ext(client: MCPClient) -> _MCPClientExt:
    return cast(_MCPClientExt, _MCPClientExt.get_or_create(client))


# ---------------------------------------------------------------------------
# MCPClient @impl
# ---------------------------------------------------------------------------


@mutobj.impl(MCPClient.connect)
async def _connect(self: MCPClient) -> None:
    ext = _ext(self)
    ext._http = httpx.AsyncClient(timeout=self.timeout)
    await _initialize(self)


@mutobj.impl(MCPClient.close)
async def _close(self: MCPClient) -> None:
    ext = _ext(self)
    if ext._http and ext._session_id:
        try:
            await ext._http.delete(
                self.url.rstrip("/"),
                headers={"Mcp-Session-Id": ext._session_id},
            )
        except Exception:
            pass
    if ext._http:
        await ext._http.aclose()
        ext._http = None


@mutobj.impl(MCPClient.list_tools)
async def _list_tools(self: MCPClient) -> list[dict[str, Any]]:
    result = await _request(self, "tools/list")
    return result.get("tools", [])


@mutobj.impl(MCPClient.call_tool)
async def _call_tool(self: MCPClient, name: str, **arguments: Any) -> dict[str, Any]:
    result = await _request(self, "tools/call", {"name": name, "arguments": arguments})
    return result


@mutobj.impl(MCPClient.list_resources)
async def _list_resources(self: MCPClient) -> list[dict[str, Any]]:
    result = await _request(self, "resources/list")
    return result.get("resources", [])


@mutobj.impl(MCPClient.read_resource)
async def _read_resource(self: MCPClient, uri: str) -> dict[str, Any]:
    result = await _request(self, "resources/read", {"uri": uri})
    return result


@mutobj.impl(MCPClient.list_prompts)
async def _list_prompts(self: MCPClient) -> list[dict[str, Any]]:
    result = await _request(self, "prompts/list")
    return result.get("prompts", [])


@mutobj.impl(MCPClient.get_prompt)
async def _get_prompt(self: MCPClient, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"name": name}
    if arguments:
        params["arguments"] = arguments
    result = await _request(self, "prompts/get", params)
    return result


@mutobj.impl(MCPClient.ping)
async def _ping(self: MCPClient) -> None:
    await _request(self, "ping")


# ---------------------------------------------------------------------------
# MCPClient 内部方法
# ---------------------------------------------------------------------------


async def _initialize(client: MCPClient) -> None:
    """MCP initialize 握手。"""
    result = await _request(client, "initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {
            "name": client.client_name,
            "version": client.client_version,
        },
    })

    client.server_info = result.get("serverInfo", {})
    client.server_capabilities = result.get("capabilities", {})
    logger.info("MCP initialized: %s v%s (protocol %s)",
                client.server_info.get("name"),
                client.server_info.get("version"),
                result.get("protocolVersion"))

    await _notify(client, "notifications/initialized")


def _next_id(client: MCPClient) -> int:
    ext = _ext(client)
    ext._request_id += 1
    return ext._request_id


async def _request(client: MCPClient, method: str, params: Any = None) -> Any:
    """发送 JSON-RPC request，返回 result。"""
    ext = _ext(client)
    assert ext._http is not None
    msg_id = _next_id(client)
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params

    url = client.url.rstrip("/")
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if ext._session_id:
        headers["Mcp-Session-Id"] = ext._session_id

    resp = await ext._http.post(url, json=payload, headers=headers)
    resp.raise_for_status()

    session_id = resp.headers.get("mcp-session-id")
    if session_id:
        ext._session_id = session_id

    content_type = resp.headers.get("content-type", "")

    if "text/event-stream" in content_type:
        return _parse_sse_response(resp.text, msg_id)
    else:
        data = resp.json()
        if "error" in data:
            raise MCPError(
                data["error"].get("code", -1),
                data["error"].get("message", "Unknown error"),
                data["error"].get("data"),
            )
        return data.get("result")


async def _notify(client: MCPClient, method: str, params: Any = None) -> None:
    """发送 JSON-RPC notification。"""
    ext = _ext(client)
    assert ext._http is not None
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params

    url = client.url.rstrip("/")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if ext._session_id:
        headers["Mcp-Session-Id"] = ext._session_id

    resp = await ext._http.post(url, json=payload, headers=headers)
    if resp.status_code not in (200, 202):
        logger.warning("Notification %s returned %d", method, resp.status_code)


def _parse_sse_response(text: str, expected_id: int) -> Any:
    """解析 SSE 响应，提取 JSON-RPC result。"""
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            data_str = line[6:]
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("id") == expected_id:
                        if "error" in item:
                            err = item["error"]
                            raise MCPError(err.get("code", -1), err.get("message", ""), err.get("data"))
                        return item.get("result")
            elif isinstance(data, dict):
                if "error" in data:
                    err = data["error"]
                    raise MCPError(err.get("code", -1), err.get("message", ""), err.get("data"))
                return data.get("result")

    raise MCPError(-1, "No response found in SSE stream")
