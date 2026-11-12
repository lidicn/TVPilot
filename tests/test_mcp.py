"""tvpilot/mcp.py 单元测试（v1.1 整合版）。

不触真机：通过 mock sys.modules 注入假 tvpilot 子模块，BlueprintRunner 注入假 runner。
覆盖验收 9 条 + 边界/异常路径。
"""
import base64
import io
import json
import struct
import sys
import types
import unittest
import zlib

# --------------------------------------------------------------------------- #
# 构造假 tvpilot 模块（延迟导入前注入）
# --------------------------------------------------------------------------- #

def _png_bytes(w, h):
    """构造一张 w*h 的 8bit RGB PNG（filter 0）。"""
    def chunk(typ, data):
        c = typ + data
        return (struct.pack('>I', len(data)) + c
                + struct.pack('>I', zlib.crc32(c) & 0xffffffff))
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    raw = b''.join(b'\x00' + bytes((x * 7 + y) % 256 for x in range(w * 3))
                   for y in range(h))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def _make_fake_modules():
    """构造假的 tvpilot.tools / state / screenshot / blueprint 模块。"""
    fake_tools = types.ModuleType('tvpilot.tools')

    def tool_zap(channel):
        if not channel:
            return None, 'invalid_parameter', 'channel 不能为空'
        return {'channel': channel, 'channel_no': 260}, None, None

    def tool_keyevent(key):
        return {'key': key}, None, None

    def tool_launch_app(package, activity=None):
        return {'package': package, 'activity': activity}, None, None

    def tool_input_text(text):
        return {'text': text, 'length': len(text)}, None, None

    def tool_tap(x, y):
        return {'x': x, 'y': y}, None, None

    def tool_combo_go_home():
        return {'is_home': True}, None, None

    fake_tools.tool_zap = tool_zap
    fake_tools.tool_keyevent = tool_keyevent
    fake_tools.tool_launch_app = tool_launch_app
    fake_tools.tool_input_text = tool_input_text
    fake_tools.tool_tap = tool_tap
    fake_tools.tool_combo_go_home = tool_combo_go_home

    fake_state = types.ModuleType('tvpilot.state')

    def get_foreground_pkg():
        return 'com.mitv.tvhome'

    def tool_system_state():
        return {'foreground': {'package': 'com.mitv.tvhome'}, 'playing': False}, None, None

    fake_state.get_foreground_pkg = get_foreground_pkg
    fake_state.tool_system_state = tool_system_state

    fake_screenshot = types.ModuleType('tvpilot.screenshot')

    def tool_screenshot(width=None):
        png = _png_bytes(1920, 1080)
        b64 = base64.b64encode(png).decode()
        return {'png_base64': b64, 'bytes': len(png)}, None, None

    fake_screenshot.tool_screenshot = tool_screenshot

    fake_blueprint = types.ModuleType('tvpilot.blueprint')

    class FakeBlueprintResult:
        def __init__(self):
            self._d = {
                'name': 'go_home', 'ok': True, 'error': None,
                'failed_step': None, 'duration_ms': 12,
                'steps': [{'step': 'home', 'ok': True, 'status': 'ok',
                           'error': None, 'output': {'x': 1}}],
            }

        def to_dict(self):
            return self._d

    class FakeBlueprintRunner:
        def __init__(self, tools=None, **kw):
            pass

        def register_builtins(self):
            pass

        def run(self, name, params=None):
            result = FakeBlueprintResult()
            result._d['name'] = name  # 返回传入的 name
            return result

    fake_blueprint.BlueprintRunner = FakeBlueprintRunner

    return {
        'tvpilot.tools': fake_tools,
        'tvpilot.state': fake_state,
        'tvpilot.screenshot': fake_screenshot,
        'tvpilot.blueprint': fake_blueprint,
    }


# 注入假模块后再 import mcp；用完立即还原 sys.modules 与 tvpilot 包属性。
# AF-PM 11-12：原实现无清理，后续测试模块级 `from tvpilot import tools as t`
# 会绑定到假模块（has no attribute 'zap_via_adb_digits'），导致全量 pytest 红。
_FAKE_MODULES = _make_fake_modules()
_ORIGINAL_MODULES = {name: sys.modules.get(name) for name in _FAKE_MODULES}
import tvpilot as _tvpkg  # noqa: E402
_ORIGINAL_ATTRS = {name: getattr(_tvpkg, name.rsplit('.', 1)[-1], None)
                   for name in _FAKE_MODULES}
for name, mod in _FAKE_MODULES.items():
    sys.modules[name] = mod
try:
    import tvpilot.mcp as mcp  # noqa: E402
finally:
    for name, original in _ORIGINAL_MODULES.items():
        if original is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original
    for name, original in _ORIGINAL_ATTRS.items():
        short = name.rsplit('.', 1)[-1]
        if original is None:
            if hasattr(_tvpkg, short):
                delattr(_tvpkg, short)
        else:
            setattr(_tvpkg, short, original)


# --------------------------------------------------------------------------- #
# 测试辅助
# --------------------------------------------------------------------------- #

def _rpc(method, params=None, req_id=1):
    msg = {'jsonrpc': '2.0', 'id': req_id, 'method': method}
    if params is not None:
        msg['params'] = params
    return mcp.handle_message(msg)


def _call_tool(name, arguments=None, req_id=1):
    return _rpc('tools/call', {'name': name, 'arguments': arguments or {}}, req_id)


def _content_json(resp):
    return json.loads(resp['result']['content'][0]['text'])


def _is_error(resp):
    return resp['result'].get('isError', False)


# --------------------------------------------------------------------------- #
# 测试用例
# --------------------------------------------------------------------------- #

class TestInitialize(unittest.TestCase):
    """验收1-2：initialize 握手 + capabilities.tools=true"""

    def test_initialize_returns_protocol_version(self):
        resp = _rpc('initialize')
        self.assertEqual(resp['result']['protocolVersion'], '2025-03-26')

    def test_initialize_capabilities_tools_true(self):
        resp = _rpc('initialize')
        self.assertTrue(resp['result']['capabilities']['tools'])

    def test_initialize_server_info(self):
        resp = _rpc('initialize')
        self.assertEqual(resp['result']['serverInfo']['name'], 'tvpilot-mcp')

    def test_ping(self):
        resp = _rpc('ping')
        self.assertEqual(resp['result'], {})


class TestToolsList(unittest.TestCase):
    """验收3：≤10 工具，每个 description ≤80 字符"""

    def test_tools_count_le_10(self):
        resp = _rpc('tools/list')
        tools = resp['result']['tools']
        self.assertLessEqual(len(tools), 10)

    def test_tools_description_le_80(self):
        resp = _rpc('tools/list')
        for t in resp['result']['tools']:
            self.assertLessEqual(len(t['description']), 80,
                                 f"{t['name']} description 超长: {len(t['description'])}")

    def test_tools_have_input_schema(self):
        resp = _rpc('tools/list')
        for t in resp['result']['tools']:
            self.assertIn('inputSchema', t)
            self.assertEqual(t['inputSchema']['type'], 'object')

    def test_expected_tools_present(self):
        resp = _rpc('tools/list')
        names = {t['name'] for t in resp['result']['tools']}
        expected = {'tv_zap', 'tv_keyevent', 'tv_tap', 'tv_input_text',
                    'tv_launch_app', 'tv_go_home', 'tv_foreground',
                    'tv_screenshot', 'tv_system_state', 'tv_blueprint_run'}
        self.assertEqual(names, expected)


class TestToolForeground(unittest.TestCase):
    """验收4：tv_foreground 返回 {package}，无 ok/cost_ms"""

    def test_foreground_returns_package(self):
        resp = _call_tool('tv_foreground')
        data = _content_json(resp)
        self.assertEqual(data['package'], 'com.mitv.tvhome')

    def test_foreground_no_ok_wrapper(self):
        resp = _call_tool('tv_foreground')
        data = _content_json(resp)
        self.assertNotIn('ok', data)
        self.assertNotIn('cost_ms', data)


class TestToolScreenshot(unittest.TestCase):
    """验收5：tv_screenshot 返回 {url, width}，无 base64"""

    def setUp(self):
        mcp._SHOT_BASE = 'http://127.0.0.1:9999'

    def test_screenshot_returns_url(self):
        resp = _call_tool('tv_screenshot')
        data = _content_json(resp)
        self.assertIn('url', data)
        self.assertTrue(data['url'].startswith('http://'))

    def test_screenshot_no_base64(self):
        resp = _call_tool('tv_screenshot')
        data = _content_json(resp)
        self.assertNotIn('base64', data)
        self.assertNotIn('png_base64', data)
        self.assertNotIn('image_base64', data)

    def test_screenshot_default_width_426(self):
        resp = _call_tool('tv_screenshot')
        data = _content_json(resp)
        self.assertEqual(data['width'], 426)

    def test_screenshot_downscales(self):
        resp = _call_tool('tv_screenshot', {'width': 240})
        data = _content_json(resp)
        self.assertEqual(data['width'], 240)
        self.assertIn('height', data)


class TestToolZap(unittest.TestCase):
    """验收6：tv_zap 正确调用 tool_zap"""

    def test_zap_returns_channel(self):
        resp = _call_tool('tv_zap', {'channel': '湖南卫视'})
        data = _content_json(resp)
        self.assertEqual(data['channel'], '湖南卫视')
        self.assertEqual(data['channel_no'], 260)

    def test_zap_no_ok_wrapper(self):
        resp = _call_tool('tv_zap', {'channel': 'CCTV1'})
        data = _content_json(resp)
        self.assertNotIn('ok', data)


class TestToolBlueprintRun(unittest.TestCase):
    """验收7：tv_blueprint_run 正确调用 BlueprintRunner"""

    def test_blueprint_run_returns_result(self):
        resp = _call_tool('tv_blueprint_run', {'name': 'go_home'})
        data = _content_json(resp)
        self.assertEqual(data['name'], 'go_home')
        self.assertTrue(data['ok'])  # 蓝图 ok 是业务语义，保留

    def test_blueprint_steps_stripped(self):
        """省 token：steps 只留定位字段，丢弃 output"""
        resp = _call_tool('tv_blueprint_run', {'name': 'go_home'})
        data = _content_json(resp)
        step = data['steps'][0]
        self.assertIn('step', step)
        self.assertIn('ok', step)
        self.assertNotIn('output', step)  # output 被丢弃

    def test_blueprint_run_with_params(self):
        resp = _call_tool('tv_blueprint_run', {'name': 'zap_to', 'params': {'channel': 'CCTV1'}})
        data = _content_json(resp)
        self.assertEqual(data['name'], 'zap_to')


class TestStdio(unittest.TestCase):
    """验收8：stdio 模式响应 initialize"""

    def test_stdio_initialize(self):
        inp = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"initialize"}\n')
        out = io.StringIO()
        mcp.serve_stdio(inp, out)
        lines = out.getvalue().strip().split('\n')
        self.assertEqual(len(lines), 1)
        resp = json.loads(lines[0])
        self.assertEqual(resp['result']['protocolVersion'], '2025-03-26')

    def test_stdio_notification_no_response(self):
        """通知（无 id）不回包"""
        inp = io.StringIO('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        out = io.StringIO()
        mcp.serve_stdio(inp, out)
        self.assertEqual(out.getvalue(), '')


class TestZeroDependencies(unittest.TestCase):
    """验收9：零新依赖，import 只有标准库 + tvpilot 内部"""

    def test_no_third_party_imports(self):
        import ast
        with open(mcp.__file__, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())
        stdlib_prefixes = ('tvpilot', '__future__')
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split('.')[0]
                    self.assertTrue(
                        top in sys.stdlib_module_names or top.startswith(stdlib_prefixes),
                        f"非标准库导入: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    top = node.module.split('.')[0]
                    self.assertTrue(
                        top in sys.stdlib_module_names or top.startswith(stdlib_prefixes),
                        f"非标准库导入: {node.module}"
                    )


class TestErrorHandling(unittest.TestCase):
    """边界/异常：fail-closed"""

    def test_unknown_method(self):
        resp = _rpc('unknown/method')
        self.assertIn('error', resp)
        self.assertEqual(resp['error']['code'], -32601)

    def test_unknown_tool(self):
        resp = _call_tool('tv_nonexistent')
        self.assertIn('error', resp)
        self.assertEqual(resp['error']['code'], -32602)

    def test_invalid_request(self):
        resp = mcp.handle_message({'jsonrpc': '1.0', 'method': 'x'})
        self.assertIn('error', resp)
        self.assertEqual(resp['error']['code'], -32600)

    def test_tool_exception_is_error(self):
        """工具抛异常收敛为 isError，不炸 server"""
        def boom(**kw):
            raise RuntimeError('模拟失败')
        _FAKE_MODULES['tvpilot.tools'].tool_keyevent = boom
        try:
            resp = _call_tool('tv_keyevent', {'key': 'KEYCODE_HOME'})
            self.assertTrue(_is_error(resp))
        finally:
            # 恢复
            def tool_keyevent(key):
                return {'key': key}, None, None
            _FAKE_MODULES['tvpilot.tools'].tool_keyevent = tool_keyevent

    def test_missing_required_param(self):
        resp = _call_tool('tv_zap', {})  # 缺 channel
        self.assertTrue(_is_error(resp))


class TestTokenSaving(unittest.TestCase):
    """省 token 设计验证"""

    def test_compact_json_no_spaces(self):
        resp = _call_tool('tv_foreground')
        text = resp['result']['content'][0]['text']
        self.assertNotIn(', ', text)  # 无空格
        self.assertNotIn(': ', text)

    def test_chinese_not_escaped(self):
        _FAKE_MODULES['tvpilot.tools'].tool_zap = lambda channel: ({'channel': '中央一台'}, None, None)
        resp = _call_tool('tv_zap', {'channel': '中央一台'})
        text = resp['result']['content'][0]['text']
        self.assertIn('中央一台', text)  # 中文不转义


if __name__ == '__main__':
    unittest.main(verbosity=2)
