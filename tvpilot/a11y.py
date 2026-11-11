"""TVPilot 无障碍 + uiautomator 读屏层。

绞杀者模式阶段7迁移：电视端无障碍 HTTP 客户端 + uiautomator dump 读屏。
自包含：A11Y_HTTP 从环境变量读。
"""
import os
import json
import time
import threading
import subprocess
import urllib.request
import xml.etree.ElementTree as ET

from tvpilot.adb import adb_shell_output, adb_cmd
from tvpilot.errors import (
    ERR_APP_NOT_RESPONDING, ERR_INVALID_PARAMETER, ERR_OPERATION_TIMEOUT,
)

A11Y_HTTP = os.environ.get('TVPILOT_A11Y_HTTP', 'http://192.168.2.238:8081')


# ========== 无障碍 HTTP 客户端 ==========

def a11y_http_post(path, body_dict, timeout=10):
    """调用电视端无障碍 HTTP 服务（:8081）。"""
    try:
        data = json.dumps(body_dict, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(A11Y_HTTP + path, data=data,
                                      headers={'Content-Type': 'application/json; charset=utf-8'},
                                      method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8')), None
    except Exception as e:
        return None, f'无障碍服务不可达: {e}'


def a11y_http_get(path, timeout=10):
    """GET 调用电视端无障碍 HTTP 服务。"""
    try:
        with urllib.request.urlopen(A11Y_HTTP + path, timeout=timeout) as resp:
            return json.loads(resp.read().decode('utf-8')), None
    except Exception as e:
        return None, f'无障碍服务不可达: {e}'


# ========== 无障碍工具（第一组）==========

def tool_a11y_health():
    data, err = a11y_http_get('/api/a11y/health')
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    return data, None, None


def tool_a11y_tree(depth=20):
    data, err = a11y_http_get(f'/api/a11y/tree?depth={depth}')
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    return {'tree': data}, None, None


def tool_a11y_text():
    data, err = a11y_http_get('/api/a11y/text')
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    return data, None, None


def tool_a11y_find(by, value):
    if not by or not value:
        return None, ERR_INVALID_PARAMETER, 'by 和 value 不能为空'
    if by not in ('text', 'id', 'class'):
        return None, ERR_INVALID_PARAMETER, 'by 必须是 text/id/class'
    data, err = a11y_http_post('/api/a11y/find', {'by': by, 'value': value})
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    return data, None, None


def tool_a11y_click(by, value):
    if not by or not value:
        return None, ERR_INVALID_PARAMETER, 'by 和 value 不能为空'
    if by not in ('text', 'id'):
        return None, ERR_INVALID_PARAMETER, 'by 必须是 text/id'
    data, err = a11y_http_post('/api/a11y/click', {'by': by, 'value': value})
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    if not data.get('ok', False):
        return None, ERR_APP_NOT_RESPONDING, f'点击失败: {data.get("error", "unknown")}'
    return data, None, None


def tool_a11y_input(text):
    if text is None:
        return None, ERR_INVALID_PARAMETER, 'text 不能为空'
    data, err = a11y_http_post('/api/a11y/input', {'text': str(text)})
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    if not data.get('ok', False):
        return None, ERR_APP_NOT_RESPONDING, f'输入失败: {data.get("error", "unknown")}'
    return data, None, None


def tool_a11y_scroll(direction='forward'):
    if direction not in ('forward', 'backward', 'up', 'down', 'left', 'right'):
        return None, ERR_INVALID_PARAMETER, 'direction 必须是 forward/backward/up/down/left/right'
    data, err = a11y_http_post('/api/a11y/scroll', {'direction': direction})
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    return data, None, None


def tool_a11y_key(key):
    valid = ('up', 'down', 'left', 'right', 'center', 'back', 'home')
    if key not in valid:
        return None, ERR_INVALID_PARAMETER, f'key 必须是 {valid}'
    data, err = a11y_http_post('/api/a11y/key', {'key': key})
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    return data, None, None


def tool_a11y_tap(x, y):
    try:
        x, y = int(x), int(y)
    except (ValueError, TypeError):
        return None, ERR_INVALID_PARAMETER, 'x, y 必须是整数'
    data, err = a11y_http_post('/api/a11y/tap', {'x': x, 'y': y})
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    return data, None, None


# ========== 无障碍增强工具（第二组）==========

def tool_a11y_focus_get():
    data, err = a11y_http_get('/api/a11y/focus', timeout=8)
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    return data, None, None


def tool_a11y_focus_move(direction):
    if direction not in ('up', 'down', 'left', 'right', 'forward', 'backward'):
        return None, ERR_INVALID_PARAMETER, f'无效焦点方向: {direction}'
    data, err = a11y_http_post('/api/a11y/focus', {'direction': direction})
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    return data, None, None


def tool_a11y_long_click(by, value):
    if by not in ('text', 'id'):
        return None, ERR_INVALID_PARAMETER, f'无效长按方式: {by}'
    data, err = a11y_http_post('/api/a11y/long_click', {'by': by, 'value': value})
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    return data, None, None


def tool_a11y_wait(text, timeout=5000, gone=False):
    data, err = a11y_http_post('/api/a11y/wait', {'text': text, 'timeout': timeout, 'gone': gone})
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    return data, None, None


def tool_a11y_global(action):
    if action not in ('back', 'home', 'recents', 'notifications', 'quick_settings', 'power_dialog', 'lock'):
        return None, ERR_INVALID_PARAMETER, f'无效全局动作: {action}'
    data, err = a11y_http_post('/api/a11y/global', {'action': action})
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    return data, None, None


def tool_a11y_node(by, value):
    if by not in ('text', 'id'):
        return None, ERR_INVALID_PARAMETER, f'无效节点查询方式: {by}'
    data, err = a11y_http_post('/api/a11y/node', {'by': by, 'value': value})
    if err:
        return None, ERR_APP_NOT_RESPONDING, err
    return data, None, None


# ========== uiautomator dump（系统自带读屏）==========

_UIA_DUMP_LOCK = threading.Lock()


def _parse_bounds(bounds_str):
    """解析 '[x1,y1][x2,y2]' 为 dict(x1,y1,x2,y2,cx,cy,w,h)。"""
    try:
        s = (bounds_str or '').strip()
        if not s or s == '[]':
            return None
        s = s.replace('[', '').replace(']', ',')
        parts = [p for p in s.split(',') if p]
        if len(parts) >= 4:
            x1, y1, x2, y2 = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
            return {'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                    'cx': (x1 + x2) // 2, 'cy': (y1 + y2) // 2,
                    'w': x2 - x1, 'h': y2 - y1}
    except Exception:
        pass
    return None


def tool_uiautomator_dump(timeout=15):
    """通过 ADB uiautomator dump 获取当前界面 XML 并解析为结构化节点列表。
    已知限制：播视频/持续动画时可能超时或返回空（主线程忙）。
    """
    dump_file = '/sdcard/tvpilot_uia_dump.xml'
    try:
        with _UIA_DUMP_LOCK:
            out = adb_shell_output(f'uiautomator dump {dump_file}', timeout=timeout)
            if 'dumped to' not in out.lower() and 'hierchary' not in out.lower() and 'hierarchy' not in out.lower():
                out2 = adb_shell_output('uiautomator dump', timeout=timeout)
                if 'dumped to' in out2.lower() or 'hierarchy' in out2.lower():
                    dump_file = '/sdcard/window_dump.xml'
                else:
                    return None, ERR_APP_NOT_RESPONDING, f'uiautomator dump 失败: {(out or out2).strip()}'
            xml_text = adb_shell_output(f'cat {dump_file}', timeout=10)
            if not xml_text or len(xml_text) < 50:
                return None, ERR_APP_NOT_RESPONDING, 'dump XML 为空（可能正在播视频，主线程忙）'
            root = ET.fromstring(xml_text)
            nodes = []
            for node in root.iter('node'):
                text = node.get('text', '')
                rid = node.get('resource-id', '')
                cls = node.get('class', '')
                pkg = node.get('package', '')
                desc = node.get('content-desc', '')
                b = _parse_bounds(node.get('bounds', ''))
                n = {
                    'text': text, 'resource_id': rid, 'class': cls, 'package': pkg,
                    'content_desc': desc,
                    'checkable': node.get('checkable', 'false') == 'true',
                    'checked': node.get('checked', 'false') == 'true',
                    'clickable': node.get('clickable', 'false') == 'true',
                    'enabled': node.get('enabled', 'true') == 'true',
                    'focusable': node.get('focusable', 'false') == 'true',
                    'focused': node.get('focused', 'false') == 'true',
                    'scrollable': node.get('scrollable', 'false') == 'true',
                    'long_clickable': node.get('long-clickable', 'false') == 'true',
                    'password': node.get('password', 'false') == 'true',
                    'selected': node.get('selected', 'false') == 'true',
                }
                if b:
                    n['bounds'] = b
                if text or rid or desc or n['clickable'] or n['focusable'] or n['focused'] or n['selected']:
                    nodes.append(n)
            return {'nodes': nodes, 'node_count': len(nodes), 'xml_size': len(xml_text)}, None, None
    except ET.ParseError as e:
        return None, ERR_APP_NOT_RESPONDING, f'XML 解析失败: {e}'
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, 'uiautomator dump 超时（可能正在播视频，主线程忙）'
    except Exception as e:
        return None, ERR_APP_NOT_RESPONDING, f'uiautomator dump 异常: {e}'


def tool_uiautomator_wait_text(text, timeout=5000, interval=500):
    """轮询 uiautomator dump 直到出现目标文字。"""
    if not text:
        return None, ERR_INVALID_PARAMETER, 'text 不能为空'
    deadline = time.time() + timeout / 1000.0
    last_err = ''
    start = time.time()
    while time.time() < deadline:
        result, err, msg = tool_uiautomator_dump(timeout=10)
        if err:
            last_err = msg or err
            time.sleep(interval / 1000.0)
            continue
        for n in result.get('nodes', []):
            if text in (n.get('text') or '') or text in (n.get('content_desc') or ''):
                return {'found': True, 'matched_node': n,
                        'waited_ms': int((time.time() - start) * 1000)}, None, None
        time.sleep(interval / 1000.0)
    return None, ERR_OPERATION_TIMEOUT, f'等待文字 "{text}" 超时 ({timeout}ms)。最后错误: {last_err}'


def _get_screen_texts(timeout=8):
    """获取当前界面文字列表。优先无障碍 HTTP，失败 fallback 到 uiautomator dump。
    返回 (texts_list, source)。source = 'a11y_http' | 'uiautomator' | 'none'。
    """
    try:
        a11y_data, _ = a11y_http_get('/api/a11y/text', timeout=min(timeout, 5))
        if a11y_data and a11y_data.get('ok'):
            texts = [t.get('text', '') for t in a11y_data.get('texts', []) if t.get('text')]
            if texts:
                return texts, 'a11y_http'
    except Exception:
        pass
    try:
        result, err, _ = tool_uiautomator_dump(timeout=timeout)
        if not err and result:
            texts = []
            for n in result.get('nodes', []):
                if n.get('text'):
                    texts.append(n['text'])
                if n.get('content_desc'):
                    texts.append(n['content_desc'])
            if texts:
                return texts, 'uiautomator'
    except Exception:
        pass
    return [], 'none'


def tool_uiautomator_tap_text(text, timeout=15):
    """通过 uiautomator dump 找到包含指定文字的节点，用 input tap 点击其中心坐标。"""
    if not text:
        return None, ERR_INVALID_PARAMETER, 'text 不能为空'
    result, err, msg = tool_uiautomator_dump(timeout=timeout)
    if err:
        return None, err, msg
    matched = None
    for n in result.get('nodes', []):
        if text in (n.get('text') or '') or text in (n.get('content_desc') or ''):
            matched = n
            break
    if not matched:
        return None, ERR_INVALID_PARAMETER, f'未找到包含文字 "{text}" 的节点'
    b = matched.get('bounds')
    if not b:
        return None, ERR_APP_NOT_RESPONDING, '匹配节点无 bounds 坐标'
    x, y = b['cx'], b['cy']
    try:
        adb_cmd(f'input tap {x} {y}', timeout=10)
    except Exception as e:
        return None, ERR_APP_NOT_RESPONDING, f'input tap 失败: {e}'
    return {'clicked': True, 'text': matched.get('text'), 'content_desc': matched.get('content_desc'),
            'x': x, 'y': y, 'class': matched.get('class')}, None, None


__all__ = [
    'A11Y_HTTP',
    'a11y_http_post', 'a11y_http_get',
    'tool_a11y_health', 'tool_a11y_tree', 'tool_a11y_text', 'tool_a11y_find',
    'tool_a11y_click', 'tool_a11y_input', 'tool_a11y_scroll', 'tool_a11y_key',
    'tool_a11y_tap', 'tool_a11y_focus_get', 'tool_a11y_focus_move',
    'tool_a11y_long_click', 'tool_a11y_wait', 'tool_a11y_global', 'tool_a11y_node',
    '_UIA_DUMP_LOCK', '_parse_bounds', 'tool_uiautomator_dump',
    'tool_uiautomator_wait_text', '_get_screen_texts', 'tool_uiautomator_tap_text',
]
