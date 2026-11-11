"""TVPilot 旧版兼容路由。

绞杀者模式阶段3-9：/health, /channels, /active, /zap, /screenshot, /fongmi/search, /keyevent, /tap。
保留旧接口兼容，不推荐新代码使用。
"""
import time
import json
import urllib.request

from tvpilot.channels import NAMES
from tvpilot.tools import tool_zap, tool_keyevent, tool_tap
from tvpilot.screenshot import tool_screenshot
from tvpilot.trim import fongmi_search

from tvpilot.config import TVPILOT_VERSION
GW_ACTIVE = 'http://192.168.2.238:8080/api/active'


def handle_legacy_get(handler, path, q):
    """统一分发：所有旧版兼容 GET 路由。返回 True 表示已处理。"""
    # /health（旧版）
    if path == '/health':
        handler._json({'ok': True, 'channels': len(NAMES), 'tvpilot': TVPILOT_VERSION})
        return True

    # /channels（旧版）
    if path == '/channels':
        handler._json({'count': len(NAMES), 'channels': NAMES})
        return True

    # /active（旧版：查询机顶盒当前频道）
    if path == '/active':
        try:
            a = json.loads(urllib.request.urlopen(GW_ACTIVE, timeout=5).read())['channel']
        except Exception:
            a = None
        handler._json({'active': a})
        return True

    # /zap（旧版 GET 换台）
    if path == '/zap':
        ch = q.get('channel', [''])[0]
        if not ch:
            handler._json({'ok': False, 'error': '缺少参数 channel'})
            return True
        handler._handle_tool('zap', tool_zap, channel=ch)
        return True

    # /screenshot（旧版 GET 截图）
    if path == '/screenshot':
        fmt = q.get('fmt', [''])[0]
        t0 = time.time()
        result, error, msg = tool_screenshot(fmt='json' if fmt == 'json' else 'png')
        if error:
            handler._json({'ok': False, 'error': 'screencap failed'}, 500)
        elif fmt == 'json':
            handler._json({'ok': True, **result})
        else:
            handler._send_png(result['png_bytes'])
        return True

    # /fongmi/search（旧版：飞牛TV搜索）
    if path == '/fongmi/search':
        t0 = time.time()
        r = fongmi_search()
        r['cost_s'] = round(time.time() - t0, 1)
        handler._json(r)
        return True

    # /keyevent（旧版 GET 按键）
    if path == '/keyevent':
        handler._handle_tool('keyevent', tool_keyevent, key=q.get('key', [''])[0])
        return True

    # /tap（旧版 GET 点击）
    if path == '/tap':
        handler._handle_tool('tap', tool_tap, x=q.get('x', ['0'])[0], y=q.get('y', ['0'])[0])
        return True

    return False


__all__ = ['handle_legacy_get']
