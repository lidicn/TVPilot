"""TVPilot MCP Server（Model Context Protocol 2025-03-26 / JSON-RPC 2.0）。

零新依赖：仅标准库 + tvpilot 内部模块。双传输：
* stdio：``python -m tvpilot.mcp --stdio``，换行分隔 JSON-RPC；
* HTTP/SSE：``python -m tvpilot.mcp``，POST /mcp 走 JSON-RPC，GET /mcp 开 SSE 心跳流。

省 token 设计：
* 工具收敛为 10 个，描述均 <= 80 字符；
* tools/call 直接回传工具三元组里的 data（剥掉 ok/cost_ms 包装），
  文本 content 用紧凑 JSON（无空格、ensure_ascii=False）；
* 截图只回 URL（默认 240p / 宽 426），不回 base64。

既有文件（tools.py / state.py / screenshot.py / blueprint.py）只读复用，不修改。
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import socket
import struct
import sys
import tempfile
import threading
import time
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial

from tvpilot.tools import (
    tool_zap, tool_keyevent, tool_launch_app, tool_input_text,
    tool_tap, tool_combo_go_home,
)
from tvpilot.state import get_foreground_pkg, tool_system_state
from tvpilot.screenshot import tool_screenshot
from tvpilot.blueprint import BlueprintRunner

PROTOCOL_VERSION = '2025-03-26'
SERVER_INFO = {'name': 'tvpilot-mcp', 'version': '0.1.0'}
MAX_TOOLS = 10

_SHOT_DIR = os.environ.get('TVPILOT_MCP_SHOT_DIR',
                           os.path.join(tempfile.gettempdir(), 'tvpilot_shots'))
_SHOT_BASE = os.environ.get('TVPILOT_MCP_BASE_URL', '').rstrip('/')
_shot_server = None
_shot_lock = threading.Lock()

_runner = None  # 懒加载 BlueprintRunner，测试可注入假 runner


# --------------------------------------------------------------------------- #
# 结果归一化
# --------------------------------------------------------------------------- #

def _unwrap(raw):
    """既有契约：仅三元组按 (data, err_code, err_message) 解释，其余视为成功。"""
    if isinstance(raw, (tuple, list)) and len(raw) == 3:
        return raw[0], raw[1], raw[2]
    return raw, None, None


def _clean(data):
    """去掉 ok/cost_ms 包装字段（防御性；正常 data 本就不含）。"""
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k not in ('ok', 'cost_ms')}
    return data


def _ok_content(data, clean=True):
    if clean:
        data = _clean(data)
    text = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    return {'content': [{'type': 'text', 'text': text}]}


def _err_content(message):
    return {'content': [{'type': 'text', 'text': str(message)}], 'isError': True}


def _call(fn, *args, **kwargs):
    """调既有工具，把 (data, err, msg) 翻成 MCP result。"""
    data, err, msg = _unwrap(fn(*args, **kwargs))
    if err is not None or msg is not None:
        return _err_content(msg or err)
    return _ok_content(data)


# --------------------------------------------------------------------------- #
# 截图：base64 -> 落盘 -> URL（纯标准库 PNG 降采样到 240p）
# --------------------------------------------------------------------------- #

def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):  # 静音，避免污染 stdio
        pass


def _ensure_shot_base():
    """懒启动截图静态文件服务；已配置 TVPILOT_MCP_BASE_URL 则直接用。"""
    global _shot_server, _SHOT_BASE
    if _SHOT_BASE:
        return _SHOT_BASE
    with _shot_lock:
        if _shot_server is None:
            os.makedirs(_SHOT_DIR, exist_ok=True)
            port = int(os.environ.get('TVPILOT_MCP_SHOT_PORT', '0'))
            srv = ThreadingHTTPServer(
                ('0.0.0.0', port), partial(_Quiet, directory=_SHOT_DIR))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            _shot_server = srv
            _SHOT_BASE = 'http://%s:%d' % (_lan_ip(), srv.server_address[1])
    return _SHOT_BASE


def _png_chunks(blob):
    if not blob.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('not png')
    pos, chunks = 8, []
    while pos + 8 <= len(blob):
        ln = struct.unpack('>I', blob[pos:pos + 4])[0]
        typ = blob[pos + 4:pos + 8]
        chunks.append((typ, blob[pos + 8:pos + 8 + ln]))
        pos += 12 + ln
    return chunks


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw, h, stride, bpp):
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _ in range(h):
        f = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 255
        out += line
        prev = line
    return bytes(out)


def _png_downscale(blob, target_w):
    """纯 stdlib 最近邻降采样。返回 (bytes, width, height, note)。"""
    try:
        chunks = _png_chunks(blob)
        ihdr = next(d for t, d in chunks if t == b'IHDR')
        w, h, depth, ctype, _, _, interlace = struct.unpack('>IIBBBBB', ihdr)
        channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(ctype)
        if channels is None or depth != 8 or interlace != 0:
            return blob, w, h, 'unsupported_png_kept_original'
        if w <= target_w or target_w < 1:
            return blob, w, h, None
        idat = b''.join(d for t, d in chunks if t == b'IDAT')
        raw = zlib.decompress(idat)
        stride = w * channels
        pix = _unfilter(raw, h, stride, channels)
        nw = target_w
        nh = max(1, round(h * nw / w))
        rows = []
        for y in range(nh):
            sy = y * h // nh
            base = sy * stride
            row = bytearray()
            for x in range(nw):
                sx = x * w // nw
                row += pix[base + sx * channels: base + (sx + 1) * channels]
            rows.append(b'\x00' + bytes(row))
        def chunk(typ, data):
            c = typ + data
            return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        out = b'\x89PNG\r\n\x1a\n'
        out += chunk(b'IHDR', struct.pack('>IIBBBBB', nw, nh, 8, ctype, 0, 0, 0))
        out += chunk(b'IDAT', zlib.compress(b''.join(rows), 6))
        out += chunk(b'IEND', b'')
        return out, nw, nh, None
    except Exception:
        return blob, None, None, 'downscale_failed_kept_original'


def _t_screenshot(args):
    try:
        width = int(args.get('width', 426))
    except (TypeError, ValueError):
        return _err_content('width 必须是整数')
    if not (16 <= width <= 4096):
        return _err_content('width 超出范围 (16~4096)')
    try:
        raw = tool_screenshot(width=width)
    except TypeError:
        raw = tool_screenshot()  # 老签名无 width 参数
    data, err, msg = _unwrap(raw)
    if err is not None or msg is not None:
        return _err_content(msg or err)
    if isinstance(data, dict) and data.get('url'):
        return _ok_content({'url': data['url'], 'width': width})
    b64 = None
    if isinstance(data, dict):
        for k in ('base64', 'b64', 'image', 'data'):
            if data.get(k):
                b64 = data[k]
                break
    elif isinstance(data, str):
        b64 = data
    if not b64:
        return _err_content('截图结果里没有图像数据')
    try:
        blob = base64.b64decode(b64)
    except (binascii.Error, ValueError):
        return _err_content('截图 base64 解码失败')
    blob, w, h, note = _png_downscale(blob, width)
    os.makedirs(_SHOT_DIR, exist_ok=True)
    name = 'shot_%s.png' % uuid.uuid4().hex[:12]
    with open(os.path.join(_SHOT_DIR, name), 'wb') as f:
        f.write(blob)
    out = {'url': '%s/%s' % (_ensure_shot_base(), name), 'width': w or width}
    if h:
        out['height'] = h
    if note:
        out['note'] = note
    return _ok_content(out)


# --------------------------------------------------------------------------- #
# 蓝图
# --------------------------------------------------------------------------- #

def _get_runner():
    global _runner
    if _runner is None:
        r = BlueprintRunner()
        r.register_builtins()
        _runner = r
    return _runner


def _t_blueprint_run(args):
    name = args.get('name')
    if not name or not isinstance(name, str):
        return _err_content('name 不能为空')
    params = args.get('params') or {}
    if not isinstance(params, dict):
        return _err_content('params 必须是对象')
    res = _get_runner().run(name, params)
    d = res.to_dict()
    # 省 token：steps 只留定位字段，丢弃 output/params 回显
    return _ok_content({
        'name': d['name'], 'ok': d['ok'], 'error': d.get('error'),
        'failed_step': d.get('failed_step'), 'duration_ms': d.get('duration_ms'),
        'steps': [{'step': s['step'], 'ok': s['ok'], 'status': s['status'],
                   'error': s.get('error')} for s in d.get('steps', [])],
    }, clean=False)  # 汇总里的 ok 是业务语义，不是包装，保留


# --------------------------------------------------------------------------- #
# 工具表（10 个，描述 <= 80 字符）
# --------------------------------------------------------------------------- #

def _t_foreground(_args):
    pkg = get_foreground_pkg()
    if pkg is None:
        return _err_content('无法获取前台包名，电视可能离线')
    return _ok_content({'package': pkg})


TOOLS = [
    {'name': 'tv_zap', 'description': '换台：按频道名切台，如 CCTV1/中央一台',
     'inputSchema': {'type': 'object', 'properties': {'channel': {'type': 'string'}},
                     'required': ['channel']},
     'handler': lambda a: _call(tool_zap, a.get('channel'))},
    {'name': 'tv_keyevent', 'description': '发送按键 KEYCODE_*，ADB 优先，6095 端口降级',
     'inputSchema': {'type': 'object', 'properties': {'key': {'type': 'string'}},
                     'required': ['key']},
     'handler': lambda a: _call(tool_keyevent, a.get('key'))},
    {'name': 'tv_tap', 'description': '点击屏幕坐标 (x,y)',
     'inputSchema': {'type': 'object',
                     'properties': {'x': {'type': 'integer'}, 'y': {'type': 'integer'}},
                     'required': ['x', 'y']},
     'handler': lambda a: _call(tool_tap, a.get('x'), a.get('y'))},
    {'name': 'tv_input_text', 'description': '输入文字，支持中文（tvremoteime 广播）',
     'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}},
                     'required': ['text']},
     'handler': lambda a: _call(tool_input_text, a.get('text'))},
    {'name': 'tv_launch_app', 'description': '启动 App：按包名，可选 activity',
     'inputSchema': {'type': 'object',
                     'properties': {'package': {'type': 'string'},
                                    'activity': {'type': 'string'}},
                     'required': ['package']},
     'handler': lambda a: _call(tool_launch_app, a.get('package'), a.get('activity'))},
    {'name': 'tv_go_home', 'description': '回桌面：HOME 键 + 前台验证',
     'inputSchema': {'type': 'object', 'properties': {}},
     'handler': lambda a: _call(tool_combo_go_home)},
    {'name': 'tv_foreground', 'description': '查询当前前台应用包名',
     'inputSchema': {'type': 'object', 'properties': {}},
     'handler': _t_foreground},
    {'name': 'tv_screenshot', 'description': '截图：返回图片 URL（默认 240p），不含 base64',
     'inputSchema': {'type': 'object',
                     'properties': {'width': {'type': 'integer', 'default': 426}}},
     'handler': _t_screenshot},
    {'name': 'tv_system_state', 'description': '查询电视系统状态',
     'inputSchema': {'type': 'object', 'properties': {}},
     'handler': lambda a: _call(tool_system_state)},
    {'name': 'tv_blueprint_run', 'description': '执行预注册蓝图流程，如 go_home/zap_to',
     'inputSchema': {'type': 'object',
                     'properties': {'name': {'type': 'string'},
                                    'params': {'type': 'object'}},
                     'required': ['name']},
     'handler': _t_blueprint_run},
]
assert len(TOOLS) <= MAX_TOOLS and all(len(t['description']) <= 80 for t in TOOLS)


# --------------------------------------------------------------------------- #
# JSON-RPC 2.0 分发
# --------------------------------------------------------------------------- #

def _resp(req_id, result):
    return {'jsonrpc': '2.0', 'id': req_id, 'result': result}


def _err(req_id, code, message):
    return {'jsonrpc': '2.0', 'id': req_id,
            'error': {'code': code, 'message': message}}


def handle_message(msg):
    """处理一条 JSON-RPC 消息；通知（无 id）返回 None。"""
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or 'method' not in msg:
        return _err(msg.get('id') if isinstance(msg, dict) else None,
                    -32600, 'Invalid Request')
    req_id = msg.get('id')
    method = msg['method']
    params = msg.get('params') or {}
    if req_id is None:  # 通知：initialized 等，不回包
        return None
    if method == 'initialize':
        return _resp(req_id, {
            'protocolVersion': PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': dict(SERVER_INFO),
        })
    if method == 'ping':
        return _resp(req_id, {})
    if method == 'tools/list':
        return _resp(req_id, {'tools': [
            {'name': t['name'], 'description': t['description'],
             'inputSchema': t['inputSchema']} for t in TOOLS]})
    if method == 'tools/call':
        name = params.get('name')
        args = params.get('arguments') or {}
        for t in TOOLS:
            if t['name'] == name:
                try:
                    return _resp(req_id, t['handler'](args))
                except Exception as exc:  # 工具抛异常收敛为 isError，不炸 server
                    return _resp(req_id, _err_content('%s: %s' % (type(exc).__name__, exc)))
        return _err(req_id, -32602, '未知工具: %s' % name)
    return _err(req_id, -32601, 'Method not found: %s' % method)


def serve_stdio(inp=None, out=None):
    """stdio 传输：每行一个 JSON-RPC 消息，每行一个响应。日志只走 stderr。"""
    inp = inp if inp is not None else sys.stdin
    out = out if out is not None else sys.stdout
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = _err(None, -32700, 'Parse error')
        else:
            resp = handle_message(msg)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False, separators=(',', ':')) + '\n')
            out.flush()


# --------------------------------------------------------------------------- #
# HTTP/SSE 传输（端点 /mcp）
# --------------------------------------------------------------------------- #

class MCPHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/healthz':
            self._send_json({'ok': True})
        elif path == '/mcp':
            # SSE 心跳流：server->client 通道（MCP 通知预留）
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            try:
                self.wfile.write(b'retry: 3000\n\n')
                while True:
                    self.wfile.write(b': ping\n\n')
                    self.wfile.flush()
                    time.sleep(15)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        else:
            self._send_json({'error': 'not found'}, 404)

    def do_POST(self):
        if self.path.split('?')[0] != '/mcp':
            self._send_json({'error': 'not found'}, 404)
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
            msg = json.loads(self.rfile.read(length).decode() or '{}')
        except (ValueError, OSError):
            self._send_json(_err(None, -32700, 'Parse error'), 400)
            return
        resp = handle_message(msg)
        if resp is None:  # 通知：202 Accepted，无 body
            self.send_response(202)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        self._send_json(resp)


def serve_http(host=None, port=None):
    host = host or os.environ.get('TVPILOT_MCP_HOST', '0.0.0.0')
    port = port if port is not None else int(os.environ.get('TVPILOT_MCP_PORT', '8765'))
    srv = ThreadingHTTPServer((host, port), MCPHandler)
    sys.stderr.write('tvpilot-mcp http://%s:%d/mcp\n' % (host, port))
    srv.serve_forever()


def main(argv=None):
    ap = argparse.ArgumentParser(prog='tvpilot.mcp', description='TVPilot MCP Server')
    ap.add_argument('--stdio', action='store_true', help='stdio 传输模式')
    ap.add_argument('--host', default=None)
    ap.add_argument('--port', type=int, default=None)
    args = ap.parse_args(argv)
    if args.stdio:
        serve_stdio()
    else:
        serve_http(args.host, args.port)


if __name__ == '__main__':
    main()
