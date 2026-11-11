"""TVPilot IPTV 直播源代理层。

绞杀者模式阶段15迁移：M3U 源管理 + 频道 ID 映射 + logcat 当前频道读取 + 缓存。
"""
import os
import re
import time
import subprocess
import urllib.request

from tvpilot.adb import ADB_HOST

DATA_DIR = os.environ.get('TVPILOT_DATA_DIR', '/app/data')

IPTV_UPSTREAM = 'http://192.168.2.200:8666/'
IPTV_LOCAL_FILE = os.environ.get('TVPILOT_IPTV_SOURCE', os.path.join(DATA_DIR, 'gd_iptv_source.m3u'))
IPTV_DIRECT_FILE = os.environ.get('TVPILOT_IPTV_DIRECT', os.path.join(DATA_DIR, 'gd_iptv_direct.m3u'))
IPTV_CACHE_FILE = os.environ.get('TVPILOT_IPTV_CACHE', os.path.join(DATA_DIR, 'iptv_source.m3u'))
IPTV_CACHE_TTL = 3600

_iptv_cache_time = 0
_iptv_cache_content = None
_channel_id_map = {}
_channel_name_map = {}


def _load_channel_id_map():
    """从直连 M3U 文件加载频道 ID → 频道名称映射。"""
    global _channel_id_map, _channel_name_map
    try:
        with open(IPTV_DIRECT_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        name = ''
        for line in lines:
            line = line.strip()
            if line.startswith('#EXTINF:'):
                tvg_name = re.search(r'tvg-name="([^"]*)"', line)
                name = tvg_name.group(1) if tvg_name else ''
            elif line.startswith('rtsp://'):
                m = re.search(r'/([0-9]+_[0-9]+)\.smil', line)
                if m and name:
                    ch_id = m.group(1)
                    _channel_id_map[ch_id] = name
                    _channel_name_map[name] = ch_id
        print(f'[ChannelID] 加载 {len(_channel_id_map)} 个频道映射', flush=True)
    except Exception as e:
        print(f'[ChannelID] 加载失败: {e}', flush=True)


_load_channel_id_map()


def get_current_channel_from_logcat():
    """从 IPTV SharedPreferences 读取当前播放频道号。"""
    try:
        result = subprocess.run(
            ['adb', '-s', ADB_HOST, 'shell',
             'run-as', 'com.android.smart.terminal.iptv',
             'cat', 'shared_prefs/iptv_prefs.xml'],
            capture_output=True, text=True, timeout=5
        )
        m = re.search(r'name="Service/LastchannelID"\s+value="(\d+)"', result.stdout)
        if m:
            return int(m.group(1))
        return None
    except Exception as e:
        print(f'[ChannelID] 读取失败: {e}', flush=True)
        return None


def fetch_iptv_source():
    """获取 M3U 直播源。优先本地电信源文件，失败时回退上游+缓存。返回 (content_bytes, source)。"""
    global _iptv_cache_time, _iptv_cache_content

    # 1. 优先读取本地电信源文件
    try:
        if os.path.exists(IPTV_LOCAL_FILE):
            with open(IPTV_LOCAL_FILE, 'rb') as f:
                content = f.read()
            if content and len(content) > 100:
                return content, 'local_telecom'
    except Exception as e:
        print(f'读取本地电信源失败: {e}', flush=True)

    # 2. 内存缓存
    now = time.time()
    if _iptv_cache_content and (now - _iptv_cache_time) < IPTV_CACHE_TTL:
        return _iptv_cache_content, 'cache'

    # 3. 尝试从上游获取
    try:
        req = urllib.request.Request(IPTV_UPSTREAM, headers={'User-Agent': 'TVPilot/1.0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            content = r.read()
        if content and len(content) > 100:
            _iptv_cache_content = content
            _iptv_cache_time = now
            try:
                os.makedirs(os.path.dirname(IPTV_CACHE_FILE), exist_ok=True)
                with open(IPTV_CACHE_FILE, 'wb') as f:
                    f.write(content)
            except Exception:
                pass
            return content, 'upstream'
    except Exception as e:
        print(f'获取上游 IPTV 源失败: {e}', flush=True)

    # 4. 内存缓存（过期也用）
    if _iptv_cache_content:
        return _iptv_cache_content, 'cache_stale'

    # 5. 磁盘缓存
    try:
        if os.path.exists(IPTV_CACHE_FILE):
            with open(IPTV_CACHE_FILE, 'rb') as f:
                content = f.read()
            if content and len(content) > 100:
                _iptv_cache_content = content
                _iptv_cache_time = now
                return content, 'disk_cache'
    except Exception:
        pass

    return None, 'unavailable'


__all__ = [
    'IPTV_UPSTREAM', 'IPTV_LOCAL_FILE', 'IPTV_DIRECT_FILE',
    'IPTV_CACHE_FILE', 'IPTV_CACHE_TTL',
    '_channel_id_map', '_channel_name_map',
    '_load_channel_id_map', 'get_current_channel_from_logcat', 'fetch_iptv_source',
]
