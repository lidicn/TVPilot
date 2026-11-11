"""TVPilot MQTT 桥接层。

绞杀者模式阶段12迁移：MQTT 客户端、工作队列、动作分发、去重。
F17: face/wake 在工作线程执行，网络线程只收发。
主文件依赖（trim_search/handle_face_recognition/handle_wake_command）用延迟 import。
"""
import os
import json
import time
import queue as _action_queue
import threading

from tvpilot.tools import (
    tool_zap, tool_keyevent, tool_tap, tool_swipe, tool_input_text,
    tool_launch_app, tool_wait, tool_foreground,
)
from tvpilot.screenshot import tool_screenshot
from tvpilot.errors import ERR_INVALID_PARAMETER

MQTT_HOST = os.environ.get('MQTT_HOST', '192.168.2.200')
MQTT_PORT = int(os.environ.get('MQTT_PORT', '1883'))
MQTT_USER = os.environ.get('MQTT_USER') or 'butler'
MQTT_PASS = os.environ.get('MQTT_PASS') or None
MQTT_IN = 'tv/livingroom/control'
MQTT_IN_FONGMI = 'tv/livingroom/fongmi/control'
MQTT_IN_TRIM = 'tv/livingroom/trim/control'
MQTT_OUT = 'tv/livingroom/result'

ZAP_DEDUP_SECONDS = 10.0
_action_q = _action_queue.Queue()
_last_action = {'key': None, 'ts': 0.0}
_DEDUP_LOCK = threading.Lock()
_mqtt_pub = {'client': None, 'connected': False}


def mqtt_do_action(action, d):
    """MQTT 动作分发（兼容旧 action 名）。"""
    if action == 'zap':
        return tool_zap(d.get('channel', ''))
    if action == 'fongmi_search' or action == 'trim_search':
        from tvpilot_server import trim_search
        return trim_search(), None, None
    if action == 'keyevent':
        return tool_keyevent(d.get('key', ''))
    if action == 'tap':
        return tool_tap(d.get('x'), d.get('y'))
    if action == 'swipe':
        return tool_swipe(d.get('x1'), d.get('y1'), d.get('x2'), d.get('y2'),
                           d.get('duration_ms', 300))
    if action == 'input_text':
        return tool_input_text(d.get('text', ''))
    if action == 'launch_app':
        return tool_launch_app(d.get('package', ''), d.get('activity'))
    if action == 'wait':
        return tool_wait(d.get('seconds', 2))
    if action == 'foreground':
        return tool_foreground()
    if action == 'screenshot':
        return tool_screenshot(fmt='json')
    return None, ERR_INVALID_PARAMETER, f'未知 action: {action}'


def mqtt_worker():
    """MQTT 工作线程：从队列取动作执行，发布结果回执。"""
    while True:
        topic, d = _action_q.get()
        # face/wake 在工作线程执行（网络线程只收发）
        if topic == '__face__':
            from tvpilot_server import handle_face_recognition
            handle_face_recognition(d.get('_raw', ''))
            continue
        if topic == '__wake__':
            from tvpilot.health import WAKE_LOCAL
            if WAKE_LOCAL:
                from tvpilot_server import handle_wake_command
                handle_wake_command(d.get('_raw', ''))
            continue
        t0 = time.time()
        try:
            if topic == MQTT_IN_FONGMI or topic == MQTT_IN_TRIM:
                from tvpilot_server import trim_search
                r = trim_search()
                result, error, msg = r, None, None
                result['movieName'] = d.get('movieName', '')
                result['keyword'] = d.get('keyword', d.get('movieName', ''))
            else:
                result, error, msg = mqtt_do_action(d.get('action', ''), d)
            resp = {
                'ok': error is None,
                'action': d.get('action', ''),
                'result': result or {},
                'error': error,
                'message': msg,
                'cost_ms': int((time.time() - t0) * 1000),
                'request_id': d.get('request_id'),
            }
        except Exception as e:
            resp = {'ok': False, 'error': repr(e), 'request_id': d.get('request_id')}
        try:
            _mqtt_pub['client'].publish(MQTT_OUT, json.dumps(resp, ensure_ascii=False), qos=1)
        except Exception as e:
            print(f'[MQTT] 回执发布失败: {e}', flush=True)


def mqtt_loop():
    """MQTT 主循环：连接→订阅→消息分发→指数退避重连。"""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print('paho-mqtt 未安装，MQTT 不可用', flush=True)
        return

    threading.Thread(target=mqtt_worker, daemon=True).start()

    def on_connect(client, userdata, flags, rc):
        client.subscribe(MQTT_IN, qos=1)
        client.subscribe(MQTT_IN_FONGMI, qos=1)
        client.subscribe(MQTT_IN_TRIM, qos=1)
        client.subscribe('tv/livingroom/face', qos=0)
        from tvpilot.health import WAKE_LOCAL
        if WAKE_LOCAL:
            client.subscribe('tv/livingroom/wake', qos=1)
        _mqtt_pub['connected'] = True
        print(f'MQTT 已连接 {MQTT_HOST}:{MQTT_PORT}', flush=True)

    def on_disconnect(client, userdata, rc):
        _mqtt_pub['connected'] = False
        print(f'MQTT 断开 rc={rc}', flush=True)

    def on_message(client, userdata, msg):
        if msg.topic == 'tv/livingroom/face':
            _action_q.put(('__face__', {'_raw': msg.payload.decode('utf-8', errors='replace')}))
            return
        if msg.topic == 'tv/livingroom/wake':
            from tvpilot.health import WAKE_LOCAL
            if WAKE_LOCAL:
                _action_q.put(('__wake__', {'_raw': msg.payload.decode('utf-8', errors='replace')}))
            return
        try:
            d = json.loads(msg.payload.decode('utf-8'))
        except Exception:
            return
        action = d.get('action', '')
        # FL-04 修复：去重 key 加入坐标/文本/包名，避免不同操作被误杀
        key = (msg.topic, action, str(d.get('channel', '')), str(d.get('key', '')),
               str(d.get('x', '')), str(d.get('y', '')), str(d.get('text', '')),
               str(d.get('package', '')), str(d.get('keyword', '') or d.get('movieName', '')))
        now = time.time()
        with _DEDUP_LOCK:
            dup_hit = (key == _last_action['key'] and now - _last_action['ts'] < ZAP_DEDUP_SECONDS)
            if not dup_hit:
                _last_action['key'] = key
                _last_action['ts'] = now
        if dup_hit:
            r = {'ok': False, 'error': 'deduped', 'duplicate': True, 'skipped': True,
                 'action': action, 'request_id': d.get('request_id'),
                 'message': f'{ZAP_DEDUP_SECONDS:.0f}s 内重复动作已忽略，未实际执行'}
            client.publish(MQTT_OUT, json.dumps(r, ensure_ascii=False), qos=1)
            return
        _action_q.put((msg.topic, d))
        _mqtt_pub['client'] = client

    backoff = 2
    while True:
        try:
            client = mqtt.Client()
            if MQTT_PASS:
                client.username_pw_set(MQTT_USER, MQTT_PASS)
            client.on_connect = on_connect
            client.on_disconnect = on_disconnect
            client.on_message = on_message
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            backoff = 2
            client.loop_forever()
        except Exception as e:
            print(f'MQTT 连接失败: {e}，{backoff}s 后重连（指数退避）', flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


def mqtt_publish_cmds(cmds):
    """自愈用：一次性发布多条 MQTT 命令。"""
    try:
        import paho.mqtt.client as mqtt
        c = mqtt.Client()
        if MQTT_PASS:
            c.username_pw_set(MQTT_USER, MQTT_PASS)
        c.connect(MQTT_HOST, MQTT_PORT, 30)
        for topic, payload in cmds:
            c.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1)
        time.sleep(0.5)
        c.disconnect()
    except Exception as e:
        print(f'[自愈] MQTT 重发失败: {e}', flush=True)


__all__ = [
    'MQTT_HOST', 'MQTT_PORT', 'MQTT_USER', 'MQTT_PASS',
    'MQTT_IN', 'MQTT_IN_FONGMI', 'MQTT_IN_TRIM', 'MQTT_OUT',
    'ZAP_DEDUP_SECONDS', '_mqtt_pub',
    'mqtt_do_action', 'mqtt_worker', 'mqtt_loop', 'mqtt_publish_cmds',
]
