"""MCP (Model Context Protocol) 路由：集成到 TVPilot 主服务 :8090。

POST /mcp  — JSON-RPC 2.0 请求（initialize / tools/list / tools/call）
GET  /mcp  — SSE 心跳流（server->client 通知通道，简化实现）

省 token 设计：
- 工具收敛为 10 个，描述 <=80 字符
- tools/call 直接回 data（剥掉 ok/cost_ms 包装）
- 截图只回 URL（默认 240p），不回 base64
"""
import json
import time
import urllib.parse


def handle_mcp_get(self, path, q):
    """GET /mcp — SSE 心跳流。"""
    if path != '/mcp':
        return False
    self.send_response(200)
    self.send_header('Content-Type', 'text/event-stream')
    self.send_header('Cache-Control', 'no-cache')
    self.send_header('Connection', 'keep-alive')
    self.send_header('Access-Control-Allow-Origin', '*')
    self.end_headers()
    try:
        self.wfile.write(b'retry: 3000\n\n')
        while True:
            self.wfile.write(b': ping\n\n')
            self.wfile.flush()
            time.sleep(15)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    return True


def handle_mcp_post(self, path, body):
    """POST /mcp — JSON-RPC 2.0 请求处理。"""
    if path != '/mcp':
        return False

    # 延迟导入，避免循环依赖
    from tvpilot.mcp import handle_message

    try:
        resp = handle_message(body)
    except Exception as exc:
        resp = {
            'jsonrpc': '2.0', 'id': body.get('id') if isinstance(body, dict) else None,
            'error': {'code': -32603, 'message': 'Internal error: %s' % exc}
        }

    if resp is None:  # 通知（无 id）：202 Accepted
        self.send_response(202)
        self.send_header('Content-Length', '0')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        return True

    body_bytes = json.dumps(resp, ensure_ascii=False, separators=(',', ':')).encode()
    self.send_response(200)
    self.send_header('Content-Type', 'application/json')
    self.send_header('Content-Length', str(len(body_bytes)))
    self.send_header('Access-Control-Allow-Origin', '*')
    self.end_headers()
    self.wfile.write(body_bytes)
    return True
