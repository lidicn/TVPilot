"""TVPilot 模块化包。

统一响应契约 make_response 定义于此（零依赖，供所有模块共用）。
绞杀者模式：主文件 tvpilot_server.py 逐步从本包 import，最终变薄为入口。
"""
import base64
import time

__version__ = '0.9.0-dev'
__all__ = ['make_response', '_json_safe']


def _json_safe(obj):
    """递归把 bytes 转 base64：POST /api/tool 截图走 make_response->json.dumps 时 bytes 会 TypeError。"""
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    return obj


def make_response(tool, result=None, error=None, message=None, start_time=None, http_status=200):
    """生成标准化响应字典。返回 (resp_dict, http_status)。"""
    resp = {
        'ok': error is None,
        'tool': tool,
        'result': _json_safe(result) if result is not None else {},
        'cost_ms': int((time.time() - start_time) * 1000) if start_time else 0,
        'error': error,
    }
    if message:
        resp['message'] = message
    # PWA 前端期望 channel/channel_no 在顶层，从 result 平铺
    if result and isinstance(result, dict):
        for k in ('channel', 'channel_no', 'name', 'no'):
            if k in result and k not in resp:
                resp[k] = result[k]
    return resp, http_status
