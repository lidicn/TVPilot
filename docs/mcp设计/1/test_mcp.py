"""tvpilot/mcp.py 单元测试。

不触真机：所有既有工具（tool_zap / tool_screenshot / get_foreground_pkg 等）
通过 patch tvpilot.mcp 模块属性替换为桩；BlueprintRunner 注入假 runner。
覆盖 §8 每条验收 + 边界/异常路径。
"""
import ast
import base64
import io
import json
import struct
import sys
import unittest
import zlib

import tvpilot.mcp as mcp


# --------------------------------------------------------------------------- #
# 桩与夹具
# --------------------------------------------------------------------------- #

def _png_bytes(w, h):
    """构造一张 w*h 的 8bit RGB PNG（filter 0），不依赖任何第三方库。"""
    def chunk(typ, data):
        c = typ + data
        return (struct.pack('>I', len(data)) + c
                + struct.pack('>I', zlib.crc32(c) & 0xffffffff))
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    raw = b''.join(b'\x00' + bytes((x * 7 + y) % 256 for x in range(w * 3))
                   for y in range(h))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def _rpc(method, params=None, req_id=1):
    msg = {'jsonrpc': '2.0', 'id': req_id, 'method': method}
    if params is not None:
        msg['params'] = params
    return mcp.handle_message(msg)


def _call_tool(name, arguments=None, req_id=1):
    return _rpc('tools/call', {'name': name, 'arguments': arguments or {}}, req_id)


def _content_json(resp):
    return json.loads(resp['result']['content'][0]['text'])


class _FakeResult:
    def __init__(self, d):
        self._d = d

    def to_dict(self):
        return self._d


class _FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, name, params=None, **kw):
        self.calls.append((name, params))
        return _FakeResult({
            'name': name, 'ok': True, 'error': None, 'failed_step': None,
            'duration_ms': 12, 'params': {},
            'steps': [{'step': 'step_1', 'ok': True, 'status': 'ok',
                       'error': None, 'output': {'x': 1}, 'duration_ms': 5,
                       'skipped': False, 'skip_reason': None,
                       'error_code': None, 'delay_ms': 0, 'note': None}],
        })


class MCPTestBase(unittest.TestCase):
    def setUp(self):
        mcp._runner = _FakeRunner()
        mcp._SHOT_BASE = 'http://127.0.0.1:9'  # 测试不真启服务器
        self._orig = {n: getattr(mcp, n) for n in
                      ('tool_zap', 'tool_keyevent', 'tool_screenshot',
                       'get_foreground_pkg', 'tool_system_state')}

    def tearDown(self):
        for n, v in self._orig.items():
            setattr(mcp, n, v)
        mcp._runner = None


# --------------------------------------------------------------------------- #
# §8 验收逐条
# --------------------------------------------------------------------------- #

class TestAcceptance(MCPTestBase):
    def test_1_module_compiles_and_imports(self):
        import py_compile
        py_compile.compile(mcp.__file__, doraise=True)

    def test_2_initialize_handshake(self):
        resp = _rpc('initialize')
        r = resp['result']
        self.assertEqual(r['protocolVersion'], '2025-03-26')
        self.assertEqual(r['capabilities'], {'tools': {}})
        self.assertIn('serverInfo', r)

    def test_3_tools_list_limits(self):
        tools = _rpc('tools/list')['result']['tools']
        self.assertLessEqual(len(tools), 10)
        self.assertEqual(len(tools), 10)  # 收敛目标恰好 10 个
        for t in tools:
            self.assertLessEqual(len(t['description']), 80, t['name'])
            self.assertIn('inputSchema', t)

    def test_4_tv_foreground_shape(self):
        mcp.get_foreground_pkg = lambda: 'com.tvcam.mytv'
        resp = _call_tool('tv_foreground')
        data = _content_json(resp)
        self.assertEqual(data, {'package': 'com.tvcam.mytv'})
        self.assertNotIn('ok', data)
        self.assertNotIn('cost_ms', data)

    def test_5_tv_screenshot_url_no_base64(self):
        png = _png_bytes(852, 480)
        mcp.tool_screenshot = lambda **kw: ({'base64': base64.b64encode(png).decode()},
                                            None, None)
        resp = _call_tool('tv_screenshot')
        data = _content_json(resp)
        self.assertIn('url', data)
        self.assertTrue(data['url'].startswith('http://127.0.0.1:9/shot_'))
        self.assertEqual(data['width'], 426)   # 852 -> 240p 宽 426
        self.assertEqual(data['height'], 240)
        text = resp['result']['content'][0]['text']
        self.assertNotIn('base64', text)
        self.assertNotIn('iVBOR', text)  # PNG magic 不出现在响应里
        # 落盘文件确实是降采样后的 PNG
        import os
        path = os.path.join(mcp._SHOT_DIR, data['url'].rsplit('/', 1)[1])
        with open(path, 'rb') as f:
            blob = f.read()
        w, h = struct.unpack('>II', blob[16:24])
        self.assertEqual((w, h), (426, 240))
        os.remove(path)

    def test_6_tv_zap_delegates(self):
        seen = {}

        def fake_zap(channel):
            seen['channel'] = channel
            return {'channel': channel, 'channel_no': 1}, None, None

        mcp.tool_zap = fake_zap
        data = _content_json(_call_tool('tv_zap', {'channel': 'CCTV1'}))
        self.assertEqual(seen['channel'], 'CCTV1')
        self.assertEqual(data['channel'], 'CCTV1')

    def test_7_tv_blueprint_run_delegates(self):
        data = _content_json(_call_tool('tv_blueprint_run',
                                        {'name': 'zap_to', 'params': {'channel': 'CCTV1'}}))
        self.assertEqual(mcp._runner.calls, [('zap_to', {'channel': 'CCTV1'})])
        self.assertTrue(data['ok'])
        # steps 裁剪：保留定位字段，丢弃 output
        self.assertEqual(set(data['steps'][0]), {'step', 'ok', 'status', 'error'})

    def test_8_stdio_roundtrip(self):
        req = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'})
        out = io.StringIO()
        mcp.serve_stdio(io.StringIO(req + '\n'), out)
        resp = json.loads(out.getvalue().strip())
        self.assertEqual(resp['result']['protocolVersion'], '2025-03-26')

    def test_9_zero_new_dependencies(self):
        with open(mcp.__file__, encoding='utf-8') as f:
            tree = ast.parse(f.read())
        allowed = set(sys.stdlib_module_names) | {'tvpilot', '__future__'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split('.')[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or '').split('.')[0]]
            else:
                continue
            for n in names:
                self.assertIn(n, allowed, n)


# --------------------------------------------------------------------------- #
# 边界与异常路径
# --------------------------------------------------------------------------- #

class TestEdgeCases(MCPTestBase):
    def test_foreground_offline(self):
        mcp.get_foreground_pkg = lambda: None
        resp = _call_tool('tv_foreground')
        self.assertTrue(resp['result'].get('isError'))

    def test_tool_error_triple_becomes_iserror(self):
        mcp.tool_zap = lambda ch: (None, 'ERR_INVALID_PARAMETER', '频道表里没有 "x"')
        resp = _call_tool('tv_zap', {'channel': 'x'})
        self.assertTrue(resp['result']['isError'])
        self.assertIn('频道表', resp['result']['content'][0]['text'])

    def test_tool_exception_becomes_iserror(self):
        mcp.tool_keyevent = lambda k: (_ for _ in ()).throw(RuntimeError('adb down'))
        resp = _call_tool('tv_keyevent', {'key': 'KEYCODE_HOME'})
        self.assertTrue(resp['result']['isError'])
        self.assertIn('adb down', resp['result']['content'][0]['text'])

    def test_unknown_tool(self):
        resp = _call_tool('tv_nope')
        self.assertEqual(resp['error']['code'], -32602)

    def test_unknown_method(self):
        resp = _rpc('resources/list')
        self.assertEqual(resp['error']['code'], -32601)

    def test_notification_no_response(self):
        self.assertIsNone(mcp.handle_message(
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'}))

    def test_invalid_request(self):
        resp = mcp.handle_message({'id': 1})
        self.assertEqual(resp['error']['code'], -32600)

    def test_binary_tuple_not_misread(self):
        # 二元组 (ok, msg) 必须视为成功数据，不得误判失败（blueprint 同口径）
        mcp.tool_zap = lambda ch: (True, 'zapped')
        resp = _call_tool('tv_zap', {'channel': 'CCTV1'})
        self.assertNotIn('isError', resp['result'])
        self.assertEqual(_content_json(resp), [True, 'zapped'])

    def test_screenshot_bad_width(self):
        resp = _call_tool('tv_screenshot', {'width': 'abc'})
        self.assertTrue(resp['result']['isError'])

    def test_screenshot_no_image(self):
        mcp.tool_screenshot = lambda **kw: ({}, None, None)
        resp = _call_tool('tv_screenshot')
        self.assertTrue(resp['result']['isError'])

    def test_screenshot_legacy_signature(self):
        png = _png_bytes(100, 50)
        mcp.tool_screenshot = lambda: ({'base64': base64.b64encode(png).decode()},
                                       None, None)
        data = _content_json(_call_tool('tv_screenshot'))
        self.assertEqual(data['width'], 100)  # 原图已小于 426，不放大

    def test_blueprint_missing_name(self):
        resp = _call_tool('tv_blueprint_run', {})
        self.assertTrue(resp['result']['isError'])

    def test_ping(self):
        self.assertEqual(_rpc('ping')['result'], {})

    def test_png_downscale_untouched_when_small(self):
        png = _png_bytes(64, 64)
        blob, w, h, note = mcp._png_downscale(png, 426)
        self.assertEqual((blob, w, h), (png, 64, 64))
        self.assertIsNone(note)

    def test_png_downscale_garbage_passthrough(self):
        blob, w, h, note = mcp._png_downscale(b'not a png', 426)
        self.assertEqual(blob, b'not a png')
        self.assertEqual(note, 'downscale_failed_kept_original')


if __name__ == '__main__':
    unittest.main(verbosity=2)
