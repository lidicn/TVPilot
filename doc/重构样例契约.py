# TVPilot 重构样例契约（代表性函数，非全量）
# 用途：给 mimo 理解代码风格、依赖关系、返回值约定
# 完整函数清单见 tvpilot_server.py 的 grep 结果

import os, json, time, shlex, subprocess, threading
from http.server import BaseHTTPRequestHandler

# ===== 配置样例 =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('TVPILOT_DATA_DIR', os.path.join(BASE_DIR, 'data'))
TV_IP = os.environ.get('TVPILOT_TV_IP', '192.168.2.238')
TV_PORT = int(os.environ.get('TVPILOT_TV_PORT', '5555'))
ADB = ['adb', '-s', f'{TV_IP}:{TV_PORT}']
MYTV_PKG = 'com.tvcam.mytv'
A11Y_HTTP = os.environ.get('TVPILOT_A11Y_HTTP', 'http://192.168.2.238:8081')

# ===== 错误码样例 =====
ERR_TV_UNREACHABLE = 'tv_unreachable'
ERR_APP_NOT_RESPONDING = 'app_not_responding'
ERR_INVALID_PARAMETER = 'invalid_parameter'
ERR_OPERATION_TIMEOUT = 'operation_timeout'

# ===== 统一响应（所有模块共用）=====
def make_response(tool, result=None, error=None, message=None, start_time=None, http_status=200):
    resp = {'ok': error is None, 'tool': tool}
    if result is not None:
        resp['result'] = result
    if error:
        resp['error'] = error
    if message:
        resp['message'] = message
    if start_time is not None:
        resp['cost_ms'] = int((time.time() - start_time) * 1000)
    return resp, http_status

# ===== ADB 基础样例（所有工具依赖）=====
_CMD_LOCK = threading.Lock()
_adb_state = {'connected': False, 'last_check': 0}

def adb_ready(force=False):
    """确保 ADB 连接。"""
    now = time.time()
    if not force and _adb_state['connected'] and now - _adb_state['last_check'] < 30:
        return
    r = subprocess.run(ADB + ['get-state'], capture_output=True, text=True, timeout=10)
    _adb_state['connected'] = (r.stdout.strip() == 'device')
    _adb_state['last_check'] = now

def adb_cmd(cmd, timeout=30):
    """执行 adb shell 命令，不返回输出。"""
    adb_ready()
    with _CMD_LOCK:
        subprocess.run(ADB + ['shell'] + shlex.split(cmd), capture_output=True, timeout=timeout)

def adb_shell_output(cmd, timeout=10):
    """执行 adb shell 命令并返回 stdout 文本。"""
    adb_ready()
    with _CMD_LOCK:
        r = subprocess.run(ADB + ['shell'] + shlex.split(cmd),
                           capture_output=True, text=True, timeout=timeout)
    return r.stdout or ''

# ===== 工具实现样例（返回三元组: result, error, message）=====
def tool_keyevent(key):
    """发送按键事件。key 如 'HOME'/'BACK'/'DPAD_CENTER'/'23'。"""
    valid = {'HOME', 'BACK', 'DPAD_UP', 'DPAD_DOWN', 'DPAD_LEFT', 'DPAD_RIGHT',
             'DPAD_CENTER', 'VOLUME_UP', 'VOLUME_DOWN', 'MENU', 'ENTER'}
    if not key:
        return None, ERR_INVALID_PARAMETER, 'key 不能为空'
    key_code = key if key.isdigit() else f'KEYCODE_{key}'
    try:
        adb_cmd(f'input keyevent {key_code}', timeout=10)
        return {'key': key, 'key_code': key_code}, None, None
    except Exception as e:
        return None, ERR_APP_NOT_RESPONDING, f'keyevent 失败: {e}'

def tool_zap(channel):
    """换台：归一化频道名→查台号→ADB 数字键注入。"""
    if not channel:
        return None, ERR_INVALID_PARAMETER, 'channel 不能为空'
    name = normalize_channel(channel)
    no = channel_no_of(name)
    if not no:
        return None, ERR_INVALID_PARAMETER, f'频道 "{channel}" 无台号'
    try:
        for digit in str(no):
            adb_cmd(f'input keyevent KEYCODE_{digit}', timeout=5)
            time.sleep(0.15)
        return {'channel': name, 'channel_no': no, 'method': 'adb_digits'}, None, None
    except Exception as e:
        return None, ERR_APP_NOT_RESPONDING, f'换台失败: {e}'

# ===== HTTP 路由分发样例 =====
class TVPilotHandler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)
        t0 = time.time()
        if path == '/api/health':
            result, error, msg = health_check()
            resp, code = make_response('health', result=result, error=error, message=msg, start_time=t0)
            self._json(resp, code)
            return
        if path == '/api/keyevent':
            key = q.get('key', [''])[0]
            result, error, msg = tool_keyevent(key)
            resp, code = make_response('keyevent', result=result, error=error, message=msg, start_time=t0)
            self._json(resp, code)
            return
        # ... 更多路由

# ===== 依赖关系说明（文字，非代码）=====
# tools.py 依赖: adb.py, channels.py, errors.py, config.py
# state.py 依赖: adb.py, a11y.py, errors.py
# server.py 依赖: 所有 tool 模块, health.py, make_response
# main.py 依赖: server.py, mqtt.py, config.py
# 所有 tool_* 函数返回 (result_dict, error_str|None, message_str|None)
# 所有 HTTP 路由用 make_response() 统一包装
