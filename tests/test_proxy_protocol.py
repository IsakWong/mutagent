"""PROXY protocol v1 解析 — HTTPProtocol 单元测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
import pytest

from mutagent.net._protocol import HTTPProtocol


def _make_protocol() -> HTTPProtocol:
    """创建一个最小可用的 HTTPProtocol 实例。"""
    app = MagicMock()
    server_state: dict = {"connections": set()}
    proto = HTTPProtocol(app, server_state=server_state)

    # 模拟 transport
    transport = MagicMock(spec=asyncio.Transport)
    transport.get_extra_info = MagicMock(side_effect=lambda key: {
        "peername": ("127.0.0.1", 54321),
        "sockname": ("127.0.0.1", 8741),
    }.get(key))
    proto.connection_made(transport)

    return proto


class TestProxyProtocol:
    """PROXY protocol v1 解析测试。"""

    async def test_parse_tcp4(self) -> None:
        """正常 TCP4 PROXY header 应覆盖 client。"""
        proto = _make_protocol()
        assert proto.client == ("127.0.0.1", 54321)

        rest = proto._try_parse_proxy_header(
            b"PROXY TCP4 10.219.26.186 192.168.1.1 56789 8741\r\nGET / HTTP/1.1\r\n"
        )
        assert proto.client == ("10.219.26.186", 56789)
        assert rest == b"GET / HTTP/1.1\r\n"

    async def test_parse_tcp6(self) -> None:
        """TCP6 PROXY header 应正常解析。"""
        proto = _make_protocol()
        rest = proto._try_parse_proxy_header(
            b"PROXY TCP6 ::ffff:10.0.0.1 ::1 12345 8741\r\nGET / HTTP/1.1\r\n"
        )
        assert proto.client == ("::ffff:10.0.0.1", 12345)
        assert rest == b"GET / HTTP/1.1\r\n"

    async def test_no_proxy_prefix(self) -> None:
        """不以 PROXY 开头的数据应原样返回，client 不变。"""
        proto = _make_protocol()
        original_data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        rest = proto._try_parse_proxy_header(original_data)
        assert rest is original_data
        assert proto.client == ("127.0.0.1", 54321)

    async def test_incomplete_proxy_line(self) -> None:
        """不完整的 PROXY 行（无 \\r\\n）应原样返回。"""
        proto = _make_protocol()
        data = b"PROXY TCP4 10.0.0.1 192.168.1.1 1234 8741"
        rest = proto._try_parse_proxy_header(data)
        assert rest is data
        assert proto.client == ("127.0.0.1", 54321)

    async def test_malformed_proxy_line(self) -> None:
        """格式错误的 PROXY 行应忽略，不覆盖 client。"""
        proto = _make_protocol()
        rest = proto._try_parse_proxy_header(b"PROXY UNKNOWN\r\nGET / HTTP/1.1\r\n")
        assert proto.client == ("127.0.0.1", 54321)
        assert rest == b"GET / HTTP/1.1\r\n"

    async def test_only_parsed_once(self) -> None:
        """_proxy_header_parsed 标志确保只解析一次。"""
        proto = _make_protocol()
        assert proto._proxy_header_parsed is False

        proto._try_parse_proxy_header(
            b"PROXY TCP4 10.0.0.1 0.0.0.0 1111 8741\r\n"
        )
        assert proto._proxy_header_parsed is True
        assert proto.client == ("10.0.0.1", 1111)

    async def test_data_received_with_proxy(self) -> None:
        """通过 data_received 入口验证完整流程。"""
        proto = _make_protocol()
        data = (
            b"PROXY TCP4 192.168.1.100 10.0.0.1 9999 8741\r\n"
            b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        )
        proto.data_received(data)
        assert proto.client == ("192.168.1.100", 9999)
        assert proto._proxy_header_parsed is True

    async def test_data_received_without_proxy(self) -> None:
        """无 PROXY header 时 data_received 正常工作。"""
        proto = _make_protocol()
        data = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        proto.data_received(data)
        assert proto.client == ("127.0.0.1", 54321)
        assert proto._proxy_header_parsed is True

    async def test_invalid_port(self) -> None:
        """端口非数字时应回退为 0。"""
        proto = _make_protocol()
        rest = proto._try_parse_proxy_header(
            b"PROXY TCP4 10.0.0.1 0.0.0.0 abc 8741\r\n"
        )
        assert proto.client == ("10.0.0.1", 0)
        assert rest == b""

    async def test_proxy_header_alone_no_h11_eof(self) -> None:
        """PROXY header 单独到达时（rest 为空），不应触发 h11 EOF。

        h11 将 receive_data(b"") 视为连接关闭信号，
        如果 PROXY 行和 HTTP 数据分开到达，空 rest 不应喂给 h11。
        """
        proto = _make_protocol()
        # 第一次 data_received：只有 PROXY header
        proto.data_received(b"PROXY TCP4 10.0.0.1 0.0.0.0 5555 8741\r\n")
        assert proto.client == ("10.0.0.1", 5555)
        assert proto._proxy_header_parsed is True

        # 第二次 data_received：HTTP 请求正常到达，不应抛 RuntimeError
        proto.data_received(
            b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        )
