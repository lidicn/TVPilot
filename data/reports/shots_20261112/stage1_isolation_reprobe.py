# -*- coding: utf-8 -*-
"""阶段 1 隔离重拨测（回应 AF-PM 11-12「HOME 注入 / 硬编码直连」质疑）。

比 stage1_probe.py 更严：
  1. 环境变量设全：TVPILOT_TV_ADB / TVPILOT_A11Y_HTTP / MYTV_HTTP / ARC_BASE /
     DOUBAO_API_URL / TVPILOT_MCP_BASE_URL / MQTT_HOST / GO2RTC_BASE_URL 全指向 127.0.0.1:1。
  2. 硬编码不可由 env 覆盖的模块常量，import 后**直接改成 127.0.0.1:1**并逐条列出。
  3. socket.socket.connect 打桩：任何非 loopback 出站连接记 VIOLATION 并直接拒绝。
  4. subprocess.run/Popen 打桩：记录所有 adb 命令，证明 -s 目标是 127.0.0.1:1。
  5. 不启动 mqtt_loop / heal_loop（那两个只在 tvpilot_server.__main__ 里启）。
  6. 只发只读 GET，零注入、零前台变更。

用法：python data/reports/shots_20261112/stage1_isolation_reprobe.py
"""
import json
import os
import socket
import subprocess
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

BLACKHOLE = '127.0.0.1:1'
_m3u = tempfile.NamedTemporaryFile('w', suffix='.m3u', delete=False, encoding='utf-8')
_m3u.write('#EXTM3U\n#EXTINF:-1 tvg-name="中央一台",中央一台\nhttp://example.invalid/1\n')
_m3u.close()
for k in ('TVPILOT_IPTV_SOURCE',):
    os.environ[k] = _m3u.name
for k in ('TVPILOT_TV_ADB', 'MYTV_HTTP', 'ARC_BASE', 'TVPILOT_A11Y_HTTP',
          'DOUBAO_API_URL', 'TVPILOT_MCP_BASE_URL', 'GO2RTC_BASE_URL'):
    os.environ[k] = BLACKHOLE if k == 'TVPILOT_TV_ADB' else 'http://' + BLACKHOLE
os.environ['MQTT_HOST'] = '127.0.0.1'
os.environ['DOUBAO_API_KEY'] = ''
os.environ['TVPILOT_DATA_DIR'] = str(ROOT / 'data')

# ---- 出站拦截 ----
_real_connect = socket.socket.connect
CONNECT_LOG = []
VIOLATIONS = []


def _guarded_connect(self, address):
    try:
        host, port = address[0], address[1]
    except Exception:
        host, port = str(address), 0
    CONNECT_LOG.append([str(host), int(port)])
    if str(host) not in ('127.0.0.1', '::1', 'localhost', '0.0.0.0'):
        VIOLATIONS.append([str(host), int(port)])
        raise OSError('STAGE1_ISOLATION: outbound blocked -> %s:%s' % (host, port))
    return _real_connect(self, address)


socket.socket.connect = _guarded_connect

# ---- adb 子进程记录 ----
ADB_CALLS = []
_real_run = subprocess.run
_real_popen = subprocess.Popen


def _guarded_run(args, *a, **kw):
    try:
        flat = ' '.join(str(x) for x in (args if isinstance(args, (list, tuple)) else [args]))
        if 'adb' in flat:
            ADB_CALLS.append(flat[:160])
    except Exception:
        pass
    return _real_run(args, *a, **kw)


def _guarded_popen(args, *a, **kw):
    try:
        flat = ' '.join(str(x) for x in (args if isinstance(args, (list, tuple)) else [args]))
        if 'adb' in flat:
            ADB_CALLS.append(flat[:160])
    except Exception:
        pass
    return _real_popen(args, *a, **kw)


subprocess.run = _guarded_run
subprocess.Popen = _guarded_popen

import tvpilot_server as s  # noqa: E402
import tvpilot.adb as adb_mod  # noqa: E402

# ---- 硬编码常量覆盖（env 无法覆盖的点，逐条列证） ----
HARDCODED = []
_patches = []


def _set(mod, attr, value, note):
    old = getattr(mod, attr, None)
    setattr(mod, attr, value)
    HARDCODED.append({'module': mod.__name__, 'attr': attr,
                      'old': str(old)[:60], 'new': str(value)[:60], 'note': note})


import tvpilot.routes.ask as ask_mod  # noqa: E402
_set(ask_mod, 'ARC_BASE', 'http://' + BLACKHOLE, 'routes/ask.py:8 硬编码，仅 POST /api/ask/* 用')
import tvpilot.iptv as iptv_mod  # noqa: E402
_set(iptv_mod, 'IPTV_UPSTREAM', 'http://' + BLACKHOLE + '/', 'iptv.py:15 硬编码（NAS 侧 8666）')
import tvpilot.logos as logos_mod  # noqa: E402
_set(logos_mod, 'TVPILOT_LOGO_BASE', 'http://' + BLACKHOLE + '/iptv/logo', 'logos.py:17 硬编码')
import tvpilot.mcp as mcp_mod  # noqa: E402
_set(mcp_mod, '_SHOT_BASE', 'http://' + BLACKHOLE + '/shots', 'mcp.py:74 默认值')
import tvpilot.vision as vision_mod  # noqa: E402
_set(vision_mod, 'DOUBAO_API_URL', 'http://' + BLACKHOLE + '/', 'vision.py:21 默认值')
import tvpilot.epg as epg_mod  # noqa: E402
_set(epg_mod, 'EPG_UPSTREAM', 'http://' + BLACKHOLE + '/e.xml',
     'epg.py:10 硬编码公网 51zmt EPG 上游（首轮重拨测拦截到的 2 条外连）')
epg_mod._epg_cache_content = None
epg_mod._epg_cache_time = 0
import tvpilot.health as health_mod  # noqa: E402
_set(health_mod, 'MYTV_HTTP', 'http://' + BLACKHOLE, 'health.py:15 默认值')
import tvpilot.config as config_mod  # noqa: E402
_set(config_mod, 'ARC_BASE', 'http://' + BLACKHOLE, 'config.py:35 默认值')
# tvpilot_server 是 from-import 复制值，需单独覆盖
_set(s, 'ARC_BASE', 'http://' + BLACKHOLE, 'tvpilot_server.py 模块全局 ARC_BASE')
_set(s, 'A11Y_HTTP', 'http://' + BLACKHOLE, 'tvpilot_server.py:192 模块全局 A11Y_HTTP')

# ---- 静态调用链：本拨测不触及的 HOME 注入点 ----
HOME_INJECTION_SITES = [
    'tvpilot/tools.py:64 ensure_mytv_foreground -> adb_cmd(input keyevent KEYCODE_HOME)',
    'tvpilot/tools.py:75 ensure_mytv_foreground -> adb_cmd(am start -n IPTV_ACT)',
    'tvpilot/tools.py:423 tool_combo_go_home -> adb_cmd(input keyevent KEYCODE_HOME)',
    'tvpilot/blueprint.py:653 注册 ensure_mytv_foreground 为蓝图可用工具（仅注册，非执行）',
    'tvpilot/blueprint.py:734-737 go_home 内置蓝图（KEYCODE_HOME 组合动作）',
    'tvpilot/routes/agent.py:74 内置蓝图定义含 KEYCODE_HOME 步骤',
    'tvpilot/experience.py:550-551 经验回放定义含 KEYCODE_HOME',
    'tvpilot/planner.py:125 意图映射 主页/桌面 -> KEYCODE_HOME',
    'tvpilot/tools.py:40 _ensure_mytv_foreground_inner 当前 tool_zap 已不调用（8ec3b4c fail-closed）',
    'tvpilot_server.py:241 heal_loop 每 60s -> rearm_tv(:230) 不含 HOME，且本进程未启动',
]

PORT = 18094
srv = ThreadingHTTPServer(('127.0.0.1', PORT), s.TVPilotHandler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()

PATHS = ['/api/health', '/api/foreground', '/api/current', '/api/ui_state',
         '/api/play_state', '/api/system_state', '/api/monitor/summary',
         '/api/a11y/health', '/api/audio', '/api/channels', '/api/blueprint/list',
         '/api/epg', '/iptv/logo?channel=%E4%B8%AD%E5%A4%AE%E4%B8%80%E5%8F%B0']


def hit(path, timeout=20):
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d%s' % (PORT, path), timeout=timeout) as r:
            return r.code, len(r.read())
    except urllib.error.HTTPError as e:
        return e.code, len(e.read())
    except Exception as e:
        return None, type(e).__name__


rows = []
try:
    for p in PATHS:
        code, ln = hit(p)
        rows.append({'path': p, 'code': code, 'len_or_err': ln})
        print('%-46s -> %-5s %s' % (p, code, ln))
finally:
    srv.shutdown()
    srv.server_close()
    try:
        os.unlink(_m3u.name)
    except OSError:
        pass

print('ADB_HOST =', adb_mod.ADB_HOST)
print('ADB =', adb_mod.ADB)
print('ADB_CALLS_COUNT =', len(ADB_CALLS))
print('ADB_CALLS_SAMPLE =', ADB_CALLS[:3])
# adb get-state 是本机 adb server 查询（不带 -s 设备号），不算设备侧命令
device_calls = [c for c in ADB_CALLS if ' -s ' in c]
state_calls = [c for c in ADB_CALLS if ' -s ' not in c]
print('ADB_DEVICE_CALLS =', len(device_calls), '| ADB_LOCAL_STATE_CALLS =', len(state_calls))
print('ADB_DEVICE_CALLS_ALL_TARGET_127 =', all('127.0.0.1:1' in c for c in device_calls))
print('ADB_LOCAL_STATE_CALLS_SAMPLE =', state_calls[:3])
print('CONNECT_LOG_COUNT =', len(CONNECT_LOG))
print('OUTBOUND_VIOLATIONS =', len(VIOLATIONS), VIOLATIONS)
print('HARDCODED_PATCHED =', len(HARDCODED))
for h in HARDCODED:
    print('  %s.%s : %s -> %s | %s' % (h['module'], h['attr'], h['old'], h['new'], h['note']))

(OUT / 'STAGE1_ISOLATION_REPROBE.json').write_text(json.dumps({
    'adb_host': adb_mod.ADB_HOST, 'adb': adb_mod.ADB,
    'adb_calls_count': len(ADB_CALLS), 'adb_calls_all_target_127': all('127.0.0.1:1' in c for c in ADB_CALLS),
    'connect_log_count': len(CONNECT_LOG), 'violations': VIOLATIONS,
    'hardcoded_patched': HARDCODED, 'home_injection_sites': HOME_INJECTION_SITES,
    'routes': rows,
}, ensure_ascii=False, indent=1), encoding='utf-8')
print('WROTE STAGE1_ISOLATION_REPROBE.json')
