"""TVPilot 状态查询层。

绞杀者模式阶段8迁移：前台包名、播放状态、界面状态、系统状态聚合。
自包含：MYTV_PKG 从环境变量读。
"""
import os
import re
import time

from tvpilot.adb import adb_shell_output
from tvpilot.a11y import _get_screen_texts
from tvpilot.audio import get_audio_info
from tvpilot.errors import ERR_TV_UNREACHABLE

MYTV_PKG = os.environ.get('TVPILOT_MYTV_PKG', 'com.tvcam.mytv')
IPTV_PKG = os.environ.get('TVPILOT_IPTV_PKG', 'com.android.smart.terminal.iptv')

# 当前频道缓存（tool_zap 换台成功后更新，play_state 读取）
# 持久化到 data/current_channel.json，容器重启后恢复
_current_channel = {'name': None, 'no': None, 'updated_at': None}
_channel_cache_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'current_channel.json')

def _load_channel_cache():
    """启动时从文件加载频道缓存。"""
    global _current_channel
    try:
        if os.path.exists(_channel_cache_file):
            import json as _json
            with open(_channel_cache_file, 'r', encoding='utf-8') as f:
                _current_channel = _json.load(f)
    except Exception:
        pass

def _save_channel_cache():
    """保存频道缓存到文件。"""
    try:
        os.makedirs(os.path.dirname(_channel_cache_file), exist_ok=True)
        import json as _json
        with open(_channel_cache_file, 'w', encoding='utf-8') as f:
            _json.dump(_current_channel, f, ensure_ascii=False)
    except Exception:
        pass

# 启动时加载
_load_channel_cache()

def set_current_channel(name, no):
    """更新当前频道缓存（由 tool_zap 调用），并持久化。"""
    global _current_channel
    _current_channel = {'name': name, 'no': no, 'updated_at': int(time.time())}
    _save_channel_cache()

def get_current_channel():
    """获取当前频道缓存。"""
    return dict(_current_channel)

# 界面状态分类规则（从主文件迁移）
UI_STATE_RULES = [
    ('play', ['com.tvcam.mytv', 'com.trim.tv', 'org.xbmc.kodi', 'com.android.smart.terminal.iptv'],
     ['播放', '暂停', '频道', '电视台', '正在播放'], '视频播放界面'),
    ('search', ['com.trim.tv', 'com.tvcam.mytv'],
     ['搜索', '搜索结果', '输入搜索', '请输入搜索词'], '搜索界面'),
    ('settings', ['com.android.tv.settings', 'com.xiaomi.mitv.settings', 'com.android.settings'],
     ['设置', '关于', '网络', '显示', '声音'], '系统设置界面'),
    ('home', ['com.mitv.tvhome', 'com.xiaomi.mitv.tvhome', 'com.dangbei.tvlauncher'],
     ['首页', '推荐', '应用', '我的'], '电视桌面/Launcher'),
    ('dialog', [],
     ['确定', '取消', '是否', '提示', '警告', '确认'], '弹窗/对话框'),
    ('loading', [],
     ['加载', '缓冲', '正在加载', '请稍候'], '加载/缓冲中'),
    ('ad', [],
     ['广告', '跳过广告', 'VIP'], '广告界面'),
]


# get_foreground_pkg 缓存（1 秒 TTL，减少换台流程中的重复调用）
_fg_cache = {'pkg': None, 'ts': 0}
_FG_CACHE_TTL = 1.0

def get_foreground_pkg(force_refresh=False):
    """返回当前前台应用包名；失败返回 None。

    v1.2：加 1 秒缓存，换台流程中多次调用只真正查询一次。
    v1.1：优先用 6095 端口 capturescreen（更快，不需 dumpsys），失败降级 ADB dumpsys。
    """
    global _fg_cache
    now = time.time()
    if not force_refresh and _fg_cache['pkg'] and (now - _fg_cache['ts']) < _FG_CACHE_TTL:
        return _fg_cache['pkg']
    # 优先用 6095 端口（更快，约 200ms vs dumpsys 500ms+）
    pkg = None
    try:
        from tvpilot.screenshot import get_foreground_mitv
        fg = get_foreground_mitv()
        if fg and fg.get('pkg'):
            pkg = fg['pkg']
    except Exception:
        pass
    # 降级：ADB dumpsys
    if pkg is None:
        try:
            out = adb_shell_output('dumpsys activity activities')
            m = re.search(r'(?:mResumedActivity|ResumedActivity)\s*:?\s*ActivityRecord\{[^}]*\s+([\w.]+)/', out)
            if m:
                pkg = m.group(1)
            else:
                m = re.search(r'mFocusedApp[^{]*\{[^}]*\s+([\w.]+)/', out)
                if m:
                    pkg = m.group(1)
        except Exception as e:
            print(f'get_foreground_pkg error: {e}', flush=True)
    if pkg:
        _fg_cache = {'pkg': pkg, 'ts': now}
    return pkg


def mytv_alive():
    """mytv 进程是否存活。"""
    try:
        out = adb_shell_output(f'pidof {MYTV_PKG}')
        return bool(out.strip())
    except Exception:
        return False


def get_screen_state():
    """获取屏幕状态（亮/暗/休眠）。"""
    try:
        out = adb_shell_output('dumpsys power')
        state = {'screen_on': False, 'wakefulness': 'unknown'}
        m = re.search(r'mWakefulness=(\w+)', out)
        if m:
            state['wakefulness'] = m.group(1)
            state['screen_on'] = m.group(1) == 'Awake'
        m = re.search(r'Display Power: state=(\w+)', out)
        if m:
            state['display_power'] = m.group(1)
            if m.group(1) == 'ON':
                state['screen_on'] = True
        return state
    except Exception:
        return {'screen_on': None, 'wakefulness': 'unknown', 'error': 'parse_failed'}


def get_network_state():
    """获取网络连接状态。"""
    try:
        out = adb_shell_output('dumpsys connectivity')
        state = {'connected': False, 'type': 'unknown', 'ip': None}
        m = re.search(r'type:\s*(\w+)\[.*state:\s*CONNECTED', out)
        if m:
            state['type'] = m.group(1).lower()
            state['connected'] = True
        m = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/24', out)
        if m:
            state['ip'] = m.group(1)
        return state
    except Exception:
        return {'connected': None, 'type': 'unknown', 'ip': None, 'error': 'parse_failed'}


def tool_ui_state():
    """界面状态分类：基于前台包名+无障碍文字，返回轻量状态（无需 LLM 看图）。"""
    pkg = get_foreground_pkg()
    if pkg is None:
        return None, ERR_TV_UNREACHABLE, '无法获取前台包名'

    texts, text_source = _get_screen_texts()
    all_text = ' '.join(texts)

    for state, pkg_patterns, keywords, desc in UI_STATE_RULES:
        pkg_match = any(p in pkg for p in pkg_patterns) if pkg_patterns else False
        text_match = any(kw in all_text for kw in keywords) if keywords else False
        if state in ('dialog', 'loading', 'ad'):
            if text_match:
                return {'state': state, 'package': pkg, 'description': desc,
                        'text_count': len(texts), 'text_source': text_source,
                        'confidence': 'text_match'}, None, None
        else:
            if pkg_match or (pkg_match and text_match):
                return {'state': state, 'package': pkg, 'description': desc,
                        'text_count': len(texts), 'text_source': text_source,
                        'confidence': 'pkg_match' if pkg_match else 'text_match'}, None, None

    return {'state': 'unknown', 'package': pkg, 'description': '未识别的界面状态',
            'text_count': len(texts), 'text_source': text_source, 'confidence': 'none'}, None, None


def tool_play_state():
    """播放状态检测：整合 mytv API + 前台包名 + 界面文字。"""
    pkg = get_foreground_pkg()
    if pkg is None:
        return None, ERR_TV_UNREACHABLE, '无法获取前台包名'

    result = {'package': pkg, 'playing': False, 'state': 'stopped', 'channel': None}

    # 旧 mytv HTTP 代理已随归档链路摘除（见 archive/README.md），此处不再查询。

    # 飞牛TV 在前台时，用界面文字判断
    if 'com.trim.tv' in pkg:
        texts, text_source = _get_screen_texts()
        if texts:
            all_text = ' '.join(texts)
            if '暂停' in all_text or '播放' in all_text:
                result['state'] = 'playing'
                result['playing'] = True
            elif '缓冲' in all_text or '加载' in all_text:
                result['state'] = 'buffering'
            result['source'] = text_source
            return result, None, None

    # 电信机顶盒在前台时，默认认为在播放，从缓存获取当前频道
    if IPTV_PKG in pkg:
        result['state'] = 'playing'
        result['playing'] = True
        result['source'] = 'iptv_stb'
        ch = get_current_channel()
        result['channel'] = ch.get('name')  # 始终返回，无缓存时为 null
        result['channel_no'] = ch.get('no')  # 始终返回，无缓存时为 null
        return result, None, None

    return result, None, None


def tool_system_state():
    """系统状态聚合：一次返回前台+界面+播放+音频+网络+屏幕，减少 LLM 多次调用。"""
    result = {}

    pkg = get_foreground_pkg()
    result['foreground'] = {'package': pkg}
    try:
        out = adb_shell_output('dumpsys activity activities')
        m = re.search(r'(?:mResumedActivity|ResumedActivity)\s*:?\s*ActivityRecord\{[^}]*\s+[\w.]+/([\w.]+)', out)
        if m:
            result['foreground']['activity'] = m.group(1)
    except Exception:
        pass

    try:
        ui_result, ui_err, _ = tool_ui_state()
        if not ui_err and ui_result:
            result['ui_state'] = ui_result
    except Exception:
        result['ui_state'] = {'state': 'unknown'}

    try:
        play_result, play_err, _ = tool_play_state()
        if not play_err and play_result:
            result['play_state'] = play_result
    except Exception:
        result['play_state'] = {'state': 'unknown'}

    audio_info, audio_err, _ = get_audio_info()
    if not audio_err and audio_info:
        result['audio'] = audio_info

    result['screen'] = get_screen_state()
    result['network'] = get_network_state()
    result['timestamp'] = int(time.time())

    return result, None, None


__all__ = [
    'MYTV_PKG', 'UI_STATE_RULES',
    'get_foreground_pkg', 'mytv_alive',
    'get_screen_state', 'get_network_state',
    'tool_ui_state', 'tool_play_state', 'tool_system_state',
]
