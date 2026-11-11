"""TVPilot 集中配置。

所有配置常量从环境变量读取，提供默认值。
各模块从 config.py import，避免重复定义。
"""
import os
import threading

# ========== 基础配置 ==========
PORT = int(os.environ.get('TVPILOT_PORT', 8090))
TVPILOT_VERSION = os.environ.get('TVPILOT_VERSION', '0.9.0')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, '..', 'static')
CH_FILE = os.path.join(BASE_DIR, '..', 'channels.json')
DATA_DIR = os.environ.get('TVPILOT_DATA_DIR', '/app/data')

# ========== ADB ==========
ADB_HOST = os.environ.get('TVPILOT_TV_ADB', '192.168.2.238:5555')
ADB = ['adb', '-s', ADB_HOST]

# ========== 电视端应用包名 ==========
MYTV_PKG = 'com.tvcam.mytv'
MYTV_ACT = 'com.tvcam.mytv/.activities.LeanbackActivity'
IPTV_PKG = 'com.android.smart.terminal.iptv'
IPTV_ACT = 'com.amt.app.IPTVActivity'
TRIM_PKG = 'com.trim.tv'
TRIM_ACT = f'{TRIM_PKG}/.modules.splash.SplashActivity'
TRIM_TAP = (822, 412)
TRIM_DOWNS = 5
# 旧名兼容（逐步废弃）
FONGMI_TAP = TRIM_TAP
FONGMI_DOWNS = TRIM_DOWNS

# Arcface / 电视侧 8080 服务基址
ARC_BASE = os.environ.get('ARC_BASE', 'http://192.168.2.238:8080')

# ========== tvremoteime 远程输入法 ==========
TVREMOTEIME_PKG = 'com.android.tvremoteime'
TVREMOTEIME_SVC = f'{TVREMOTEIME_PKG}/.IMEService'
TVREMOTEIME_ACTION = 'ADB_INPUT_TEXT'

# ========== MQTT ==========
MQTT_HOST = os.environ.get('MQTT_HOST', '192.168.2.200')
MQTT_PORT = int(os.environ.get('MQTT_PORT', '1883'))
MQTT_USER = os.environ.get('MQTT_USER') or 'butler'
MQTT_PASS = os.environ.get('MQTT_PASS') or None
MQTT_IN = 'tv/livingroom/control'
MQTT_IN_FONGMI = 'tv/livingroom/fongmi/control'
MQTT_IN_TRIM = 'tv/livingroom/trim/control'
MQTT_OUT = 'tv/livingroom/result'

# ========== doubao2api 多模态分析 ==========
DOUBAO_API_URL = os.environ.get('DOUBAO_API_URL', 'http://192.168.2.200:9090/v1/chat/completions')
DOUBAO_API_KEY = os.environ.get('DOUBAO_API_KEY', '')
DOUBAO_MODEL = os.environ.get('DOUBAO_MODEL', 'doubao-pro')

# ========== go2rtc（归档保留） ==========
GO2RTC_BASE = os.environ.get('GO2RTC_BASE', 'http://127.0.0.1:1984')
GO2RTC_AUTH_USER = os.environ.get('GO2RTC_USER', '')
GO2RTC_AUTH_PASS = os.environ.get('GO2RTC_PASS', '')
GW_ACTIVE = GO2RTC_BASE + '/api/streams'

# ========== RTSP 刷新（归档保留） ==========
ADMIN_TOKEN = os.environ.get('TVPILOT_ADMIN_TOKEN') or None
DEVICE_TOKEN = os.environ.get('TVPILOT_DEVICE_TOKEN') or ADMIN_TOKEN
REFRESH_OPEN = os.environ.get('TVPILOT_REFRESH_OPEN', '0') == '1'
REFRESH_MIN_INTERVAL = float(os.environ.get('TVPILOT_REFRESH_MIN_INTERVAL', '120'))
DOCKER_SOCK = os.environ.get('DOCKER_SOCK', '/var/run/docker.sock')
WAKE_LOCAL = os.environ.get('TVPILOT_WAKE_LOCAL', '0') == '1'

# ========== 锁 ==========
_FG_LOCK = threading.RLock()
_DEDUP_LOCK = threading.Lock()

# ========== RTSP 刷新状态 ==========
_refresh_rtsp_state = {
    'running': False,
    'last_success': None,
    'message': '',
    'last_trigger': 0.0,
    'docker_available': os.path.exists(DOCKER_SOCK),
}

__all__ = [
    'PORT', 'TVPILOT_VERSION', 'BASE_DIR', 'STATIC_DIR', 'CH_FILE', 'DATA_DIR',
    'ADB_HOST', 'ADB',
    'MYTV_PKG', 'MYTV_ACT', 'IPTV_PKG', 'IPTV_ACT',
    'TRIM_PKG', 'TRIM_ACT', 'TRIM_TAP', 'TRIM_DOWNS',
    'FONGMI_TAP', 'FONGMI_DOWNS', 'ARC_BASE',
    'TVREMOTEIME_PKG', 'TVREMOTEIME_SVC', 'TVREMOTEIME_ACTION',
    'MQTT_HOST', 'MQTT_PORT', 'MQTT_USER', 'MQTT_PASS',
    'MQTT_IN', 'MQTT_IN_FONGMI', 'MQTT_IN_TRIM', 'MQTT_OUT',
    'DOUBAO_API_URL', 'DOUBAO_API_KEY', 'DOUBAO_MODEL',
    'GO2RTC_BASE', 'GO2RTC_AUTH_USER', 'GO2RTC_AUTH_PASS', 'GW_ACTIVE',
    'ADMIN_TOKEN', 'DEVICE_TOKEN', 'REFRESH_OPEN', 'REFRESH_MIN_INTERVAL',
    'DOCKER_SOCK', 'WAKE_LOCAL',
    '_FG_LOCK', '_DEDUP_LOCK', '_refresh_rtsp_state',
]
