#!/usr/bin/env python3
"""Patch tvpilot_server.py to add camera streams to M3U"""
import re

FILE = '/app/tvpilot_server.py'

with open(FILE, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add camera list constant after IPTV_CACHE_FILE line
camera_const = '''
# v1.0: 米家摄像头列表（go2rtc cam_ 流），追加到 M3U 供 mytv 查看监控
CAMERA_STREAMS = [
    {"name": "客厅监控", "go2rtc": "cam_客厅", "group": "监控摄像头"},
    {"name": "客厅监控标清", "go2rtc": "cam_客厅标清", "group": "监控摄像头"},
    {"name": "小黄人监控", "go2rtc": "cam_小黄人", "group": "监控摄像头"},
    {"name": "书房监控", "go2rtc": "cam_书房", "group": "监控摄像头"},
]
'''

if 'CAMERA_STREAMS' not in content:
    content = content.replace(
        "IPTV_CACHE_FILE = '/app/data/iptv_source.m3u'",
        "IPTV_CACHE_FILE = '/app/data/iptv_source.m3u'\n" + camera_const,
        1
    )
    print("Added CAMERA_STREAMS constant")
else:
    print("CAMERA_STREAMS already exists")

# 2. Add generate_camera_m3u function before inject_tvg_logos
camera_func = '''
def generate_camera_m3u():
    """生成摄像头 M3U 条目（追加到 IPTV M3U 末尾）。"""
    import os
    import urllib.parse
    go2rtc_auth = os.environ.get('GO2RTC_AUTH')
    if not go2rtc_auth:
        raise RuntimeError('GO2RTC_AUTH 未设置：拒绝生成无凭据的摄像头 M3U（见 .env.example）')
    lines = []
    for cam in CAMERA_STREAMS:
        src_encoded = urllib.parse.quote(cam["go2rtc"])
        logo_url = f'http://192.168.2.200:8090/iptv/logo?channel={urllib.parse.quote(cam["name"])}'
        stream_url = f'http://{go2rtc_auth}@192.168.2.200:1984/api/stream.m3u8?src={src_encoded}'
        lines.append(f'#EXTINF:-1 tvg-logo="{logo_url}" tvg-name="{cam["name"]}" group-title="{cam["group"]}",{cam["name"]}')
        lines.append(stream_url)
    return '\\n'.join(lines) + '\\n'

'''

if 'def generate_camera_m3u' not in content:
    content = content.replace(
        'def inject_tvg_logos(',
        camera_func + 'def inject_tvg_logos(',
        1
    )
    print("Added generate_camera_m3u function")
else:
    print("generate_camera_m3u already exists")

# 3. Inject camera streams into M3U response (after inject_tvg_logos)
old_m3u = "                content = inject_tvg_logos(content)\n                self.send_response(200)"
new_m3u = """                content = inject_tvg_logos(content)
                # v1.0: 追加摄像头监控流到 M3U 末尾
                content = content.rstrip() + '\\n' + generate_camera_m3u()
                self.send_response(200)"""

if 'generate_camera_m3u()' not in content.split('def generate_camera_m3u')[1].split('def ')[0] if 'def generate_camera_m3u' in content else True:
    if old_m3u in content:
        content = content.replace(old_m3u, new_m3u, 1)
        print("Injected camera streams into M3U endpoint")
    else:
        print("WARNING: M3U endpoint pattern not found, checking...")
        # Try alternative pattern
        if 'content = inject_tvg_logos(content)' in content:
            print("  inject_tvg_logos call found, but send_response pattern differs")
else:
    print("Camera injection already present")

with open(FILE, 'w', encoding='utf-8') as f:
    f.write(content)

print("Done. File size:", len(content))
