"""TVPilot 状态查询路由。

绞杀者模式阶段3-2：/api/health, /api/foreground, /api/current, /api/ui_state, /api/play_state, /api/system_state。
"""
import time

from tvpilot import make_response
from tvpilot.health import health_check
from tvpilot.state import (
    get_foreground_pkg, mytv_alive,
    tool_ui_state, tool_play_state, tool_system_state,
)
from tvpilot.tools import tool_foreground
from tvpilot.iptv import get_current_channel_from_logcat
from tvpilot.channels import channel_name_of_no
from tvpilot.epg import fetch_epg

MYTV_PKG = 'com.tvcam.mytv'
IPTV_PKG = 'com.android.smart.terminal.iptv'


def handle_health(handler, path, q):
    """/api/health：F19 显式 status/degraded，?strict=1 时 degraded 返 503。"""
    if path != '/api/health':
        return False
    hc = health_check()
    strict = q.get('strict', ['0'])[0] in ('1', 'true', 'yes')
    code = 503 if (strict and hc.get('status') != 'ok') else 200
    handler._json({'ok': code == 200, 'tool': 'health', 'result': hc,
                   'status': hc.get('status'), 'degraded': hc.get('degraded', False),
                   'cost_ms': 0}, code)
    return True


def handle_foreground(handler, path, q):
    """/api/foreground：查询当前前台包名。"""
    if path != '/api/foreground':
        return False
    handler._handle_tool('foreground', tool_foreground)
    return True


def handle_current(handler, path, q):
    """/api/current：F16 明确口径——台号来自电信机顶盒 App SharedPreferences，非 mytv。"""
    if path != '/api/current':
        return False
    ch_no = get_current_channel_from_logcat()
    fg_pkg = None
    mytv_up = False
    try:
        fg_pkg = get_foreground_pkg()
        mytv_up = (MYTV_PKG in (fg_pkg or '')) or mytv_alive()
    except Exception:
        pass
    base = {
        'source': 'iptv_stb_sharedprefs',
        'source_package': IPTV_PKG,
        'source_note': '台号取自电信机顶盒 App SharedPreferences 的 Service/LastchannelID，不是 mytv',
        'foreground_package': fg_pkg,
        'mytv_alive': mytv_up,
    }
    if ch_no:
        ch_name = channel_name_of_no(ch_no) or f'频道{ch_no}'
        program_name = ''
        try:
            import xml.etree.ElementTree as ET
            content, source = fetch_epg()
            if content:
                root = ET.fromstring(content)
                channel_id = None
                for ch in root.findall('channel'):
                    for dn in ch.findall('display-name'):
                        if dn.text and ch_name in dn.text:
                            channel_id = ch.get('id')
                            break
                    if channel_id:
                        break
                if channel_id:
                    now = time.strftime('%Y%m%d%H%M%S')
                    for prog in root.findall('programme'):
                        if prog.get('channel') != channel_id:
                            continue
                        start = prog.get('start', '')
                        stop = prog.get('stop', '')
                        if start <= now < stop:
                            title_el = prog.find('title')
                            program_name = title_el.text if title_el is not None else ''
                            break
        except Exception:
            pass
        handler._json({'ok': True, **base,
                       'channel': {'name': ch_name, 'no': ch_no, 'program': program_name}})
    else:
        handler._json({'ok': True, **base, 'channel': None,
                       'message': '未从电信机顶盒 App 检测到播放中的频道'})
    return True


def handle_ui_state(handler, path, q):
    """/api/ui_state：智能界面状态识别。"""
    if path != '/api/ui_state':
        return False
    t0 = time.time()
    result, error, msg = tool_ui_state()
    resp, code = make_response('ui_state', result=result, error=error, message=msg, start_time=t0)
    handler._json(resp, code)
    return True


def handle_play_state(handler, path, q):
    """/api/play_state：播放状态识别。"""
    if path != '/api/play_state':
        return False
    t0 = time.time()
    result, error, msg = tool_play_state()
    resp, code = make_response('play_state', result=result, error=error, message=msg, start_time=t0)
    handler._json(resp, code)
    return True


def handle_system_state(handler, path, q):
    """/api/system_state：系统状态聚合。"""
    if path != '/api/system_state':
        return False
    t0 = time.time()
    result, error, msg = tool_system_state()
    resp, code = make_response('system_state', result=result, error=error, message=msg, start_time=t0)
    handler._json(resp, code)
    return True


def handle_state_routes(handler, path, q):
    """统一分发：处理所有状态查询 GET 路由。返回 True 表示已处理。"""
    if handle_health(handler, path, q):
        return True
    if handle_foreground(handler, path, q):
        return True
    if handle_current(handler, path, q):
        return True
    if handle_ui_state(handler, path, q):
        return True
    if handle_play_state(handler, path, q):
        return True
    if handle_system_state(handler, path, q):
        return True
    return False


__all__ = [
    'handle_health', 'handle_foreground', 'handle_current',
    'handle_ui_state', 'handle_play_state', 'handle_system_state',
    'handle_state_routes',
]
