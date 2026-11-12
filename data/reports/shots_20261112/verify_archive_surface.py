# -*- coding: utf-8 -*-
"""归档后活面离线探针（不碰真机 / 容器，只调 handler 层）。

用法：python data/reports/shots_20261112/verify_archive_surface.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tvpilot.health as h
import tvpilot.iptv as ip
import tvpilot.routes.iptv as ri


def _offline(*a, **k):
    raise OSError('offline stub: no device / no network')


h.adb_ready = _offline
h.urllib.request.urlopen = _offline

hc = h.health_check()
print('HEALTH.status =', hc['status'])
print('HEALTH.channels_count =', hc['channels_count'])
print('HEALTH.zappable_count =', hc['zappable_count'])
print('HEALTH.go2rtc_creds_configured_present =', 'go2rtc_creds_configured' in hc)

tmp = tempfile.NamedTemporaryFile('w', suffix='.m3u', delete=False, encoding='utf-8')
tmp.write('#EXTM3U\n#EXTINF:-1 tvg-name="中央一台",中央一台\n')
tmp.close()
ip.IPTV_LOCAL_FILE = tmp.name


class F:
    def __init__(self):
        self.obj = None
        self.code = None

    def _json(self, obj, code=200):
        self.obj = obj
        self.code = code


f = F()
ri.handle_channels(f, '/api/channels', {})
print('CHANNELS.code =', f.code)
print('CHANNELS.count =', f.obj['count'])
print('CHANNELS.zappable_count =', f.obj['zappable_count'])
print('CHANNELS.m3u_entry_count =', f.obj['m3u_entry_count'])
print('CHANNELS.has_go2rtc_count =', 'go2rtc_count' in f.obj)

g = F()
ri.handle_iptv_routes(g, '/api/iptv/refresh_status', {})
print('REFRESH_STATUS.code =', g.code)
print('REFRESH_STATUS.body =', json.dumps(g.obj, ensure_ascii=False))

os.unlink(tmp.name)
