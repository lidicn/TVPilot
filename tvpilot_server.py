# -*- coding: utf-8 -*-
"""TVPilot — 电视设备控制服务（v0.3 场景组合动作 + 联调支撑）

定位：LLM Agent 的"手"，把 ADB/截图/换台等能力封装成标准化工具接口。

v0.3 变更：
  - P0 中文输入：tvremoteime 广播方案（ADB_INPUT_TEXT），支持中文
  - 操作存档：每次工具调用记录 JSONL（request_id 维度），供联调回放
  - GET /api/history：查询操作存档
  - 组合动作：POST /api/combo/go_home（回桌面+前台验证）

HTTP API（统一 /api/ 前缀，标准化响应 {ok,tool,result,cost_ms,error}）：
  GET  /api/screenshot?fmt=json|png&width=480   截图
  GET  /api/foreground                             当前前台包名
  POST /api/keyevent     {"key":"KEYCODE_ENTER"}  发送按键
  POST /api/tap          {"x":540,"y":180}        点击坐标
  POST /api/swipe        {"x1":..,"y1":..,"x2":..,"y2":..,"duration_ms":300}  滑动
  POST /api/input_text   {"text":"你好"}           输入文字（支持中文，tvremoteime广播）
  POST /api/launch_app   {"package":"com.xxx"}     启动 App
  POST /api/wait         {"seconds":2}              等待
  POST /api/zap          {"channel":"湖南卫视"}     mytv 换台
  POST /api/combo/go_home                          回桌面（组合动作）
  POST /api/combo/trim_search_play {"keyword":"流浪地球"}  飞牛TV搜索播放（组合动作，支持中文）
  GET  /api/current                                mytv 当前频道（代理）
  GET  /api/channels                               mytv 频道列表（代理）
  GET  /api/health                                 服务健康检查
  GET  /api/history?limit=N&request_id=xxx        操作存档查询

旧接口兼容（保留，逐步废弃）：
  GET /zap?channel=xxx  GET /keyevent?key=xxx  GET /tap?x=&y=
  GET /screenshot  GET /channels  GET /health  GET /fongmi/search

PWA：
  GET /remote              遥控器页面
  GET /remote-static/*     静态资源

MQTT：
  订阅 tv/livingroom/control  {"action":"zap","channel":"xxx"}
  发布 tv/livingroom/result   执行结果
"""
import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# ========== TVPilot 模块化包（绞杀者模式阶段1）==========
from tvpilot import make_response, _json_safe
from tvpilot.config import (
    PORT, TVPILOT_VERSION, BASE_DIR, STATIC_DIR, CH_FILE, DATA_DIR,
    ADB_HOST, ADB,
    MYTV_PKG, MYTV_ACT, IPTV_PKG, IPTV_ACT,
    TRIM_PKG, TRIM_ACT, TRIM_TAP, TRIM_DOWNS, FONGMI_TAP, FONGMI_DOWNS, ARC_BASE,
    TVREMOTEIME_PKG, TVREMOTEIME_SVC, TVREMOTEIME_ACTION,
    MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS,
    MQTT_IN, MQTT_IN_FONGMI, MQTT_IN_TRIM, MQTT_OUT,
    DOUBAO_API_URL, DOUBAO_API_KEY, DOUBAO_MODEL,
    DOCKER_SOCK, WAKE_LOCAL,
)
from tvpilot.errors import (
    ERR_TV_UNREACHABLE, ERR_APP_NOT_RESPONDING, ERR_WRONG_FOREGROUND,
    ERR_INVALID_PARAMETER, ERR_OPERATION_TIMEOUT, ERR_MYTV_API_ERROR,
    ERR_NOT_IMPLEMENTED, ERR_CHANNEL_NO_NUMBER,
)
from tvpilot.ops_log import (
    DATA_DIR, OPS_LOG, OPS_LOG_LOCK, OPS_LOG_MAX_BYTES, SHOT_KEEP,
    _local_iso_ts, _rotate_ops_if_needed, _trim_shots,
    log_operation, read_ops_history,
)
from tvpilot.adb import (
    ADB_HOST, ADB, _CMD_LOCK, _adb_state,
    adb_ready, _adb_argv, adb_cmd, adb_shell_output, tv_online,
)
from tvpilot.channels import (
    CH_FILE, CHANNEL_NUMBERS_FILE, NAMES,
    _load_names, _reload_channel_numbers,
    channel_no_of, channel_name_of_no, channel_numbers_map,
    normalize_channel, _ALIAS, _CN_NUM,
)
from tvpilot.screenshot import (
    HAS_PIL, screencap_raw, compress_image,
    tool_screenshot, tool_screenshot_smart,
)
from tvpilot.audio import (
    get_audio_info, tool_audio_get, tool_audio_set_volume,
    tool_audio_mute, tool_audio_volume_up, tool_audio_volume_down,
)
from tvpilot.a11y import (
    A11Y_HTTP, a11y_http_post, a11y_http_get,
    tool_a11y_health, tool_a11y_tree, tool_a11y_text, tool_a11y_find,
    tool_a11y_click, tool_a11y_input, tool_a11y_scroll, tool_a11y_key,
    tool_a11y_tap, tool_a11y_focus_get, tool_a11y_focus_move,
    tool_a11y_long_click, tool_a11y_wait, tool_a11y_global, tool_a11y_node,
    _UIA_DUMP_LOCK, _parse_bounds, tool_uiautomator_dump,
    tool_uiautomator_wait_text, _get_screen_texts, tool_uiautomator_tap_text,
)
from tvpilot.state import (
    MYTV_PKG, UI_STATE_RULES,
    get_foreground_pkg, mytv_alive,
    get_screen_state, get_network_state,
    tool_ui_state, tool_play_state, tool_system_state,
)
from tvpilot.tools import (
    MYTV_ACT, TVREMOTEIME_PKG, TVREMOTEIME_SVC, TVREMOTEIME_ACTION,
    ZAP_FOREGROUND_TIMEOUT, _FG_LOCK,
    ensure_mytv_foreground,
    tool_foreground, tool_keyevent, tool_tap, tool_swipe, tool_wait,
    get_current_ime, ensure_tvremoteime, tool_input_text,
    tool_combo_go_home, tool_launch_app,
    zap_via_adb_digits, tool_zap,
)
from tvpilot.health import health_check, TVPILOT_VERSION, IPTV_LOCAL_FILE, DOCKER_SOCK, WAKE_LOCAL
from tvpilot.mqtt import (
    MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS,
    MQTT_IN, MQTT_IN_FONGMI, MQTT_IN_TRIM, MQTT_OUT,
    ZAP_DEDUP_SECONDS, _mqtt_pub,
    mqtt_do_action, mqtt_worker, mqtt_loop, mqtt_publish_cmds,
)
from tvpilot.tts import TTS_CACHE_DIR, TTS_CACHE_MAX, _tts_cache_trim, edge_tts_synthesize
from tvpilot.epg import EPG_UPSTREAM, EPG_CACHE_FILE, EPG_CACHE_TTL, EPG_CHANNEL_MAP, _map_epg_channel_name, fetch_epg
from tvpilot.iptv import IPTV_UPSTREAM, IPTV_LOCAL_FILE, IPTV_DIRECT_FILE, IPTV_CACHE_FILE, IPTV_CACHE_TTL, _channel_id_map, _channel_name_map, _load_channel_id_map, get_current_channel_from_logcat, fetch_iptv_source
from tvpilot.faceid import USER_CONFIG_FILE, _current_user, FACE_DEDUP_SECONDS, load_user_profiles, save_user_profiles, apply_user_profile, handle_face_recognition
from tvpilot.voice import CHANNEL_ALIASES, match_channel, handle_wake_command
from tvpilot.vision import DOUBAO_API_URL, DOUBAO_API_KEY, DOUBAO_MODEL, tool_analyze, tool_ocr
from tvpilot.trim import TRIM_PKG, TRIM_ACT, TRIM_TAP, TRIM_DOWNS, FONGMI_TAP, FONGMI_DOWNS, trim_search, fongmi_search, tool_combo_trim_search_play
from tvpilot.logos import _logo_cache, TVPILOT_LOGO_BASE, inject_tvg_logos, generate_channel_logo
from tvpilot.routes.iptv import handle_iptv_routes, handle_channels, handle_epg_api
from tvpilot.routes.state import handle_state_routes
from tvpilot.routes.media import handle_media_get, handle_media_post
from tvpilot.routes.users import handle_users_get_all, handle_users_post
from tvpilot.routes.control import handle_control_post
from tvpilot.routes.a11y import handle_a11y_get, handle_a11y_post
from tvpilot.routes.ask import handle_ask_post
from tvpilot.routes.remote import handle_remote
from tvpilot.routes.monitor import handle_monitor_get
from tvpilot.routes.agent import handle_agent_get, handle_agent_post
from tvpilot.routes.mcp import handle_mcp_get, handle_mcp_post
from tvpilot.routes.deprecated import handle_deprecated_get
from tvpilot.monitor import check_tv_alert, check_health_alert

# ========== 路由表（绞杀者模式）==========
GET_HANDLERS = [
    handle_deprecated_get,
    handle_mcp_get,
    handle_remote,
    handle_iptv_routes,
    handle_media_get,
    handle_users_get_all,
    handle_a11y_get,
    handle_state_routes,
    handle_channels,
    handle_epg_api,
    handle_monitor_get,
    handle_agent_get,
]

POST_HANDLERS = [
    handle_mcp_post,
    handle_media_post,
    handle_users_post,
    handle_control_post,
    handle_a11y_post,
    handle_ask_post,
    handle_agent_post,
]



# 旧 RTSP 刷新链已整体隔离归档：实现与复活步骤见 archive/README.md

# 操作存档（JSONL，供联调回放与问题复盘）






# 电视打断链路自愈（保留原有功能；电视侧 8080 基址统一用 ARC_BASE，F21 消除重复常量）
# v0.4: 无障碍 HTTP 服务（Arcface App 内 A11yHttpServer，端口 8081）
A11Y_HTTP = os.environ.get('TVPILOT_A11Y_HTTP', 'http://192.168.2.238:8081')
TV_ACCESSIBILITY_SERVICES = (
    'com.xiaomi.mitv.karaoke.service/com.loostone.puremic.voice.service.accessibility.PuremicKeyService'
    ':com.xiaomi.voicecontrol/.VoiceAccessibilityService'
    ':com.example.arcfaceandroid/com.example.arcfaceandroid.TvAccessibilityService'
)
WANT_KEYWORDS = {'豆包', '管家', '豆包管家'}
# Arcface 后台保活：通过广播启动 FaceServerService（含 HTTP/人脸识别），不弹出界面
# 之前用 am start 启动 TvMainActivity 会打断用户当前使用
TV_APP_START = 'am broadcast -a com.example.arcfaceandroid.action.START_SERVICE -n com.example.arcfaceandroid/.ServiceLauncherReceiver'

# 错误码已迁移到 tvpilot/errors.py

# ========== 频道数据 ==========
# RLock：tool_input_text 等路径会在持锁后再次调用带锁的 adb_ready，需可重入（F20）


# 可选：Pillow 用于截图压缩
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# 统一响应已迁移到 tvpilot/__init__.py

# ========== v0.6 系统状态聚合 + 音频控制 + 无障碍增强 ==========

def _http_json(url, timeout=8):
    """HTTP GET JSON 工具函数（RA-01 修复：重构时丢失，自愈链路依赖）。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception:
        return None


def rearm_tv(restart_app=False):
    adb_cmd(f'settings put secure enabled_accessibility_services {TV_ACCESSIBILITY_SERVICES}')
    if restart_app:
        adb_cmd(TV_APP_START)
    mqtt_publish_cmds([
        ('tv/livingroom/cmd/xiaoai_set_keywords', {'keywords': ['豆包', '管家', '豆包管家']}),
        ('tv/livingroom/cmd/xiaoai_interrupt', {'enable': True}),
        ('tv/livingroom/cmd/xiaoai_monitor', {'enable': True}),
    ])


def heal_loop():
    while True:
        try:
            # ===== 监控告警检查（v1.0-T2）=====
            check_tv_alert()
            check_health_alert()

            # ===== mytv 保活巡检：已禁用 =====
            # mytv 不需要后台保活，仅在语音换台/管家调用时通过 tool_zap -> ensure_mytv_foreground 唤起
            # 之前的保活逻辑会导致用户用其他应用时 mytv 被误拉到前台

            # ===== TV 打断链路巡检 =====
            st = _http_json(ARC_BASE + '/api/xiaoai/keywords')
            bad = False
            restart_app = False
            if st is None:
                bad, restart_app = True, True
            else:
                if st.get('error') == 'accessibility_service_not_connected':
                    bad = True
                if not st.get('enabled'):
                    bad = True
                if not WANT_KEYWORDS.issubset(set(st.get('keywords') or [])):
                    bad = True
            if bad:
                print(f'[自愈] TV 打断链路异常，执行恢复', flush=True)
                rearm_tv(restart_app=restart_app)
                time.sleep(30)
        except Exception as e:
            print(f'[自愈] 巡检异常: {e}', flush=True)
        time.sleep(60)


# ========== HTTP 服务器 ==========
MIME_MAP = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
            '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
            '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml', '.ico': 'image/x-icon'}


class TVPilotHandler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_png(self, png_bytes):
        self.send_response(200)
        self.send_header('Content-Type', 'image/png')
        self.send_header('Content-Length', str(len(png_bytes)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(png_bytes)

    def _serve_static(self, rel_path, cache=False):
        # 防路径遍历：拒绝绝对路径 + realpath 必须在 STATIC_DIR 内
        if rel_path.startswith('/') or '\x00' in rel_path:
            self._json({'ok': False, 'error': 'invalid path'}, 400)
            return
        fp = os.path.realpath(os.path.join(STATIC_DIR, rel_path))
        base = os.path.realpath(STATIC_DIR) + os.sep
        if not fp.startswith(base):
            self._json({'ok': False, 'error': 'invalid path'}, 400)
            return
        if not os.path.isfile(fp):
            self._json({'ok': False, 'error': 'not found: ' + rel_path}, 404)
            return
        ext = os.path.splitext(fp)[1].lower()
        ctype = MIME_MAP.get(ext, 'application/octet-stream')
        with open(fp, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        if cache:
            self.send_header('Cache-Control', 'public, max-age=86400')
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        MAX_BODY = 10 * 1024 * 1024  # 10MB 上限，防 OOM
        try:
            length = int(self.headers.get('Content-Length', 0))
        except (ValueError, TypeError):
            length = 0
        if length < 0:
            length = 0
        if length > MAX_BODY:
            self._json({'ok': False, 'error': 'payload too large', 'max_bytes': MAX_BODY}, 413)
            return None
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        self._raw_body = raw  # 保存原始 bytes，供代理分支复用（RA-02 修复）
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception as _e:
            print(f'[http] body JSON 解码失败 len={length}: {_e}', flush=True)
            return {}

    def _handle_tool(self, tool_name, func, request_id=None, save_screenshot=False, **kwargs):
        """统一工具处理：调用函数 → 包装标准化响应 → 操作存档 → 返回。"""
        t0 = time.time()
        fg_before = None
        try:
            fg_before = get_foreground_pkg()
        except Exception:
            pass
        try:
            result, error, msg = func(**kwargs)
        except subprocess.TimeoutExpired:
            result, error, msg = None, ERR_OPERATION_TIMEOUT, '操作超时'
        except Exception as e:
            result, error, msg = None, ERR_TV_UNREACHABLE, f'执行异常: {e}'
        cost_ms = int((time.time() - t0) * 1000)
        resp, code = make_response(tool_name, result=result, error=error,
                                    message=msg, start_time=t0)
        # 操作存档
        fg_after = None
        try:
            fg_after = get_foreground_pkg()
        except Exception:
            pass
        screenshot_path = None
        if save_screenshot and error:
            # 失败时自动截图存档
            try:
                png = screencap_raw()
                if png:
                    ts = int(time.time() * 1000)
                    shot_path = os.path.join(DATA_DIR, f'shot_{tool_name}_{ts}.png')
                    with open(shot_path, 'wb') as f:
                        f.write(png)
                    screenshot_path = shot_path
                    _trim_shots()  # F18: 仅保留最新 N 张失败截图
            except Exception:
                pass
        log_operation(tool_name, kwargs, error is None, error, cost_ms,
                      request_id=request_id, foreground_before=fg_before,
                      foreground_after=fg_after, screenshot_path=screenshot_path)
        self._json(resp, code)

    # ----- GET -----
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path
        try:
            # 遍历 GET 路由表
            for handler_fn in GET_HANDLERS:
                if handler_fn(self, path, q):
                    return










            self._json({'ok': False, 'error': 'not found', 'path': path}, 404)
        except Exception as e:
            self._json({'ok': False, 'error': repr(e)}, 500)

    # ----- POST -----
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        try:
            body = self._read_body()
            if body is None:
                return

            # 遍历 POST 路由表
            for handler_fn in POST_HANDLERS:
                if handler_fn(self, path, body):
                    return

            if path.startswith('/api/') and not path.startswith('/api/a11y/') and not path.startswith('/api/audio') and path not in (
                '/api/screenshot', '/api/foreground', '/api/current',
                '/api/channels', '/api/health', '/api/zap', '/api/keyevent',
                '/api/tap', '/api/swipe', '/api/input_text', '/api/launch_app',
                '/api/wait', '/api/combo/go_home', '/api/combo/trim_search_play', '/api/history',
                '/api/system_state', '/api/ui_state', '/api/play_state', '/api/ocr', '/api/screenshot_smart',
                '/api/users', '/api/users/switch',
                '/api/iptv/refresh_rtsp',
                '/api/face/backup', '/api/face/restore',
                '/api/ask/listen', '/api/ask/stop', '/api/ask/beep'
            ):
                # 旧 mytv HTTP 代理兜底已随归档链路摘除：明确返 410，不静默 500 / 假 404
                self._json({'ok': False, 'error': 'deprecated',
                            'see': 'archive/README.md'}, 410)
                return

            req_id = body.get('request_id')
            save_shot = body.get('save_screenshot', False)



            # ===== RTSP 自动刷新（已归档隔离）：调用端点保留，明确返 410 Gone =====
            if path == '/api/iptv/refresh_rtsp':
                self._json({'ok': False, 'error': 'deprecated',
                            'see': 'archive/README.md'}, 410)
                return



            self._json({'ok': False, 'error': 'not found', 'path': path}, 404)
        except Exception as e:
            self._json({'ok': False, 'error': repr(e)}, 500)

    def log_message(self, fmt, *args):
        sys.stdout.write('[%s] %s\n' % (time.strftime('%H:%M:%S'), fmt % args))
        sys.stdout.flush()


# ========== 主入口 ==========
if __name__ == '__main__':
    threading.Thread(target=mqtt_loop, daemon=True).start()
    threading.Thread(target=heal_loop, daemon=True).start()
    print(f'TVPilot v{TVPILOT_VERSION} 启动: http://0.0.0.0:{PORT}  (频道数 {len(NAMES)}, Pillow={HAS_PIL}, ops_log={OPS_LOG}, wake_local={WAKE_LOCAL}, docker_sock={os.path.exists(DOCKER_SOCK)})', flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), TVPilotHandler).serve_forever()
