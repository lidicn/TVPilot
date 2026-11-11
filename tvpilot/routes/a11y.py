"""TVPilot 无障碍路由。

绞杀者模式阶段3-6：/api/a11y/*（GET 6个 + POST 13个 = 19个路由）。
"""
import time

from tvpilot import make_response
from tvpilot.a11y import (
    tool_a11y_health, tool_a11y_tree, tool_a11y_text, tool_a11y_focus_get,
    tool_uiautomator_dump,
    tool_a11y_find, tool_a11y_click, tool_a11y_input, tool_a11y_scroll,
    tool_a11y_key, tool_a11y_tap, tool_a11y_focus_move, tool_a11y_long_click,
    tool_a11y_wait, tool_a11y_global, tool_a11y_node,
    tool_uiautomator_wait_text, tool_uiautomator_tap_text,
)


def handle_a11y_get(handler, path, q):
    """统一分发：所有无障碍 GET 路由。返回 True 表示已处理。"""
    # /api/a11y/health
    if path == '/api/a11y/health':
        t0 = time.time()
        result, error, msg = tool_a11y_health()
        resp, code = make_response('a11y.health', result=result, error=error, message=msg, start_time=t0)
        handler._json(resp, code)
        return True

    # /api/a11y/tree
    if path == '/api/a11y/tree':
        depth = int(q.get('depth', ['20'])[0])
        t0 = time.time()
        result, error, msg = tool_a11y_tree(depth=depth)
        resp, code = make_response('a11y.tree', result=result, error=error, message=msg, start_time=t0)
        handler._json(resp, code)
        return True

    # /api/a11y/text
    if path == '/api/a11y/text':
        t0 = time.time()
        result, error, msg = tool_a11y_text()
        resp, code = make_response('a11y.text', result=result, error=error, message=msg, start_time=t0)
        handler._json(resp, code)
        return True

    # /api/a11y/focus
    if path == '/api/a11y/focus':
        t0 = time.time()
        result, error, msg = tool_a11y_focus_get()
        resp, code = make_response('a11y.focus', result=result, error=error, message=msg, start_time=t0)
        handler._json(resp, code)
        return True

    # /api/a11y/dump（uiautomator 系统自带读屏）
    if path == '/api/a11y/dump':
        t0 = time.time()
        result, error, msg = tool_uiautomator_dump()
        resp, code = make_response('a11y.dump', result=result, error=error, message=msg, start_time=t0)
        handler._json(resp, code)
        return True

    return False


def handle_a11y_post(handler, path, body):
    """统一分发：所有无障碍 POST 路由。返回 True 表示已处理。"""
    req_id = body.get('request_id')

    # /api/a11y/find
    if path == '/api/a11y/find':
        handler._handle_tool('a11y.find', tool_a11y_find, request_id=req_id,
                             by=body.get('by', ''), value=body.get('value', ''))
        return True

    # /api/a11y/click
    if path == '/api/a11y/click':
        handler._handle_tool('a11y.click', tool_a11y_click, request_id=req_id,
                             by=body.get('by', ''), value=body.get('value', ''))
        return True

    # /api/a11y/input
    if path == '/api/a11y/input':
        handler._handle_tool('a11y.input', tool_a11y_input, request_id=req_id,
                             text=body.get('text', ''))
        return True

    # /api/a11y/scroll
    if path == '/api/a11y/scroll':
        handler._handle_tool('a11y.scroll', tool_a11y_scroll, request_id=req_id,
                             direction=body.get('direction', 'forward'))
        return True

    # /api/a11y/key
    if path == '/api/a11y/key':
        handler._handle_tool('a11y.key', tool_a11y_key, request_id=req_id,
                             key=body.get('key', ''))
        return True

    # /api/a11y/tap
    if path == '/api/a11y/tap':
        handler._handle_tool('a11y.tap', tool_a11y_tap, request_id=req_id,
                             x=body.get('x'), y=body.get('y'))
        return True

    # /api/a11y/focus（焦点移动）
    if path == '/api/a11y/focus':
        direction = body.get('direction', '')
        handler._handle_tool('a11y.focus_move', tool_a11y_focus_move, request_id=req_id,
                             direction=direction)
        return True

    # /api/a11y/long_click
    if path == '/api/a11y/long_click':
        handler._handle_tool('a11y.long_click', tool_a11y_long_click, request_id=req_id,
                             by=body.get('by', 'text'), value=body.get('value', ''))
        return True

    # /api/a11y/wait
    if path == '/api/a11y/wait':
        handler._handle_tool('a11y.wait', tool_a11y_wait, request_id=req_id,
                             text=body.get('text', ''), timeout=body.get('timeout', 5000),
                             gone=body.get('gone', False))
        return True

    # /api/a11y/global
    if path == '/api/a11y/global':
        handler._handle_tool('a11y.global', tool_a11y_global, request_id=req_id,
                             action=body.get('action', ''))
        return True

    # /api/a11y/node
    if path == '/api/a11y/node':
        handler._handle_tool('a11y.node', tool_a11y_node, request_id=req_id,
                             by=body.get('by', 'text'), value=body.get('value', ''))
        return True

    # /api/a11y/wait_text（uiautomator 轮询）
    if path == '/api/a11y/wait_text':
        handler._handle_tool('a11y.wait_text', tool_uiautomator_wait_text, request_id=req_id,
                             text=body.get('text', ''), timeout=body.get('timeout', 5000),
                             interval=body.get('interval', 500))
        return True

    # /api/a11y/tap_text（uiautomator 按文字点击）
    if path == '/api/a11y/tap_text':
        handler._handle_tool('a11y.tap_text', tool_uiautomator_tap_text, request_id=req_id,
                             text=body.get('text', ''), timeout=body.get('timeout', 15))
        return True

    return False


__all__ = ['handle_a11y_get', 'handle_a11y_post']
