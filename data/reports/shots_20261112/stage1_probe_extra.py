# -*- coding: utf-8 -*-
"""阶段 1 补拨：3 条超时 GET 用长超时重拿状态码 + fail-closed 参数校验读数。

隔离同 stage1_probe.py：ADB/外部依赖全指向 127.0.0.1:1，零注入、零前台变更。
M3U/EPG 正文含 URL 行，只记录 code + Content-Length，绝不打印正文（凭据零打印）。
用法：python data/reports/shots_20261112/stage1_probe_extra.py
"""
import json
import os
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

_m3u = tempfile.NamedTemporaryFile('w', suffix='.m3u', delete=False, encoding='utf-8')
_m3u.write('#EXTM3U\n'
           '#EXTINF:-1 tvg-name="中央一台" group-title="央视",中央一台\n'
           'http://example.invalid/1\n')
_m3u.close()
os.environ['TVPILOT_IPTV_SOURCE'] = _m3u.name
os.environ['TVPILOT_TV_ADB'] = '127.0.0.1:1'
os.environ['TVPILOT_A11Y_HTTP'] = 'http://127.0.0.1:1'
os.environ['DOUBAO_API_URL'] = 'http://127.0.0.1:1/v1/chat/completions'
os.environ['DOUBAO_API_KEY'] = ''
os.environ['TVPILOT_DATA_DIR'] = str(ROOT / 'data')

import tvpilot_server as s  # noqa: E402

PORT = 18093
srv = ThreadingHTTPServer(('127.0.0.1', PORT), s.TVPilotHandler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()


def hit(path, timeout=40, body_limit=0):
    """body_limit=0 时只回 code + Content-Length，不读正文（凭据零打印）。"""
    url = 'http://127.0.0.1:%d%s' % (PORT, path)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw = r.read()
            clen = r.headers.get('Content-Length') or len(raw)
            summary = ('len=%s' % clen) if not body_limit else _safe(raw[:body_limit])
            return r.code, summary
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, _safe(raw[:body_limit]) if body_limit else 'len=%d' % len(raw)
    except Exception as e:
        return None, type(e).__name__


def _safe(b):
    try:
        d = json.loads(b.decode('utf-8', 'replace'))
    except Exception:
        return 'non-json(len=%d)' % len(b)
    if not isinstance(d, dict):
        return type(d).__name__
    return json.dumps({k: d[k] for k in ('ok', 'error', 'status', 'count', 'message', 'tool')
                       if k in d and isinstance(d[k], (bool, int, float, str))},
                      ensure_ascii=False, separators=(',', ':'))


# (组, 路径, 说明)
CASES = [
    ('state_routes', '/api/foreground', 'ADB 不可达 -> tv_unreachable 包络（长超时）'),
    ('state_routes', '/api/system_state', '聚合状态（长超时）'),
    ('epg_api', '/api/epg', 'EPG 今日节目单（长超时）'),
    ('iptv_routes', '/iptv/source.m3u', 'M3U 代理：只记 code+len，正文含 URL 不打印'),
    ('iptv_routes', '/iptv/epg.xml', 'EPG XML 代理：只记 code+len'),
    ('monitor_get', '/api/metrics?minutes=abc', 'fail-closed：非法 minutes -> 400'),
    ('monitor_get', '/api/alerts?limit=abc', 'fail-closed：非法 limit -> 400'),
    ('iptv_routes', '/iptv/logo', 'fail-closed：缺 channel -> 400'),
]

rows = []
try:
    for grp, p, why in CASES:
        code, summary = hit(p, body_limit=400 if 'fail-closed' in why else 0)
        rows.append({'group': grp, 'path': p, 'code': code,
                     'summary': summary, 'note': why})
        print('%-16s %-34s -> %-5s %-52s | %s' % (grp, p, code, summary, why))
finally:
    srv.shutdown()
    srv.server_close()
    try:
        os.unlink(_m3u.name)
    except OSError:
        pass

(OUT / 'STAGE1_ROUTE_PROBE_EXTRA.json').write_text(
    json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
print('EXTRA_ROWS =', len(rows))
