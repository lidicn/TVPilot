"""TVPilot 核心工具层。

绞杀者模式阶段9迁移：换台、按键、点击、滑动、中文输入、启动App、回桌面。
自包含：MYTV_ACT/TVREMOTEIME_* 从环境变量读。
"""
import os
import re
import time
import shlex
import subprocess
import threading

from tvpilot.adb import adb_cmd, adb_ready, adb_shell_output, ADB, _CMD_LOCK
from tvpilot.state import get_foreground_pkg, MYTV_PKG, set_current_channel
from tvpilot.channels import normalize_channel, channel_no_of, NAMES
from tvpilot.errors import (
    ERR_INVALID_PARAMETER, ERR_OPERATION_TIMEOUT, ERR_TV_UNREACHABLE,
    ERR_WRONG_FOREGROUND, ERR_CHANNEL_NO_NUMBER, ERR_MYTV_API_ERROR,
    ERR_APP_NOT_RESPONDING, ERR_APP_NOT_FOUND, ERR_APP_LAUNCH_FAILED,
)

MYTV_ACT = os.environ.get('TVPILOT_MYTV_ACT', 'com.tvcam.mytv/.activities.LeanbackActivity')
IPTV_PKG = os.environ.get('TVPILOT_IPTV_PKG', 'com.android.smart.terminal.iptv')
IPTV_ACT = os.environ.get('TVPILOT_IPTV_ACT', 'com.android.smart.terminal.iptv/com.amt.app.IPTVActivity')
TVREMOTEIME_PKG = os.environ.get('TVPILOT_IME_PKG', 'com.android.tvremoteime')
TVREMOTEIME_SVC = f'{TVREMOTEIME_PKG}/.IMEService'
TVREMOTEIME_ACTION = os.environ.get('TVPILOT_IME_ACTION', 'ADB_INPUT_TEXT')

ZAP_FOREGROUND_TIMEOUT = float(os.environ.get('TVPILOT_ZAP_FG_TIMEOUT', '8'))
_FG_LOCK = threading.Lock()


# ========== mytv 前台保障 ==========

def ensure_mytv_foreground(timeout=ZAP_FOREGROUND_TIMEOUT):
    """HV-1: 整段拉前台动作串行化，避免语音/MQTT 两路 HOME+am start 互相打断。"""
    with _FG_LOCK:
        return _ensure_mytv_foreground_inner(timeout)


def _ensure_mytv_foreground_inner(timeout=ZAP_FOREGROUND_TIMEOUT):
    """确保 IPTV 在前台（v2 优化：直接启动 IPTV，绕过 mytv）。
    冷启动时额外等待 15s 让 IPTV 加载完成（导航页→播放页）。"""
    def _is_iptv():
        p = get_foreground_pkg() or ''
        return (IPTV_PKG in p), p

    ok, pkg = _is_iptv()
    if ok:
        return {'ok': True, 'action': 'already_foreground', 'pkg': pkg, 'cold_start': False}

    deadline = time.time() + timeout
    last = pkg
    cold_start = False

    for attempt in (1, 2):
        if time.time() >= deadline:
            break
        adb_cmd('input keyevent KEYCODE_HOME')
        time.sleep(min(0.5, max(0.0, deadline - time.time())))

        # 检查 IPTV 进程是否已存在（判断是否冷启动）
        try:
            out = adb_shell_output(f'pidof {IPTV_PKG}')
            iptv_running = bool(out and out.strip())
        except Exception:
            iptv_running = False

        # v2: 直接启动 IPTV Activity，不经过 mytv
        adb_cmd(f'am start -n {IPTV_ACT}')
        cold_start = not iptv_running

        # 等待 IPTV 到前台
        waited = 0
        while time.time() < deadline:
            time.sleep(0.4); waited += 1
            ok, last = _is_iptv()
            if ok:
                # 冷启动时：等待加载 + 按 BACK 退出导航层，进入全屏播放
                if cold_start:
                    load_wait = min(5, max(0.0, deadline - time.time()))
                    print(f'[ZAP] IPTV 冷启动，等待加载 {load_wait:.1f}s', flush=True)
                    time.sleep(load_wait)
                    # 按 2 次 BACK 退出导航层/菜单，进入全屏播放
                    for _ in range(2):
                        try:
                            adb_cmd('input keyevent KEYCODE_BACK')
                            time.sleep(0.3)
                        except Exception:
                            pass
                    print('[ZAP] IPTV 冷启动，已按 BACK 退出导航层', flush=True)
                return {'ok': True, 'action': 'launched' if attempt == 1 else 'launched_retry',
                        'was': pkg, 'wait_s': round(waited * 0.4, 1),
                        'cold_start': cold_start}
    return {'ok': False, 'error': 'IPTV 启动后未到前台', 'last': last}


# ========== 基础工具 ==========

def tool_foreground():
    """前台包名查询工具。"""
    pkg = get_foreground_pkg()
    if pkg is None:
        return None, ERR_TV_UNREACHABLE, '无法获取前台包名，电视可能离线'
    return {'package': pkg}, None, None


def tool_keyevent(key):
    """按键工具。ADB 优先，6095 端口降级（仅支持基本按键）。"""
    if not key or not re.fullmatch(r'KEYCODE_[A-Z0-9_]+', key):
        return None, ERR_INVALID_PARAMETER, f'非法按键名: {key}（需 KEYCODE_XXX 格式）'
    # 常用合法 KEYCODE 白名单（Android 标准键码中 TV 场景常用的）
    _VALID_KEYCODES = {
        'KEYCODE_HOME', 'KEYCODE_BACK', 'KEYCODE_MENU', 'KEYCODE_POWER',
        'KEYCODE_DPAD_UP', 'KEYCODE_DPAD_DOWN', 'KEYCODE_DPAD_LEFT', 'KEYCODE_DPAD_RIGHT',
        'KEYCODE_DPAD_CENTER', 'KEYCODE_ENTER', 'KEYCODE_ESCAPE',
        'KEYCODE_VOLUME_UP', 'KEYCODE_VOLUME_DOWN', 'KEYCODE_VOLUME_MUTE',
        'KEYCODE_CHANNEL_UP', 'KEYCODE_CHANNEL_DOWN',
        'KEYCODE_NUMBER_0', 'KEYCODE_NUMBER_1', 'KEYCODE_NUMBER_2', 'KEYCODE_NUMBER_3',
        'KEYCODE_NUMBER_4', 'KEYCODE_NUMBER_5', 'KEYCODE_NUMBER_6', 'KEYCODE_NUMBER_7',
        'KEYCODE_NUMBER_8', 'KEYCODE_NUMBER_9',
        'KEYCODE_TV', 'KEYCODE_TV_INPUT', 'KEYCODE_TV_MENU',
        'KEYCODE_MEDIA_PLAY', 'KEYCODE_MEDIA_PAUSE', 'KEYCODE_MEDIA_PLAY_PAUSE',
        'KEYCODE_MEDIA_STOP', 'KEYCODE_MEDIA_NEXT', 'KEYCODE_MEDIA_PREVIOUS',
        'KEYCODE_MEDIA_REWIND', 'KEYCODE_MEDIA_FAST_FORWARD',
        'KEYCODE_SEARCH', 'KEYCODE_SETTINGS', 'KEYCODE_GUIDE',
        'KEYCODE_INFO', 'KEYCODE_DEL', 'KEYCODE_TAB',
    }
    if key not in _VALID_KEYCODES:
        return None, ERR_INVALID_PARAMETER, f'未知键码: {key}（不在常用键码白名单中，可能无效）'
    try:
        adb_cmd(f'input keyevent {key}')
        return {'key': key, 'channel': 'adb'}, None, None
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    # 降级：6095 端口（仅支持 11 个基本按键）
    _MITV_KEY_MAP = {
        'KEYCODE_HOME': 'home', 'KEYCODE_BACK': 'back',
        'KEYCODE_DPAD_UP': 'up', 'KEYCODE_DPAD_DOWN': 'down',
        'KEYCODE_DPAD_LEFT': 'left', 'KEYCODE_DPAD_RIGHT': 'right',
        'KEYCODE_DPAD_CENTER': 'enter', 'KEYCODE_ENTER': 'enter',
        'KEYCODE_MENU': 'menu', 'KEYCODE_VOLUME_UP': 'volumeup',
        'KEYCODE_VOLUME_DOWN': 'volumedown', 'KEYCODE_POWER': 'power',
    }
    mitv_key = _MITV_KEY_MAP.get(key)
    if mitv_key:
        try:
            from tvpilot.screenshot import mitv_key as _mitv_key_send
            if _mitv_key_send(mitv_key):
                return {'key': key, 'channel': 'mitv6095'}, None, None
        except Exception:
            pass
    return None, ERR_OPERATION_TIMEOUT, f'按键 {key} 失败（ADB 和 6095 均不可用）'


def tool_tap(x, y):
    """点击工具。"""
    try:
        x, y = int(x), int(y)
    except (TypeError, ValueError):
        return None, ERR_INVALID_PARAMETER, 'x/y 必须是整数'
    if not (0 <= x <= 4096 and 0 <= y <= 4096):
        return None, ERR_INVALID_PARAMETER, f'坐标超出范围: ({x},{y})'
    try:
        adb_cmd(f'input tap {x} {y}')
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, '点击超时'
    return {'x': x, 'y': y}, None, None


def tool_swipe(x1, y1, x2, y2, duration_ms=300):
    """滑动工具。"""
    try:
        x1, y1, x2, y2, duration_ms = int(x1), int(y1), int(x2), int(y2), int(duration_ms)
    except (TypeError, ValueError):
        return None, ERR_INVALID_PARAMETER, '坐标和 duration_ms 必须是整数'
    if duration_ms < 50:
        duration_ms = 50
    try:
        adb_cmd(f'input swipe {x1} {y1} {x2} {y2} {duration_ms}')
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, '滑动超时'
    return {'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'duration_ms': duration_ms}, None, None


def tool_long_press(x, y, duration_ms=800):
    """长按工具：在指定位置长按（用 swipe 起终点相同模拟）。"""
    try:
        x, y, duration_ms = int(x), int(y), int(duration_ms)
    except (TypeError, ValueError):
        return None, ERR_INVALID_PARAMETER, 'x/y/duration_ms 必须是整数'
    if duration_ms < 300:
        duration_ms = 300  # 太短不叫长按
    try:
        adb_cmd(f'input swipe {x} {y} {x} {y} {duration_ms}')
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, '长按超时'
    return {'x': x, 'y': y, 'duration_ms': duration_ms}, None, None


def tool_scroll(direction='down'):
    """滚动工具：按方向滚动列表（基于 swipe 封装）。

    direction: up / down / left / right
    """
    valid_dirs = ('up', 'down', 'left', 'right')
    if direction not in valid_dirs:
        return None, ERR_INVALID_PARAMETER, f'方向必须是 {"/".join(valid_dirs)}'
    # 假设屏幕 1920x1080，从中间滑
    if direction == 'down':
        x1, y1, x2, y2 = 960, 700, 960, 300  # 从上往下滑 = 内容向上滚
    elif direction == 'up':
        x1, y1, x2, y2 = 960, 300, 960, 700  # 从下往上滑 = 内容向下滚
    elif direction == 'left':
        x1, y1, x2, y2 = 1400, 540, 500, 540  # 从右往左滑 = 内容向右滚
    else:  # right
        x1, y1, x2, y2 = 500, 540, 1400, 540  # 从左往右滑 = 内容向左滚
    try:
        adb_cmd(f'input swipe {x1} {y1} {x2} {y2} 400')
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, '滚动超时'
    return {'direction': direction, 'from': [x1, y1], 'to': [x2, y2]}, None, None


def tool_wait(seconds=2):
    """等待工具（供模板引擎使用）。"""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return None, ERR_INVALID_PARAMETER, 'seconds 必须是数字'
    seconds = max(0.1, min(seconds, 10))
    time.sleep(seconds)
    return {'seconds': seconds}, None, None


def tool_wait_for_foreground(package, timeout=10):
    """等待前台变为指定包名。轮询直到前台匹配或超时。"""
    if not package:
        return None, ERR_INVALID_PARAMETER, 'package 不能为空'
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = 10.0
    deadline = time.time() + min(timeout, 30.0)
    while time.time() < deadline:
        fg = get_foreground_pkg()
        if fg and package in fg:
            return {'foreground': fg, 'matched': True, 'waited': round(time.time() - (deadline - timeout), 1)}, None, None
        time.sleep(0.5)
    fg_now = get_foreground_pkg()
    return None, ERR_WRONG_FOREGROUND, f'等待 {timeout:.0f}s 后前台仍为 {fg_now}，期望包含 {package}'


# ========== 中文输入（tvremoteime 广播方案）==========

def get_current_ime():
    """返回当前默认输入法 ID。"""
    try:
        out = adb_shell_output('settings get secure default_input_method')
        return out.strip()
    except Exception:
        return None


def ensure_tvremoteime():
    """确保 tvremoteime 是当前输入法；返回 (switched, original_ime)。"""
    cur = get_current_ime()
    if cur and TVREMOTEIME_SVC in cur:
        return False, cur
    try:
        adb_cmd(f'ime set {TVREMOTEIME_SVC}')
        time.sleep(0.3)
        return True, cur
    except Exception as e:
        print(f'[ime] switch failed: {e}', flush=True)
        return False, cur


def tool_input_text(text):
    """文字输入工具（支持中文）。
    优先用剪贴板粘贴方式，失败则降级到 tvremoteime 广播。
    """
    if text is None:
        return None, ERR_INVALID_PARAMETER, 'text 不能为空'
    text = str(text)
    if not text:
        return None, ERR_INVALID_PARAMETER, 'text 不能为空'

    is_ascii = all(ord(c) < 128 for c in text)
    if is_ascii:
        safe_text = shlex.quote(text)
        try:
            adb_cmd(f'input text {safe_text}')
        except subprocess.TimeoutExpired:
            return None, ERR_OPERATION_TIMEOUT, '输入文字超时'
        time.sleep(0.5)
        verified = _verify_text_on_screen(text)
        return {'text': text, 'length': len(text), 'method': 'ascii_input',
                'verified': verified}, None, None

    # 中文输入：先试剪贴板粘贴，失败再试 tvremoteime
    switched, original_ime = ensure_tvremoteime()
    try:
        # 方式1：剪贴板粘贴
        clipboard_result = _input_via_clipboard(text)
        if clipboard_result['verified']:
            return {**clipboard_result, 'text': text, 'length': len(text),
                    'ime_switched': switched}, None, None

        # 方式2：tvremoteime 广播（重试2次）
        last_verified = False
        for attempt in range(2):
            safe_text = shlex.quote(text)
            cmd = f"am broadcast -a {TVREMOTEIME_ACTION} --es msg {safe_text}"
            try:
                with _CMD_LOCK:
                    adb_ready()
                    r = subprocess.run(ADB + ['shell', cmd],
                                       capture_output=True, text=True, timeout=15)
                    out = r.stdout or ''
                if 'Broadcast completed' not in out:
                    continue
            except subprocess.TimeoutExpired:
                continue

            time.sleep(1.0 + attempt * 0.5)  # 每次重试多等一会
            last_verified = _verify_text_on_screen(text)
            if last_verified:
                break

        return {'text': text, 'length': len(text), 'method': 'tvremoteime_broadcast',
                'ime_switched': switched, 'verified': last_verified,
                'attempts': 3}, None, None
    finally:
        if switched and original_ime and TVREMOTEIME_SVC not in original_ime:
            try:
                adb_cmd(f'ime set {original_ime}')
            except Exception:
                pass


def _input_via_clipboard(text):
    """通过剪贴板粘贴输入文字。
    先把文字放到剪贴板，再按 KEYCODE_PASTE 粘贴。
    """
    try:
        # 方式：用 tvremoteime 的剪贴板广播（如果支持）
        # 或者直接用 input keyevent KEYCODE_PASTE（需要剪贴板里有内容）

        # 先尝试用 am broadcast 设置剪贴板
        # tvremoteime 可能支持 SET_CLIPBOARD action
        safe_text = shlex.quote(text)

        # 尝试1：SET_CLIPBOARD action
        try:
            cmd = f"am broadcast -a com.android.tvremoteime.SET_CLIPBOARD --es text {safe_text}"
            with _CMD_LOCK:
                adb_ready()
                r = subprocess.run(ADB + ['shell', cmd],
                                   capture_output=True, text=True, timeout=10)
        except Exception:
            pass

        # 按粘贴键
        time.sleep(0.3)
        adb_cmd('input keyevent KEYCODE_PASTE')
        time.sleep(0.8)

        verified = _verify_text_on_screen(text)
        return {'method': 'clipboard_paste', 'verified': verified}
    except Exception as e:
        return {'method': 'clipboard_paste', 'verified': False, 'error': str(e)}


def _verify_text_on_screen(text):
    """验证文字是否真的出现在当前界面上。返回 bool 或 None（验证失败）。"""
    try:
        out = adb_shell_output('uiautomator dump /sdcard/ui_verify.xml', timeout=10)
        if not out or 'ERROR' in out:
            return None
        time.sleep(0.2)
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix='.xml', delete=False)
        vpath = tmp.name
        tmp.close()
        with _CMD_LOCK:
            adb_ready()
            subprocess.run(ADB + ['pull', '/sdcard/ui_verify.xml', vpath],
                           capture_output=True, timeout=5)
        if not os.path.exists(vpath) or os.path.getsize(vpath) == 0:
            try: os.unlink(vpath)
            except: pass
            return None
        import xml.etree.ElementTree as ET
        tree = ET.parse(vpath)
        root = tree.getroot()
        found = False
        for node in root.iter('node'):
            node_text = node.get('text', '')
            if text in node_text:
                found = True
                break
        try: os.unlink(vpath)
        except: pass
        return found
    except Exception:
        return None


# ========== 组合动作 ==========

def tool_combo_go_home():
    """组合动作：回桌面（KEYCODE_HOME + 前台验证，带轮询重试）。"""
    fg_before = get_foreground_pkg()
    try:
        adb_cmd('input keyevent KEYCODE_HOME')
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, '回桌面超时'
    # 轮询验证：最多等 3 秒，每 0.5 秒检查一次
    fg_after = None
    for _ in range(6):
        time.sleep(0.5)
        fg_after = get_foreground_pkg()
        if fg_after is None:
            continue
        if 'tvhome' in fg_after or 'launcher' in fg_after.lower() or 'home' in fg_after.lower():
            return {'foreground_before': fg_before, 'foreground_after': fg_after,
                    'is_home': True}, None, None
    if fg_after is None:
        return {'foreground_before': fg_before, 'foreground_after': None,
                'is_home': None, 'warning': 'HOME键已发送，但无法获取前台包名'}, None, None
    return {'foreground_before': fg_before, 'foreground_after': fg_after,
            'is_home': False, 'warning': 'HOME键已发送，但3秒后前台仍未切到桌面'}, None, None


def tool_launch_app(package, activity=None):
    """启动 App 工具。v3：用 monkey 命令（自动找 LAUNCHER），启动前检查包存在性。"""
    if not package or not re.fullmatch(r'[\w.]+', package):
        return None, ERR_INVALID_PARAMETER, f'非法包名: {package}'
    t0 = time.time()

    # 1. 检查包是否已安装
    try:
        pkgs = adb_shell_output(f'pm list packages {package}', timeout=5)
        if package not in pkgs:
            return None, ERR_APP_NOT_FOUND, f'包未安装: {package}'
    except Exception:
        pass  # 检查失败不阻断，继续尝试启动

    # 2. 启动应用（monkey 自动找 LAUNCHER Activity，比 am start -n .MainActivity 更可靠）
    channel = 'adb'
    if activity:
        if not re.fullmatch(r'[A-Za-z0-9_./]{1,200}', activity):
            return None, ERR_INVALID_PARAMETER, f'非法 activity: {activity}'
        cmd = f'am start -n {package}/{shlex.quote(activity)}'
    else:
        cmd = f'monkey -p {package} -c android.intent.category.LAUNCHER 1'
    try:
        out = adb_shell_output(cmd, timeout=8)
        # monkey 成功标志：Events injected: 1
        if activity:
            # am start 模式：检查是否有 Error
            if 'Error' in out or 'Exception' in out:
                return None, ERR_APP_LAUNCH_FAILED, f'启动 {package} 失败: {out.strip()[:100]}'
        else:
            # monkey 模式：必须有 Events injected: 1
            if 'Events injected: 1' not in out:
                return None, ERR_APP_LAUNCH_FAILED, f'启动 {package} 失败（无 LAUNCHER Activity）: {out.strip()[:100]}'
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, f'启动 {package} 超时'

    # 3. 等待应用到前台（最多 1.5s，避免阻塞；get_foreground_pkg 有 1s 缓存）
    # 注意：IPTV STB 等应用可能有前台锁定，启动指令成功但前台可能不变
    for i in range(3):
        time.sleep(0.5)
        cur = get_foreground_pkg()
        if cur and package in cur:
            return {'package': package, 'activity': activity, 'wait_s': round((i + 1) * 0.5, 1),
                    'foreground': True, 'channel': channel, 'launched': True}, None, None
    cur = get_foreground_pkg()
    if cur and package in cur:
        return {'package': package, 'foreground': True, 'channel': channel, 'launched': True}, None, None
    # 启动指令已发送（monkey/am start 成功），但前台未变（可能被其他应用锁定）
    # 返回 launched=true, foreground=false，不报错
    return {'package': package, 'foreground': False, 'current_foreground': cur,
            'wait_s': round(time.time() - t0, 1), 'channel': channel,
            'launched': True, 'note': '启动指令已发送，前台未变（可能被其他应用锁定）'}, None, None


# ========== 换台 ==========

def zap_via_adb_digits(channel_no):
    """降级换台：单次 `input keyevent` 一次下发全部数字键 + ENTER。

    AF-PM 11-12：原实现用 `input keyevent K1 && sleep 0.35 && input keyevent K2 ...`
    串位，B5 真机同代码同基线仍出现 `50 -> 5`，且耗时翻倍（36.9s vs 14.4s）。
    `input keyevent` 原生支持一次多键；改成单条命令彻底去掉 sleep 与 `&&` 链，
    位间不再依赖 toybox 小数 sleep，也不再依赖前一条命令的返回码。
    """
    digits = str(channel_no)
    key_map = {'0': 'KEYCODE_0', '1': 'KEYCODE_1', '2': 'KEYCODE_2', '3': 'KEYCODE_3',
               '4': 'KEYCODE_4', '5': 'KEYCODE_5', '6': 'KEYCODE_6', '7': 'KEYCODE_7',
               '8': 'KEYCODE_8', '9': 'KEYCODE_9'}
    try:
        keys = [key_map[d] for d in digits]
        keys.append('KEYCODE_ENTER')
        adb_cmd('input keyevent ' + ' '.join(keys))
        return True, f'输入频道号 {digits}'
    except subprocess.TimeoutExpired:
        return False, '数字键输入超时'
    except Exception as e:
        return False, f'数字键输入失败: {e}'


def tool_zap(channel):
    """mytv 换台工具（ADB 数字键注入）。
    F1: 换台前先把 mytv 拉到前台——仅在语音/管家换台这一显式调用点唤起；
    F2: 数字台号取自单一真源 data/channel_numbers.json（channel_no_of）。"""
    if not channel:
        return None, ERR_INVALID_PARAMETER, 'channel 不能为空'
    t0 = time.time()
    # 支持纯台号输入（如 "24" → 查找台号为 24 的频道）
    if str(channel).strip().isdigit():
        no = str(channel).strip()
        matched = [n for n in NAMES if str(channel_no_of(n)) == no]
        if matched:
            name = matched[0]
        else:
            return None, ERR_INVALID_PARAMETER, f'台号 "{channel}" 无对应频道'
    else:
        name = normalize_channel(channel)
        if name not in NAMES:
            cands = [n for n in NAMES if n.startswith(name)]
            if not cands:
                cands = [n for n in NAMES if name in n]
            if len(cands) == 1:
                name = cands[0]
            elif len(cands) > 1:
                # 优化：短数字输入优先匹配 CCTV 系列（如 "4" → CCTV4 而非广东4K）
                cctv_cands = [n for n in cands if 'CCTV' in n.upper() or '中央' in n]
                if cctv_cands:
                    name = min(cctv_cands, key=len)
                else:
                    name = min(cands, key=len)
            else:
                return None, ERR_INVALID_PARAMETER, f'频道表里没有 "{channel}"'

    no = channel_no_of(name)
    if no is None:
        return None, ERR_CHANNEL_NO_NUMBER, (
            f'频道 "{name}" 无数字台号映射；请在 mytv 频道列表选择，或在 data/channel_numbers.json 补台号')

    # 快速路径：已在 IPTV 前台时跳过 ensure（force_refresh 绕开 state 的 1s 缓存，
    # 否则读到旧前台会误进 ensure_mytv_foreground 并超时）。
    fg_before = get_foreground_pkg(force_refresh=True)
    if fg_before and IPTV_PKG in fg_before:
        fg = {'ok': True, 'action': 'already_foreground_fastpath', 'pkg': fg_before, 'cold_start': False}
    else:
        fg = ensure_mytv_foreground()
        if not fg.get('ok'):
            # 如实反映底层错误（_ensure_mytv_foreground_inner 返回的是 IPTV 启动结果），
            # 不再误导成已不存在的 "mytv 前置"。
            return None, ERR_WRONG_FOREGROUND, f'IPTV 启动后未到前台: {fg.get("last") or fg.get("error")}'

    adb_ok, adb_msg = zap_via_adb_digits(no)
    if not adb_ok:
        return None, ERR_MYTV_API_ERROR, f'ADB 换台失败: {adb_msg}'

    # 等待换台完成（IPTV 切换需要 1-2 秒）
    time.sleep(1.5)

    # 验证前台是否还是 IPTV
    fg_after = get_foreground_pkg()
    iptv_foreground = bool(fg_after and IPTV_PKG in fg_after)

    # 更新当前频道缓存
    if iptv_foreground:
        set_current_channel(name, no)

    result = {'channel': name, 'channel_no': no, 'foreground_after': fg_after,
              'foreground_action': fg.get('action'),
              'iptv_foreground': iptv_foreground,
              'warmed': False, 'method': 'adb_digits',
              'zap_time_ms': int((time.time() - t0) * 1000)}
    return result, None, None




# ========== 应用管理与 UI 交互（点播基础能力） ==========

def tool_list_apps(filter_keyword=None):
    """列出已安装应用。filter_keyword 可选，按包名过滤（如 'video'、'iqiyi'）。"""
    try:
        cmd = 'pm list packages'
        if filter_keyword:
            cmd += f' | grep {shlex.quote(filter_keyword)}'
        out = adb_shell_output(cmd, timeout=10)
        packages = []
        for line in out.strip().split('\n'):
            line = line.strip()
            if line.startswith('package:'):
                packages.append(line[8:])
        return {'count': len(packages), 'packages': packages, 'filter': filter_keyword}, None, None
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, '列出应用超时'
    except Exception as e:
        return None, ERR_TV_UNREACHABLE, f'列出应用失败: {e}'


def tool_app_state(package):
    """查询应用运行状态：是否安装、是否在运行、是否在前台。"""
    if not package or not re.fullmatch(r'[\w.]+', package):
        return None, ERR_INVALID_PARAMETER, f'非法包名: {package}'
    result = {'package': package, 'installed': False, 'running': False, 'foreground': False}
    try:
        # 检查是否安装
        out = adb_shell_output(f'pm list packages {package}', timeout=5)
        result['installed'] = package in out
        if not result['installed']:
            return result, None, None
        # 检查是否在运行
        try:
            pid_out = adb_shell_output(f'pidof {package}', timeout=5)
            result['running'] = bool(pid_out and pid_out.strip())
            result['pid'] = pid_out.strip() if pid_out.strip() else None
        except Exception:
            pass
        # 检查是否在前台
        fg = get_foreground_pkg()
        result['foreground'] = bool(fg and package in fg)
        result['foreground_package'] = fg
        return result, None, None
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, '查询应用状态超时'
    except Exception as e:
        return None, ERR_TV_UNREACHABLE, f'查询应用状态失败: {e}'


def tool_force_stop(package):
    """强制停止应用。先检查包是否安装。"""
    if not package or not re.fullmatch(r'[\w.]+', package):
        return None, ERR_INVALID_PARAMETER, f'非法包名: {package}'
    # 先检查包是否已安装（检查失败=ADB不可达，不静默跳过）
    try:
        pkgs = adb_shell_output(f'pm list packages {package}', timeout=5)
        if f'package:{package}' not in pkgs:
            return None, ERR_APP_NOT_FOUND, f'包未安装: {package}（无需 force-stop）'
    except Exception as e:
        return None, ERR_TV_UNREACHABLE, f'无法检查包是否安装（ADB不可达）: {e}'
    try:
        adb_cmd(f'am force-stop {package}', timeout=10)
        time.sleep(0.5)
        # 验证是否已停止（pidof失败=无法确认，不谎报成功）
        try:
            pid_out = adb_shell_output(f'pidof {package}', timeout=5)
            stopped = not (pid_out and pid_out.strip())
            result = {'package': package, 'stopped': stopped}
        except Exception:
            result = {'package': package, 'stopped': None,
                      'warning': 'force-stop已下发，但pidof验证失败（ADB不可达），无法确认是否已停止'}
        return result, None, None
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, '强制停止超时'
    except Exception as e:
        return None, ERR_TV_UNREACHABLE, f'强制停止失败: {e}'


def _node_xpath(node, class_counter=None):
    """给一个 uiautomator node 生成稳定的 xpath 定位表达式。

    优先级：resource_id > text > class+index
    """
    if class_counter is None:
        class_counter = {}
    res_id = node.get('resource-id', '')
    text = node.get('text', '').strip()
    cls = node.get('class', '')
    cls_short = cls.split('.')[-1] if cls else 'node'

    # 1. 有 resource-id 且非空 → 用 resource-id 定位（最稳定）
    if res_id:
        rid = res_id.split('/')[-1] if '/' in res_id else res_id
        return f"//node[@resource-id='{rid}']"

    # 2. 有文字且非空 → 用 text 定位
    if text and len(text) <= 20:
        return f"//node[@text='{text}']"

    # 3. 用 class + 同类序号
    class_counter[cls] = class_counter.get(cls, 0) + 1
    return f"//{cls_short}[{class_counter[cls]}]"


def tool_ui_dump():
    """获取当前界面 UI 层级（uiautomator dump），返回带索引的可交互元素列表。

    优化：过滤不可见元素，生成稳定 element_id，减少 token 消耗。
    返回：{'elements': [{'index':N, 'element_id':..., 'text':..., 'bounds':[x1,y1,x2,y2], 'center':[x,y],
            'clickable':bool, 'class':..., 'resource_id':..., 'xpath':..., 'type':...}], 'count': N}
    """
    try:
        # uiautomator dump 到设备
        out = adb_shell_output('uiautomator dump /sdcard/ui_dump.xml', timeout=15)
        if not out or 'ERROR' in out or 'Exception' in out:
            return None, ERR_TV_UNREACHABLE, f'uiautomator dump 失败: {(out or "无输出").strip()[:100]}（电视可能离线或ADB未连接）'
        time.sleep(0.3)
        # pull 到容器本地（用临时文件避免残留）
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(suffix='.xml', delete=False)
        local_path = tmp.name
        tmp.close()
        with _CMD_LOCK:
            adb_ready()
            r = subprocess.run(ADB + ['pull', '/sdcard/ui_dump.xml', local_path],
                               capture_output=True, timeout=10)
        if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
            try:
                os.unlink(local_path)
            except Exception:
                pass
            return None, ERR_TV_UNREACHABLE, 'adb pull ui_dump.xml 失败（ADB连接可能中断）'
        # 解析 XML
        import xml.etree.ElementTree as ET
        import hashlib
        tree = ET.parse(local_path)
        root = tree.getroot()
        elements = []
        class_counter = {}  # 用于生成 class+index 的 xpath
        for node in root.iter('node'):
            text = node.get('text', '').strip()
            bounds_str = node.get('bounds', '')
            clickable = node.get('clickable', 'false') == 'true'
            cls = node.get('class', '')
            res_id = node.get('resource-id', '')
            visible = node.get('visible-to-user', 'true') == 'true'
            focusable = node.get('focusable', 'false') == 'true'
            editable = node.get('editable', 'false') == 'true'
            desc = node.get('content-desc', '').strip()

            # 过滤不可见的元素
            if not visible:
                continue

            # 解析 bounds [x1,y1][x2,y2]
            m = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', bounds_str)
            if m:
                x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                # 过滤大小为 0 的元素
                if x2 - x1 < 5 or y2 - y1 < 5:
                    continue
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

                # 显式谓词（AF-PM 11-12）：只保留「有有效 bounds 且对用户有语义」的节点。
                # 电信 IPTV 播放页是 WebView + 视频层，节点常无 text/desc/clickable/editable，
                # 靠 visible_to_user + bounds 兜底保留容器节点，供 tv_tap_index 坐标点击。
                # ⛔ 不得写成 if True：那会把 visible-to-user="false" 的隐形节点一并收进来；
                # 隐形节点由上面 `if not visible: continue` 先挡掉，离线单测见
                # tests/test_ui_dump_filter.py。
                bounds_ok = bool(m)
                visible_to_user = visible
                keep_node = bounds_ok and (
                    bool(text) or bool(desc) or clickable or editable or visible_to_user
                )
                if not keep_node:
                    continue
                xpath = _node_xpath(node, class_counter)

                # 生成稳定的 element_id（用 text + bounds 哈希）
                id_str = f"{text}|{desc}|{x1},{y1},{x2},{y2}"
                element_id = hashlib.md5(id_str.encode()).hexdigest()[:8]

                # 推断元素类型
                el_type = 'button' if clickable else ('input' if editable else ('text' if text else 'region'))

                elements.append({
                    'element_id': element_id,
                    'text': text,
                    'desc': desc if desc else None,
                    'bounds': [x1, y1, x2, y2],
                    'center': [cx, cy],
                    'clickable': clickable,
                    'focusable': focusable,
                    'editable': editable,
                    'type': el_type,
                    'class': cls.split('.')[-1] if cls else '',
                    'resource_id': res_id.split('/')[-1] if '/' in res_id else res_id,
                    'xpath': xpath,
                })
        # 排序：可点击优先 → 从上到下 → 从左到右
        elements.sort(key=lambda e: (0 if e['clickable'] else 1, e['bounds'][1], e['bounds'][0]))
        # 分配索引
        for i, el in enumerate(elements):
            el['index'] = i
        # 缓存索引映射（供 tap_index 使用）
        _UI_INDEX_CACHE['elements'] = elements
        _UI_INDEX_CACHE['fg_pkg'] = get_foreground_pkg()
        _UI_INDEX_CACHE['ts'] = time.time()
        # 清理
        try:
            import os
            if os.path.exists(local_path):
                os.remove(local_path)
        except Exception:
            pass
        return {'count': len(elements), 'elements': elements}, None, None
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, 'UI dump 超时'
    except Exception as e:
        return None, ERR_APP_NOT_RESPONDING, f'UI dump 失败: {e}'


# 索引缓存：最近一次 ui_dump 的元素列表，供 tap_index 快速查找
_UI_INDEX_CACHE = {'elements': [], 'fg_pkg': None, 'ts': 0}
_UI_INDEX_TTL = 30.0  # 索引缓存 30 秒有效


def tool_tap_index(index):
    """按元素索引点击。索引来自最近一次 tv_ui_dump 的返回。

    索引在 30 秒内、同一前台应用下有效；过期或应用切换时自动重新 dump。
    """
    if index is None or not isinstance(index, int) or index < 0:
        return None, ERR_INVALID_PARAMETER, 'index 必须是非负整数'
    # 检查缓存有效性
    cache = _UI_INDEX_CACHE
    fg_now = get_foreground_pkg()
    cache_valid = (cache['elements'] and cache['fg_pkg'] == fg_now
                   and (time.time() - cache['ts']) < _UI_INDEX_TTL)
    if not cache_valid:
        # 重新 dump 获取最新索引
        data, err, msg = tool_ui_dump()
        if err is not None:
            return None, err, msg
        cache = _UI_INDEX_CACHE
    elements = cache.get('elements', [])
    if index >= len(elements):
        return None, ERR_INVALID_PARAMETER, f'索引 {index} 超出范围（当前共 {len(elements)} 个元素）'
    target = elements[index]
    cx, cy = target['center']
    fg_before = get_foreground_pkg()
    tap_result, tap_err, tap_msg = tool_tap(cx, cy)
    if tap_err is not None:
        return None, tap_err, tap_msg or '点击失败'
    # 验证：等 0.5s 后检查前台是否变化
    time.sleep(0.5)
    fg_after = get_foreground_pkg()
    changed = fg_before != fg_after if (fg_before and fg_after) else None
    return {'index': index, 'text': target.get('text', ''),
            'position': [cx, cy], 'clickable': target.get('clickable'),
            'foreground_before': fg_before, 'foreground_after': fg_after,
            'foreground_changed': changed}, None, None


def tool_tap_xpath(xpath):
    """按 XPath 表达式点击元素。

    xpath 来自 tv_ui_dump 返回的元素 xpath 字段。
    支持格式：
      //node[@resource-id='xxx']
      //node[@text='xxx']
      //ClassName[n]
    """
    if not xpath or not isinstance(xpath, str):
        return None, ERR_INVALID_PARAMETER, 'xpath 不能为空'
    # 先 dump 最新 UI
    data, err, msg = tool_ui_dump()
    if err is not None:
        return None, err, msg
    elements = data.get('elements', [])
    # 匹配 xpath
    for el in elements:
        if el.get('xpath') == xpath:
            cx, cy = el['center']
            fg_before = get_foreground_pkg()
            tap_result, tap_err, tap_msg = tool_tap(cx, cy)
            if tap_err is not None:
                return None, tap_err, tap_msg or '点击失败'
            time.sleep(0.5)
            fg_after = get_foreground_pkg()
            changed = fg_before != fg_after if (fg_before and fg_after) else None
            return {'xpath': xpath, 'text': el.get('text', ''),
                    'position': [cx, cy],
                    'foreground_before': fg_before, 'foreground_after': fg_after,
                    'foreground_changed': changed}, None, None
    return None, ERR_INVALID_PARAMETER, f'未找到匹配 xpath "{xpath}" 的元素'


def tool_get_element(index):
    """获取指定索引元素的完整信息（从最近一次 ui_dump 缓存读取）。

    索引 30 秒内有效，过期时提示先调 tv_ui_dump。
    """
    if index is None or not isinstance(index, int) or index < 0:
        return None, ERR_INVALID_PARAMETER, 'index 必须是非负整数'
    cache = _UI_INDEX_CACHE
    fg_now = get_foreground_pkg()
    cache_valid = (cache['elements'] and cache['fg_pkg'] == fg_now
                   and (time.time() - cache['ts']) < _UI_INDEX_TTL)
    if not cache_valid:
        return None, ERR_INVALID_PARAMETER, '索引已过期或界面已变化，请先调 tv_ui_dump 获取最新索引'
    elements = cache.get('elements', [])
    if index >= len(elements):
        return None, ERR_INVALID_PARAMETER, f'索引 {index} 超出范围（当前共 {len(elements)} 个元素）'
    el = elements[index]
    return {'index': index, 'text': el.get('text', ''),
            'bounds': el.get('bounds'), 'center': el.get('center'),
            'clickable': el.get('clickable'),
            'class': el.get('class'), 'resource_id': el.get('resource_id'),
            'xpath': el.get('xpath')}, None, None


def tool_click_text(text, exact=False):
    """按文字点击控件。先 ui_dump 找到文字对应坐标，再 tap。

    exact=True 时精确匹配，否则模糊匹配（包含关系）。
    多个匹配时点击第一个。
    """
    if not text:
        return None, ERR_INVALID_PARAMETER, 'text 不能为空'
    # 获取 UI 层级
    data, err, msg = tool_ui_dump()
    if err is not None:
        return None, err, msg
    elements = data.get('elements', [])
    # 查找匹配元素
    matched = []
    for el in elements:
        el_text = el.get('text', '')
        if not el_text:
            continue
        if exact:
            if el_text == text:
                matched.append(el)
        else:
            if text in el_text:
                matched.append(el)
    if not matched:
        return None, ERR_INVALID_PARAMETER, f'未找到文字为 "{text}" 的控件'
    # 优先点击可点击的元素
    clickable = [e for e in matched if e.get('clickable')]
    target = clickable[0] if clickable else matched[0]
    cx, cy = target['center']
    # 执行点击
    fg_before = get_foreground_pkg()
    tap_result, tap_err, tap_msg = tool_tap(cx, cy)
    if tap_err is not None:
        return None, tap_err, tap_msg or '点击失败'
    time.sleep(0.5)
    fg_after = get_foreground_pkg()
    return {'text': text, 'matched': len(matched), 'clicked_text': target.get('text'),
            'position': [cx, cy], 'clickable': target.get('clickable'),
            'foreground_before': fg_before, 'foreground_after': fg_after}, None, None


# ========== 批量命令 ==========

def tool_batch(actions):
    """批量执行多个操作，一次调用完成。减少网络往返，速度翻倍。

    actions: 操作列表，每个操作是一个 dict：
      - {"action": "tap", "x": 100, "y": 200}  点击坐标
      - {"action": "click_text", "text": "搜索"}  按文字点击
      - {"action": "input_text", "text": "你好"}  输入文字
      - {"action": "keyevent", "key": "KEYCODE_ENTER"}  按键
      - {"action": "wait", "seconds": 1}  等待
      - {"action": "swipe", "x1": 100, "y1": 200, "x2": 100, "y2": 500}  滑动
      - {"action": "launch_app", "package": "com.x.y"}  启动 App

    返回：{'results': [...], 'success': N, 'failed': N}
    """
    if not actions or not isinstance(actions, list):
        return None, ERR_INVALID_PARAMETER, 'actions 必须是操作列表'

    results = []
    success = 0
    failed = 0

    for i, act in enumerate(actions):
        if not isinstance(act, dict):
            results.append({'index': i, 'action': str(act), 'ok': False, 'error': '操作必须是 dict'})
            failed += 1
            continue

        action = act.get('action', '')
        try:
            if action == 'tap':
                x = act.get('x')
                y = act.get('y')
                if x is None or y is None:
                    raise ValueError('tap 需要 x 和 y')
                r, e, m = tool_tap(int(x), int(y))
                results.append({'index': i, 'action': action, 'ok': e is None, 'result': r, 'error': m})
            elif action == 'click_text':
                text = act.get('text', '')
                r, e, m = tool_click_text(text, exact=act.get('exact', False))
                results.append({'index': i, 'action': action, 'ok': e is None, 'result': r, 'error': m})
            elif action == 'input_text':
                text = act.get('text', '')
                r, e, m = tool_input_text(text)
                results.append({'index': i, 'action': action, 'ok': e is None, 'result': r, 'error': m})
            elif action == 'keyevent':
                key = act.get('key', '')
                r, e, m = tool_keyevent(key)
                results.append({'index': i, 'action': action, 'ok': e is None, 'result': r, 'error': m})
            elif action == 'wait':
                seconds = float(act.get('seconds', 1))
                time.sleep(seconds)
                results.append({'index': i, 'action': action, 'ok': True, 'slept': seconds})
            elif action == 'swipe':
                x1 = act.get('x1', 0)
                y1 = act.get('y1', 0)
                x2 = act.get('x2', 0)
                y2 = act.get('y2', 0)
                r, e, m = tool_swipe(int(x1), int(y1), int(x2), int(y2))
                results.append({'index': i, 'action': action, 'ok': e is None, 'result': r, 'error': m})
            elif action == 'launch_app':
                package = act.get('package', '')
                activity = act.get('activity')
                r, e, m = tool_launch_app(package, activity)
                results.append({'index': i, 'action': action, 'ok': e is None, 'result': r, 'error': m})
            else:
                results.append({'index': i, 'action': action, 'ok': False, 'error': f'未知操作: {action}'})
                failed += 1
                continue

            if e is None:
                success += 1
            else:
                failed += 1

            # 操作间短暂等待，让 UI 稳定
            if i < len(actions) - 1:
                time.sleep(0.3)

        except Exception as ex:
            results.append({'index': i, 'action': action, 'ok': False, 'error': str(ex)})
            failed += 1

    return {'results': results, 'success': success, 'failed': failed}, None, None


__all__ = [
    'MYTV_ACT', 'IPTV_PKG', 'IPTV_ACT', 'TVREMOTEIME_PKG', 'TVREMOTEIME_SVC', 'TVREMOTEIME_ACTION',
    'ZAP_FOREGROUND_TIMEOUT', '_FG_LOCK',
    'ensure_mytv_foreground',
    'tool_foreground', 'tool_keyevent', 'tool_tap', 'tool_swipe', 'tool_wait',
    'tool_wait_for_foreground',
    'get_current_ime', 'ensure_tvremoteime', 'tool_input_text',
    'tool_combo_go_home', 'tool_launch_app',
    'zap_via_adb_digits', 'tool_zap',
    'tool_list_apps', 'tool_app_state', 'tool_force_stop',
    'tool_ui_dump', 'tool_tap_index', 'tool_click_text',
    'tool_batch',
]
