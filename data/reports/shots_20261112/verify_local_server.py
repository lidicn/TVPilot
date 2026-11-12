# -*- coding: utf-8 -*-
"""本机 127.0.0.1 临时 server 只读验收（不碰容器 / 不重建镜像 / 不碰真机 ADB 写操作）。

用工作区代码起一个空闲端口的 HTTP server，打：
  1. /api/health            -> 无 go2rtc_creds_configured 字段
  2. /api/channels          -> count=80
  3. /api/iptv/refresh_status -> 410 deprecated
  4. 旧版 8 条 GET          -> 410 deprecated（与 POST 同包络）
跑完即 shutdown，不留后台进程。
用法：python data/reports/shots_20261112/verify_local_server.py
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
sys.path.insert(0, str(ROOT))

# 本机没有容器里的外挂 M3U，指向一份假 M3U 让 /api/channels 不因缺文件 500；
# count 仍取 channels.json（80），与本假文件条目数无关。
_m3u = tempfile.NamedTemporaryFile('w', suffix='.m3u', delete=False, encoding='utf-8')
_m3u.write('#EXTM3U\n#EXTINF:-1 tvg-name="中央一台" group-title="央视",中央一台\nhttp://example.invalid/1\n')
_m3u.close()
os.environ['TVPILOT_IPTV_SOURCE'] = _m3u.name

import tvpilot_server as s  # noqa: E402

PORT = 18091
srv = ThreadingHTTPServer(('127.0.0.1', PORT), s.TVPilotHandler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()


def hit(path):
    url = 'http://127.0.0.1:%d%s' % (PORT, path)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.code, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')
    except Exception as e:
        return None, repr(e)


DEPRECATED_BODY = {'ok': False, 'error': 'deprecated', 'see': 'archive/README.md'}
results = {}
try:
    for path in ('/api/health', '/api/channels', '/api/iptv/refresh_status',
                 '/health', '/channels', '/active', '/zap', '/screenshot',
                 '/fongmi/search', '/keyevent', '/tap'):
        code, body = hit(path)
        results[path] = {'code': code, 'body': body}
        print('%s -> %s %s' % (path, code, body[:220].replace('\n', ' ')))

    # 判据
    h = json.loads(results['/api/health']['body'])
    print('CHECK health.go2rtc_creds_configured_present =',
          'go2rtc_creds_configured' in h, '| status =', h.get('status'))
    c = json.loads(results['/api/channels']['body'])
    print('CHECK channels.count =', c.get('count'),
          '| zappable_count =', c.get('zappable_count'))
    print('CHECK refresh_status 410 =',
          results['/api/iptv/refresh_status']['code'] == 410)
    legacy = ['/health', '/channels', '/active', '/zap', '/screenshot',
              '/fongmi/search', '/keyevent', '/tap']
    ok = all(results[p]['code'] == 410 and
             json.loads(results[p]['body']) == DEPRECATED_BODY for p in legacy)
    print('CHECK legacy 8 GET all 410 =', ok)
finally:
    srv.shutdown()
    srv.server_close()
    try:
        os.unlink(_m3u.name)
    except OSError:
        pass
