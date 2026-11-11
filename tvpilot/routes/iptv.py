"""TVPilot IPTV/EPG 路由。

绞杀者模式阶段3-1：/iptv/*, /api/iptv/*, /api/channels, /api/epg 路由。
处理函数签名：handle_xxx(handler, path, q) -> bool（True=已处理，False=未匹配）
"""
import os

from tvpilot.iptv import fetch_iptv_source, IPTV_DIRECT_FILE, get_current_channel_from_logcat
from tvpilot.epg import fetch_epg, _epg_cache_time
from tvpilot.logos import inject_tvg_logos, generate_channel_logo
from tvpilot.channels import NAMES, channel_numbers_map, channel_name_of_no


def handle_iptv_routes(handler, path, q):
    """处理所有 /iptv/* 和 /api/iptv/* GET 路由。返回 True 表示已处理。"""

    # RTSP 刷新状态查询
    if path == '/api/iptv/refresh_status':
        from tvpilot_server import _refresh_rtsp_state
        handler._json({
            'ok': True,
            'running': _refresh_rtsp_state['running'],
            'last_success': _refresh_rtsp_state['last_success'],
            'message': _refresh_rtsp_state['message'],
        })
        return True

    # IPTV 直播源代理
    if path in ('/iptv/source.m3u', '/iptv/source.m3u8', '/iptv.m3u', '/iptv.m3u8'):
        content, source = fetch_iptv_source()
        if content is None:
            handler._json({'ok': False, 'error': 'IPTV 源不可用（上游和缓存均失败）'}, 502)
            return True
        content = inject_tvg_logos(content)
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/vnd.apple.mpegurl; charset=utf-8')
        handler.send_header('Content-Length', str(len(content)))
        handler.send_header('X-Source', source)
        handler.send_header('X-Cache-Ts', str(int(_epg_cache_time)))
        handler.send_header('Cache-Control', 'public, max-age=3600')
        handler.end_headers()
        handler.wfile.write(content)
        return True

    # EPG 节目单代理
    if path in ('/iptv/epg.xml', '/iptv/epg', '/epg.xml'):
        content, source = fetch_epg()
        if content is None:
            handler._json({'ok': False, 'error': 'EPG 不可用'}, 502)
            return True
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/xml; charset=utf-8')
        handler.send_header('Content-Length', str(len(content)))
        handler.send_header('X-Source', source)
        handler.send_header('X-Cache-Ts', str(int(_epg_cache_time)))
        handler.send_header('Cache-Control', 'public, max-age=3600')
        handler.end_headers()
        handler.wfile.write(content)
        return True

    # 台标代理
    if path == '/iptv/logo':
        channel = q.get('channel', [''])[0]
        if not channel:
            handler._json({'ok': False, 'error': 'channel required'}, 400)
            return True
        png = generate_channel_logo(channel)
        if png:
            handler.send_response(200)
            handler.send_header('Content-Type', 'image/png')
            handler.send_header('Content-Length', str(len(png)))
            handler.send_header('Cache-Control', 'public, max-age=86400')
            handler.end_headers()
            handler.wfile.write(png)
        else:
            handler._json({'ok': False, 'error': 'logo generation failed'}, 500)
        return True

    # 直连 RTSP 源
    if path in ('/iptv/source_direct.m3u', '/iptv/direct.m3u'):
        try:
            if os.path.exists(IPTV_DIRECT_FILE):
                with open(IPTV_DIRECT_FILE, 'rb') as f:
                    content = f.read()
                if content and len(content) > 100:
                    handler.send_response(200)
                    handler.send_header('Content-Type', 'application/vnd.apple.mpegurl; charset=utf-8')
                    handler.send_header('Content-Length', str(len(content)))
                    handler.send_header('Cache-Control', 'public, max-age=3600')
                    handler.end_headers()
                    handler.wfile.write(content)
                    return True
        except Exception as e:
            print(f'直连源读取失败: {e}', flush=True)
        handler._json({'ok': False, 'error': '直连 RTSP 源不可用'}, 502)
        return True

    return False


def handle_channels(handler, path, q):
    """处理 /api/channels GET 路由。F2: 频道清单来自电信抓包 M3U；台号取自单一真源。"""
    if path != '/api/channels':
        return False
    try:
        import re as _re
        from tvpilot.iptv import IPTV_LOCAL_FILE
        numbers = channel_numbers_map()
        channels = []
        groups = []
        group_map = {}
        _m3u_sz = os.path.getsize(IPTV_LOCAL_FILE) if os.path.exists(IPTV_LOCAL_FILE) else 0
        if _m3u_sz > 50 * 1024 * 1024:
            print(f'[channels] M3U 过大({_m3u_sz}B)，拒绝一次性读入以防内存暴涨', flush=True)
            lines = []
        else:
            with open(IPTV_LOCAL_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        current_group = ''
        for line in lines:
            line = line.strip()
            if line.startswith('#EXTINF:'):
                tvg_name = _re.search(r'tvg-name="([^"]*)"', line)
                group_title = _re.search(r'group-title="([^"]*)"', line)
                name = tvg_name.group(1) if tvg_name else ''
                if group_title:
                    current_group = group_title.group(1)
                    if current_group not in group_map:
                        group_map[current_group] = len(groups)
                        groups.append({'name': current_group, 'channels': []})
                no = numbers.get(name, 0)
                channels.append({
                    'no': no,
                    'name': name,
                    'group': current_group,
                    'logo': '',
                    'zappable': bool(no),
                })
                if current_group in group_map:
                    groups[group_map[current_group]]['channels'].append(no)
        channels.sort(key=lambda x: (x['no'] == 0, x['no']))
        zappable = sum(1 for c in channels if c['zappable'])
        handler._json({
            'ok': True,
            'count': len(channels),
            'zappable_count': zappable,
            'go2rtc_count': len(NAMES),
            'count_semantics': {
                'count': '电信抓包 M3U 频道总数（/api/channels 列表长度）',
                'zappable_count': '其中在 channel_numbers.json 有数字台号、可数字键换台数',
                'go2rtc_count': 'channels.json 的 go2rtc 可播流数（mytv 实际可播集合，/api/health.channels_count）',
            },
            'groups': groups, 'channels': channels})
    except Exception as e:
        handler._json({'ok': False, 'tool': 'channels', 'error': 'read_m3u_failed', 'message': str(e)}, 500)
    return True


def handle_epg_api(handler, path, q):
    """处理 /api/epg GET 路由：返回指定频道今日节目单。"""
    if path != '/api/epg':
        return False
    try:
        import time
        import xml.etree.ElementTree as ET
        channel_name = q.get('channel', [''])[0]
        content, source = fetch_epg()
        root = ET.fromstring(content)
        channel_id = None
        for ch in root.findall('channel'):
            for dn in ch.findall('display-name'):
                if dn.text and channel_name in dn.text:
                    channel_id = ch.get('id')
                    break
            if channel_id:
                break
        if not channel_id:
            handler._json({'ok': True, 'channel': channel_name, 'programs': []})
            return True
        programs = []
        today = time.strftime('%Y%m%d')
        for prog in root.findall('programme'):
            if prog.get('channel') != channel_id:
                continue
            start = prog.get('start', '')
            stop = prog.get('stop', '')
            if start.startswith(today):
                start_time = start[8:10] + ':' + start[10:12]
                stop_time = stop[8:10] + ':' + stop[10:12] if len(stop) >= 12 else ''
                title_el = prog.find('title')
                title = title_el.text if title_el is not None else ''
                desc_el = prog.find('desc')
                desc = desc_el.text if desc_el is not None else ''
                programs.append({
                    'start': start_time,
                    'stop': stop_time,
                    'title': title,
                    'desc': desc
                })
        handler._json({'ok': True, 'channel': channel_name, 'programs': programs})
    except Exception as e:
        handler._json({'ok': False, 'error': 'epg_failed', 'message': str(e)}, 500)
    return True


__all__ = [
    'handle_iptv_routes', 'handle_channels', 'handle_epg_api',
]
