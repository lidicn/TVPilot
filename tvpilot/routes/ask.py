"""TVPilot 远场语音路由。

绞杀者模式阶段3-7：/api/ask/*（转发到 Arcface）。
"""
import json
import urllib.request

ARC_BASE = 'http://192.168.2.238:8080'


def handle_ask_post(handler, path, body):
    """统一分发：所有远场语音 POST 路由。返回 True 表示已处理。"""
    # /api/ask/listen：启动聆听模式
    if path == '/api/ask/listen':
        duration = max(1, min(int(body.get('duration', 30)), 60))
        try:
            data = json.dumps({'duration': duration}).encode('utf-8')
            req = urllib.request.Request(f'{ARC_BASE}/api/ask/listen',
                                         data=data,
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                handler._json(json.loads(resp.read().decode('utf-8')))
        except Exception as e:
            handler._json({'ok': False, 'error': 'arcface_unreachable', 'message': str(e)}, 502)
        return True

    # /api/ask/stop：停止聆听
    if path == '/api/ask/stop':
        try:
            req = urllib.request.Request(f'{ARC_BASE}/api/ask/stop', data=b'', method='POST')
            with urllib.request.urlopen(req, timeout=5) as resp:
                handler._json(json.loads(resp.read().decode('utf-8')))
        except Exception as e:
            handler._json({'ok': False, 'error': 'arcface_unreachable', 'message': str(e)}, 502)
        return True

    # /api/ask/beep：提示音
    if path == '/api/ask/beep':
        try:
            req = urllib.request.Request(f'{ARC_BASE}/api/ask/beep', data=b'', method='POST')
            with urllib.request.urlopen(req, timeout=5) as resp:
                handler._json(json.loads(resp.read().decode('utf-8')))
        except Exception as e:
            handler._json({'ok': False, 'error': 'arcface_unreachable', 'message': str(e)}, 502)
        return True

    return False


__all__ = ['handle_ask_post']
