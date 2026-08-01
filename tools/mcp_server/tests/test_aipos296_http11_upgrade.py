"""AIPOS-296: gate/board HTTP/1.1 响应修复单测

验证：
1. LybraMcpHttpSseHandler.protocol_version = "HTTP/1.1"
2. BoardHandler.protocol_version = "HTTP/1.1"
3. 所有响应路径 Content-Length/chunked/Connection: close 正确性
4. SSE 流式响应使用 Connection: close（不定长流无 Content-Length）
"""

import http.client
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mcp_server.http_sse import HttpSseConfig, build_http_server
from web.board.app import make_handler
from http.server import ThreadingHTTPServer


def find_free_port() -> int:
    """找到可用端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


class TestAIPOS296HttpVersionUpgrade(unittest.TestCase):
    """AIPOS-296: HTTP/1.1 升级与传输正确性验证"""

    def test_gate_protocol_version_is_http11(self) -> None:
        """S1: LybraMcpHttpSseHandler.protocol_version = "HTTP/1.1" """
        from tools.mcp_server.http_sse import LybraMcpHttpSseHandler
        
        self.assertEqual(
            LybraMcpHttpSseHandler.protocol_version,
            "HTTP/1.1",
            "LybraMcpHttpSseHandler must declare protocol_version = 'HTTP/1.1'",
        )

    def test_board_protocol_version_is_http11(self) -> None:
        """S1: BoardHandler.protocol_version = "HTTP/1.1" """
        # BoardHandler 是动态创建的内部类，通过 make_handler 获取
        handler_class = make_handler(repo_root=REPO_ROOT)
        
        self.assertEqual(
            handler_class.protocol_version,
            "HTTP/1.1",
            "BoardHandler must declare protocol_version = 'HTTP/1.1'",
        )

    def test_gate_json_response_has_content_length(self) -> None:
        """S2a: gate JSON 响应有 Content-Length"""
        port = find_free_port()
        config = HttpSseConfig(host="127.0.0.1", port=port, token="test-token")
        server = build_http_server(config)
        
        def run_server():
            server.serve_forever()
        
        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            # 未授权请求，预期返回 401 JSON 错误
            conn.request("POST", "/mcp", "{}", headers={"Content-Length": "2"})
            response = conn.getresponse()
            
            # 验证 HTTP/1.1
            self.assertEqual(response.version, 11, "Response must be HTTP/1.1 (version=11)")
            
            # 验证 Content-Length 存在
            content_length = response.getheader("Content-Length")
            self.assertIsNotNone(content_length, "JSON response must have Content-Length header")
            self.assertGreater(int(content_length), 0, "Content-Length must be > 0")
            
            # 验证响应体长度匹配
            body = response.read()
            self.assertEqual(
                len(body),
                int(content_length),
                "Actual body length must match Content-Length header",
            )
            
            conn.close()
        finally:
            server.shutdown()

    def test_gate_sse_stream_has_connection_close(self) -> None:
        """S2b: gate SSE 流式响应有 Connection: close（不定长流）"""
        port = find_free_port()
        config = HttpSseConfig(
            host="127.0.0.1",
            port=port,
            token="test-token",
            keepalive_seconds=0.1,
            max_keepalive_events=2,
        )
        server = build_http_server(config)
        
        def run_server():
            server.serve_forever()
        
        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            conn.request(
                "GET",
                "/sse",
                headers={"Authorization": "Bearer test-token"},
            )
            response = conn.getresponse()
            
            # 验证 HTTP/1.1
            self.assertEqual(response.version, 11, "SSE response must be HTTP/1.1 (version=11)")
            
            # 验证 Connection: close（流式响应必须显式关闭连接）
            connection_header = response.getheader("Connection")
            self.assertEqual(
                connection_header,
                "close",
                "SSE stream must have Connection: close (undici streamable requirement)",
            )
            
            # 验证无 Content-Length（流式响应不定长）
            content_length = response.getheader("Content-Length")
            self.assertIsNone(
                content_length,
                "SSE stream must not have Content-Length (indefinite length stream)",
            )
            
            # 验证 Content-Type
            content_type = response.getheader("Content-Type")
            self.assertIn("text/event-stream", content_type)
            
            conn.close()
        finally:
            server.shutdown()

    def test_gate_delete_empty_response_has_content_length_zero(self) -> None:
        """S2c: gate DELETE 空响应有 Content-Length: 0"""
        port = find_free_port()
        config = HttpSseConfig(host="127.0.0.1", port=port, token="test-token")
        server = build_http_server(config)
        
        def run_server():
            server.serve_forever()
        
        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request(
                "DELETE",
                "/mcp",
                headers={"Authorization": "Bearer test-token"},
            )
            response = conn.getresponse()
            
            # 验证 HTTP/1.1
            self.assertEqual(response.version, 11, "DELETE response must be HTTP/1.1")
            
            # 验证 Content-Length: 0
            content_length = response.getheader("Content-Length")
            self.assertEqual(
                content_length,
                "0",
                "DELETE empty response must have Content-Length: 0",
            )
            
            # 验证响应体为空
            body = response.read()
            self.assertEqual(len(body), 0, "DELETE response body must be empty")
            
            conn.close()
        finally:
            server.shutdown()

    def test_board_json_response_has_content_length(self) -> None:
        """S2a: board JSON 响应有 Content-Length"""
        port = find_free_port()
        handler_class = make_handler(repo_root=REPO_ROOT)
        server = ThreadingHTTPServer(("127.0.0.1", port), handler_class)
        
        def run_server():
            server.serve_forever()
        
        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            # 访问需要登录的路由，预期返回 302 重定向（空响应体）
            conn.request("GET", "/api/health")
            response = conn.getresponse()
            
            # 验证 HTTP/1.1
            self.assertEqual(response.version, 11, "Board response must be HTTP/1.1")
            
            # 302 重定向应该有 Content-Length: 0
            if response.status == 302:
                content_length = response.getheader("Content-Length")
                self.assertEqual(
                    content_length,
                    "0",
                    "302 redirect must have Content-Length: 0",
                )
            
            conn.close()
        finally:
            server.shutdown()

    def test_board_static_file_has_content_length(self) -> None:
        """S2a: board 静态文件响应有 Content-Length"""
        port = find_free_port()
        handler_class = make_handler(repo_root=REPO_ROOT)
        server = ThreadingHTTPServer(("127.0.0.1", port), handler_class)
        
        def run_server():
            server.serve_forever()
        
        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        time.sleep(0.3)
        
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            # 登录页是静态文件，不需要鉴权
            conn.request("GET", "/login")
            response = conn.getresponse()
            
            # 验证 HTTP/1.1
            self.assertEqual(response.version, 11, "Static file response must be HTTP/1.1")
            
            # 验证 Content-Length 存在
            content_length = response.getheader("Content-Length")
            self.assertIsNotNone(
                content_length,
                "Static file response must have Content-Length header",
            )
            self.assertGreater(int(content_length), 0, "Content-Length must be > 0")
            
            # 验证响应体长度匹配
            body = response.read()
            self.assertEqual(
                len(body),
                int(content_length),
                "Actual body length must match Content-Length header",
            )
            
            conn.close()
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
