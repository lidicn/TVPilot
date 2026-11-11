"""TVPilot 健康检查层。

绞杀者模式阶段11迁移：health_check 函数。
F19: 显式给出 status/degraded，探针不再说谎。
"""
import os
import json
import subprocess
import urllib.request

from tvpilot.adb import adb_ready, _CMD_LOCK
from tvpilot.state import get_foreground_pkg, mytv_alive
from tvpilot.channels import NAMES, channel_numbers_map
from tvpilot.legacy import GO2RTC_AUTH_USER, GO2RTC_AUTH_PASS, MYTV_HTTP

TVPILOT_VERSION = os.environ.get('TVPILOT_VERSION', '0.9.0')
IPTV_LOCAL_FILE = os.environ.get('TVPILOT_IPTV_SOURCE', os.path.join(
    os.environ.get('TVPILOT_DATA_DIR', '/app/data'), 'gd_iptv_source.m3u'))
DOCKER_SOCK = os.environ.get('DOCKER_SOCK', '/var/run/docker.sock')
WAKE_LOCAL = os.environ.get('TVPILOT_WAKE_LOCAL', '0') == '1'

# MQTT 连接状态（由 mqtt 模块更新；延迟 import 避免循环依赖）
_mqtt_pub = {'connected': False}


def _get_mqtt_state():
    """获取 MQTT 连接状态（优先用主文件的 _mqtt_pub，延迟 import）。"""
    try:
        from tvpilot_server import _mqtt_pub as _main_mqtt_pub
        return _main_mqtt_pub
    except Exception:
        return _mqtt_pub


def health_check():
    """全面健康检查。F19: 显式给出 status/degraded，区分"进程存活"与"外部依赖健康"。"""
    mqtt_state = _get_mqtt_state()
    result = {
        'tvpilot_version': TVPILOT_VERSION,
        'tv_online': False,
        'adb_connected': False,
        'foreground_package': None,
        'mytv_alive': False,
        'mytv_http_ok': False,
        'channels_count': len(NAMES),
        'zappable_count': len(channel_numbers_map()),
        'mqtt_connected': mqtt_state.get('connected', False),
        'iptv_source_present': os.path.exists(IPTV_LOCAL_FILE),
        'docker_sock': os.path.exists(DOCKER_SOCK),
        'go2rtc_creds_configured': bool(GO2RTC_AUTH_USER and GO2RTC_AUTH_PASS),
        'wake_local': WAKE_LOCAL,
        'problems': [],
    }
    # ADB 连接状态
    try:
        adb_ready()
        with _CMD_LOCK:
            r = subprocess.run(['adb', 'get-state'], capture_output=True, text=True, timeout=4)
        result['adb_connected'] = 'device' in (r.stdout or '')
    except Exception:
        pass
    result['tv_online'] = result['adb_connected']
    # 前台包名 / mytv 存活
    if result['tv_online']:
        result['foreground_package'] = get_foreground_pkg()
        result['mytv_alive'] = mytv_alive()
    # mytv HTTP
    try:
        r = urllib.request.urlopen(f'{MYTV_HTTP}/api/health', timeout=3)
        result['mytv_http_ok'] = True
        mytv_health = json.loads(r.read())
        if mytv_health.get('channels_loaded', 0) > 0:
            result['channels_count'] = mytv_health['channels_loaded']
    except Exception:
        pass
    # F19: 仅当核心能力受损才判 degraded
    problems = []
    if not result['tv_online'] or not result['adb_connected']:
        problems.append('tv/adb unreachable')
    if not result['iptv_source_present']:
        problems.append('iptv source m3u missing')
    result['problems'] = problems
    result['degraded'] = bool(problems)
    result['status'] = 'degraded' if problems else 'ok'
    return result


__all__ = [
    'TVPILOT_VERSION', 'IPTV_LOCAL_FILE', 'DOCKER_SOCK', 'WAKE_LOCAL',
    'health_check',
]
