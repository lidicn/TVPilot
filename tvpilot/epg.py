"""TVPilot EPG 节目单代理层。

绞杀者模式阶段14迁移：51zmt EPG 上游拉取 + 频道名映射 + 内存/磁盘缓存。
"""
import os
import re
import time
import urllib.request

EPG_UPSTREAM = 'http://epg.51zmt.top:8000/e.xml'
EPG_CACHE_FILE = os.environ.get('TVPILOT_EPG_CACHE', '/app/data/epg.xml')
EPG_CACHE_TTL = 21600  # 6 小时

_epg_cache_time = 0
_epg_cache_content = None

# 51zmt EPG 频道名 → mytv 频道名映射
EPG_CHANNEL_MAP = {}
for _i in range(1, 18):
    _cn_num = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十',
               '十一', '十二', '十三', '十四', '十五', '十六', '十七'][_i - 1]
    EPG_CHANNEL_MAP[f'CCTV{_i}'] = f'中央{_cn_num}台'
EPG_CHANNEL_MAP['CCTV5+'] = '中央五+'
EPG_CHANNEL_MAP['CCTV4K'] = '中央4K'
EPG_CHANNEL_MAP['CCTV16'] = '中央十六台'
EPG_CHANNEL_MAP.update({
    '广东卫视': '广东卫视',
    '深圳卫视': '深圳卫视',
    '广州卫视': '广州卫视',
    '大湾区卫视': '大湾区卫视',
})


def _map_epg_channel_name(name):
    """将 51zmt EPG 频道名映射为 mytv 频道名。"""
    if name in EPG_CHANNEL_MAP:
        return EPG_CHANNEL_MAP[name]
    return name


def fetch_epg():
    """获取 EPG 节目单 XML。拉取 51zmt 源后做频道名映射，带缓存。返回 (content_bytes, source)。"""
    global _epg_cache_time, _epg_cache_content

    now = time.time()
    # 1. 内存缓存
    if _epg_cache_content and (now - _epg_cache_time) < EPG_CACHE_TTL:
        return _epg_cache_content, 'cache@%d' % int(_epg_cache_time)

    # 2. 拉取上游
    try:
        req = urllib.request.Request(EPG_UPSTREAM, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode('utf-8', errors='replace')

        mapped = raw
        for src_name, dst_name in EPG_CHANNEL_MAP.items():
            if src_name == dst_name:
                continue
            pattern = r'(<display-name[^>]*>)' + re.escape(src_name) + r'(</display-name>)'
            mapped = re.sub(pattern, r'\g<1>' + dst_name + r'\g<2>', mapped)

        content = mapped.encode('utf-8')
        _epg_cache_content = content
        _epg_cache_time = now
        try:
            os.makedirs(os.path.dirname(EPG_CACHE_FILE), exist_ok=True)
            with open(EPG_CACHE_FILE, 'wb') as f:
                f.write(content)
        except Exception:
            pass
        return content, 'upstream_mapped'
    except Exception as e:
        print(f'获取 EPG 上游失败: {e}', flush=True)

    # 3. 磁盘缓存
    try:
        if os.path.exists(EPG_CACHE_FILE):
            with open(EPG_CACHE_FILE, 'rb') as f:
                content = f.read()
            if content and len(content) > 100:
                _epg_cache_content = content
                _epg_cache_time = now
                return content, 'disk_cache'
    except Exception:
        pass

    return None, 'unavailable'


__all__ = [
    'EPG_UPSTREAM', 'EPG_CACHE_FILE', 'EPG_CACHE_TTL',
    'EPG_CHANNEL_MAP', '_map_epg_channel_name', 'fetch_epg',
]
