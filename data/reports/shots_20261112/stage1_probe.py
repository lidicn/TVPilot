# -*- coding: utf-8 -*-
"""阶段 1 只读定级探针（零注入、零前台变更、不改产品代码）。

两块读数：
  A. 29 个 tv_* 工具的静态可达性 + fail-closed 分支计数（纯源码，不 import 设备）
  B. HTTP 路由按组只读 GET 拨测：本机 127.0.0.1 临时 server，所有真机/外部
     依赖指向不可达回环地址，确保绝不连接真机；⛔ 不跑任何 POST / 注入。

用法：python data/reports/shots_20261112/stage1_probe.py
输出：STAGE1_TOOL_PROBE.json / STAGE1_ROUTE_PROBE.json（同目录）
"""
import importlib
import json
import os
import re
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
OUT = ROOT / 'data' / 'reports' / 'shots_20261112'
sys.path.insert(0, str(ROOT))

# ---------- 隔离：所有真机 / 外部依赖指向不可达回环 ----------
_m3u = tempfile.NamedTemporaryFile('w', suffix='.m3u', delete=False, encoding='utf-8')
_m3u.write('#EXTM3U\n'
           '#EXTINF:-1 tvg-name="中央一台" group-title="央视",中央一台\n'
           'http://example.invalid/1\n')
_m3u.close()
os.environ['TVPILOT_IPTV_SOURCE'] = _m3u.name
os.environ['TVPILOT_TV_ADB'] = '127.0.0.1:1'          # ADB 永不连真机
os.environ['TVPILOT_A11Y_HTTP'] = 'http://127.0.0.1:1'  # Arcface 侧不连
os.environ['DOUBAO_API_URL'] = 'http://127.0.0.1:1/v1/chat/completions'
os.environ['DOUBAO_API_KEY'] = ''
os.environ['TVPILOT_DATA_DIR'] = str(ROOT / 'data')

# ================= A. 29 个工具静态定级 =================
mcp_src = (ROOT / 'tvpilot/mcp.py').read_text(encoding='utf-8').splitlines()
tools_src = (ROOT / 'tvpilot/tools.py').read_text(encoding='utf-8').splitlines()

# 每个 tool_* 函数体范围 -> fail-closed 分支（return None, ERR_*）计数
def _func_bounds(src):
    starts = [(i + 1, m.group(1)) for i, l in enumerate(src)
              for m in [re.match(r'def (tool_[a-z_]+)\(', l)] if m]
    out = {}
    for k, (ln, name) in enumerate(starts):
        end = starts[k + 1][0] - 1 if k + 1 < len(starts) else len(src)
        body = '\n'.join(src[ln - 1:end])
        out[name] = {
            'line': ln,
            'failclosed_returns': len(re.findall(r'return\s+None\s*,\s*ERR_', body)),
            'input_validation': bool(re.search(r'if not |非法|不能为空|invalid', body)),
        }
    return out

bounds = _func_bounds(tools_src)
m = importlib.import_module('tvpilot.mcp')
TOOLS = m.TOOLS

tool_rows = []
for t in TOOLS:
    name = t['name']
    ln = next((i + 1 for i, l in enumerate(mcp_src)
               if "'name': '%s'" % name in l), None)
    handler = t['handler']
    hname = getattr(handler, '__name__', '<lambda>')
    # 从 handler 源码文本里抽底层函数（lambda 场景）
    htxt = ''
    try:
        import inspect
        htxt = inspect.getsource(handler).strip()
    except Exception:
        htxt = ''
    impl = re.findall(r'(?:_tools\.|_state\.|_blueprint\.|_theme\.)([a-z_]+)', htxt)
    impl_names = sorted(set(impl)) or ([hname] if hname.startswith('_t_') else [])
    fc = [bounds[n]['failclosed_returns'] for n in impl_names if n in bounds]
    tool_rows.append({
        'tool': name,
        'mcp_line': ln,
        'handler': hname,
        'impl': impl_names,
        'impl_lines': {n: bounds[n]['line'] for n in impl_names if n in bounds},
        'failclosed_returns': sum(fc),
        'required': t.get('inputSchema', {}).get('required', []),
        'schema_properties': list(t.get('inputSchema', {}).get('properties', {}).keys()),
    })

(OUT / 'STAGE1_TOOL_PROBE.json').write_text(
    json.dumps(tool_rows, ensure_ascii=False, indent=1), encoding='utf-8')
print('=== A. TOOL STATIC PROBE ===')
print('TOOL_COUNT =', len(tool_rows))
for r in tool_rows:
    print('%-24s mcp.py:%-4s handler=%-16s impl=%-38s fc=%s required=%s' % (
        r['tool'], r['mcp_line'], r['handler'], ','.join(r['impl']) or '-',
        r['failclosed_returns'], ','.join(r['required']) or '-'))
print('WHITELIST tool_keyevent._VALID_KEYCODES lines = tools.py:114-131')
print('KEYCODE whitelist size =', len(re.findall(r"'KEYCODE_[A-Z0-9_]+'", '\n'.join(tools_src[113:131]))))
print('tool_zap guards = tools.py:623-638 (fg + playback, ERR_INVALID_STATE / fail-closed)')
print('MAX_TOOLS =', m.MAX_TOOLS, '| assert =', 'mcp.py:949')

# ================= B. HTTP 路由只读 GET 拨测 =================
import tvpilot_server as s  # noqa: E402

PORT = 18092
srv = ThreadingHTTPServer(('127.0.0.1', PORT), s.TVPilotHandler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()


def _summarize(body):
    """凭据零打印：只回非敏感标量字段，绝不输出 URL / RTSP / 原文。"""
    try:
        d = json.loads(body)
    except Exception:
        return 'non-json(len=%d)' % len(body)
    if not isinstance(d, dict):
        return type(d).__name__
    out = {}
    for k in ('ok', 'error', 'status', 'count', 'degraded', 'see', 'tool',
              'message', 'zappable_count', 'm3u_entry_count'):
        if k in d and isinstance(d[k], (bool, int, float, str)):
            out[k] = d[k]
    return json.dumps(out, ensure_ascii=False, separators=(',', ':'))


def hit(path, timeout=6):
    url = 'http://127.0.0.1:%d%s' % (PORT, path)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.code, _summarize(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        return e.code, _summarize(e.read().decode('utf-8', 'replace'))
    except Exception as e:
        return None, type(e).__name__


# (分组, 路径) —— 全部 GET；不含任何 POST / 注入 / 改状态路由
GROUPS = [
    ('deprecated_get(8条旧版兼容)', [
        '/health', '/channels', '/active', '/zap', '/screenshot',
        '/fongmi/search', '/keyevent', '/tap']),
    ('remote(PWA)', ['/remote']),
    ('iptv_routes', ['/api/iptv/refresh_status', '/iptv/logo?channel=%E4%B8%AD%E5%A4%AE%E4%B8%80%E5%8F%B0']),
    ('media_get', ['/api/audio']),
    ('users_get_all', ['/api/users', '/api/history?limit=1', '/api/face/restore']),
    ('a11y_get', ['/api/a11y/health']),
    ('state_routes', ['/api/health', '/api/foreground', '/api/current',
                      '/api/ui_state', '/api/play_state', '/api/system_state']),
    ('channels', ['/api/channels']),
    ('epg_api', ['/api/epg']),
    ('monitor_get', ['/api/metrics', '/api/alerts', '/api/monitor/summary']),
    ('agent_get', ['/api/blueprint/list', '/api/experience/query?task=stage1']),
]
# 故意不拨：/mcp（SSE 死循环，单独短连读状态行）、/api/users/switch（改内存态）、
# /api/tts /api/analyze /api/ocr /api/screenshot*（外部合成或设备 screencap）、
# /iptv/source.m3u（正文含 URL 行，避免打印）
SKIPPED = [
    ('mcp_get', '/mcp', 'SSE 心跳长连接，改短连只读状态行见下方'),
    ('users_get_all', '/api/users/switch', 'apply_user_profile 改内存状态，⛔ 改状态类'),
    ('media_get', '/api/screenshot, /api/screenshot_smart, /api/ocr, /api/analyze, /api/tts',
     '设备 screencap / 外部 TTS / 外部 LLM，阶段 1 不跑'),
    ('iptv_routes', '/iptv/source.m3u, /iptv/epg.xml', '正文含 M3U URL 行，凭据零打印'),
]

route_rows = []
try:
    print('=== B. ROUTE GET PROBE (127.0.0.1:%d, ADB->127.0.0.1:1) ===' % PORT)
    for grp, paths in GROUPS:
        for p in paths:
            code, body = hit(p)
            route_rows.append({'group': grp, 'path': p, 'code': code, 'summary': body})
            print('%-30s %-46s -> %s %s' % (grp, p, code, body))

    # /mcp 用短连接只读状态行，不进入 SSE 循环
    try:
        sk = socket.create_connection(('127.0.0.1', PORT), timeout=3)
        sk.settimeout(3)
        sk.sendall(b'GET /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n')
        head = sk.recv(2048).decode('utf-8', 'replace')
        sk.close()
        line = head.split('\r\n', 1)[0]
        ctype = 'text/event-stream' in head.lower()
        route_rows.append({'group': 'mcp_get', 'path': '/mcp', 'code': line,
                           'summary': 'SSE content-type=%s' % ctype})
        print('%-30s %-46s -> %s SSE=%s' % ('mcp_get', '/mcp', line, ctype))
    except Exception as e:
        route_rows.append({'group': 'mcp_get', 'path': '/mcp', 'code': None,
                           'summary': type(e).__name__})
        print('mcp_get /mcp ->', type(e).__name__)

    for grp, p, why in SKIPPED:
        route_rows.append({'group': grp, 'path': p, 'code': 'SKIPPED', 'summary': why})
        print('%-30s %-46s -> SKIPPED (%s)' % (grp, p, why))
finally:
    srv.shutdown()
    srv.server_close()
    try:
        os.unlink(_m3u.name)
    except OSError:
        pass

(OUT / 'STAGE1_ROUTE_PROBE.json').write_text(
    json.dumps(route_rows, ensure_ascii=False, indent=1), encoding='utf-8')
print('ROUTE_ROWS =', len(route_rows))
print('WROTE STAGE1_TOOL_PROBE.json / STAGE1_ROUTE_PROBE.json')
