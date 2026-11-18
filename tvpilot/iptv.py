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
    """从 IPTV SharedPreferences 读取当前播放频道号（**仅作旁证/留痕**）。

    ⚠ 2026-11-17 实测：本机多位数注入时 `LastchannelID` 只写首位（注 22 读 2、
    21 读 1），且 `play_state.channel_no` 是缓存旧值 ⇒ 两者都**不可信**，
    ⛔ 不得用作 S4 判据。可信台号改用 `resolve_channel_from_livepath()`。
    """
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


# ---- 2026-11-17：换台瞬间从 CTCMediaPlayer 的 livePath 日志解可信台号 ----
# 文档 `doc/archive/电信IPTV_RTSP地址抓取流程.md`：换台瞬间 logcat 输出
#   CTCMediaPlayer: MediaPlayer > setDataSource --livePath--->rtsp://.../PLTV/88888895/224/{channel_id}/{stream_id}_0.smil...
# channel_id / stream_id 固定，可反解台号。⚠ 该行只在换台瞬间出现、非常驻，
# 必须「清 logcat → 注入台号 → 等 7-12s → 抓 setDataSource」同步进行。
_LIVEPATH_RE = re.compile(
    r'setDataSource\s*--livePath--->\s*(rtsp://[^\s]+/PLTV/\d+/\d+/(\d+)/[^_\s]+_0\.smil)')
_channel_id_to_no = None   # 懒加载 {channel_id(str): 台号(int)}


def parse_channel_id_from_logcat(text):
    """从一段 logcat 文本解析最新 livePath 的 channel_id（纯函数，不连设备）。

    :return: (channel_id:str|None, matched_rtsp:bool) —— 只回 channel_id，不回完整
             URL（URL 含认证串，凭据零打印）；无命中返回 (None, False)。
    """
    if not text:
        return None, False
    found = None
    for line in text.splitlines():
        m = _LIVEPATH_RE.search(line)
        if m:
            found = m.group(2)     # 取最后一条（最近一次换台）
    return (found, found is not None)


def _load_channel_id_to_no():
    """离线建 {channel_id: 台号}：channel_id 取自 channels.json 各台的 RTSP URL，
    台号取自 channel_numbers.json（台名→台号）。纯读本地文件，不连设备、不打印 URL 值。"""
    global _channel_id_to_no
    if _channel_id_to_no is not None:
        return _channel_id_to_no
    mapping = {}
    try:
        import json
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, 'channels.json'), encoding='utf-8') as f:
            chans = json.load(f)                       # {台名: rtsp_url}
        nums_path = os.path.join(base, 'data', 'channel_numbers.json')
        with open(nums_path, encoding='utf-8') as f:
            _nums_doc = json.load(f)
        # 真源是嵌套结构 {"numbers": {台名: 台号}, "_comment":..., ...}；
        # 兼容旧的平铺结构，避免映射建成 0 条又静默。
        nums = _nums_doc.get('numbers', _nums_doc) if isinstance(_nums_doc, dict) else {}
        for name, url in chans.items():
            m = re.search(r'/PLTV/\d+/\d+/(\d+)/', str(url))
            no = nums.get(name)
            if m and no is not None:
                mapping[m.group(1)] = int(no)
    except Exception:
        pass
    _channel_id_to_no = mapping
    return mapping


def resolve_channel_from_livepath(logcat_text):
    """换台后从 livePath 日志反解可信台号（fail-closed）。

    :return: (no:int|None, reason:str|None, channel_id:str|None)
        - 命中 livePath 且 channel_id 在映射中 ⇒ (台号, None, channel_id)
        - 命中但映射不到 ⇒ (None, 'channel_id_unmapped', channel_id)
        - 无 livePath ⇒ (None, 'livepath_absent', None)
    ⛔ 不猜、不回退 LastchannelID/缓存；拿不到就是 unknown+reason。
    """
    cid, matched = parse_channel_id_from_logcat(logcat_text)
    if not matched:
        return None, 'livepath_absent', None
    m = _load_channel_id_to_no()
    no = m.get(cid)
    if no is None:
        return None, 'channel_id_unmapped', cid
    return no, None, cid


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
