"""TVPilot 核心控制路由。

绞杀者模式阶段3-5：/api/zap, /api/keyevent, /api/tap, /api/swipe, /api/input_text,
/api/launch_app, /api/wait, /api/combo/*。
"""
from tvpilot.tools import (
    tool_zap, tool_keyevent, tool_tap, tool_tap_index, tool_swipe, tool_input_text,
    tool_launch_app, tool_wait, tool_combo_go_home,
)
from tvpilot.trim import tool_combo_trim_search_play


def handle_control_post(handler, path, body):
    """统一分发：所有核心控制 POST 路由。返回 True 表示已处理。"""
    req_id = body.get('request_id')
    save_shot = body.get('save_screenshot', False)

    # 换台
    if path == '/api/zap':
        handler._handle_tool('zap', tool_zap, request_id=req_id,
                             save_screenshot=save_shot, channel=body.get('channel', ''))
        return True

    # 按键
    if path == '/api/keyevent':
        handler._handle_tool('keyevent', tool_keyevent, request_id=req_id,
                             save_screenshot=save_shot, key=body.get('key', ''))
        return True

    # 点击
    if path == '/api/tap':
        handler._handle_tool('tap', tool_tap, request_id=req_id,
                             save_screenshot=save_shot, x=body.get('x'), y=body.get('y'))
        return True

    # 按索引点击
    if path == '/api/tap_index':
        handler._handle_tool('tap_index', tool_tap_index, request_id=req_id,
                             save_screenshot=save_shot, index=body.get('index'))
        return True

    # 滑动
    if path == '/api/swipe':
        handler._handle_tool('swipe', tool_swipe, request_id=req_id,
                             save_screenshot=save_shot,
                             x1=body.get('x1'), y1=body.get('y1'),
                             x2=body.get('x2'), y2=body.get('y2'),
                             duration_ms=body.get('duration_ms', 300))
        return True

    # 输入文字
    if path == '/api/input_text':
        handler._handle_tool('input_text', tool_input_text, request_id=req_id,
                             save_screenshot=save_shot, text=body.get('text', ''))
        return True

    # 启动应用
    if path == '/api/launch_app':
        handler._handle_tool('launch_app', tool_launch_app, request_id=req_id,
                             save_screenshot=save_shot,
                             package=body.get('package', ''),
                             activity=body.get('activity'))
        return True

    # 等待
    if path == '/api/wait':
        handler._handle_tool('wait', tool_wait, request_id=req_id,
                             save_screenshot=save_shot, seconds=body.get('seconds', 2))
        return True

    # 组合操作：回桌面
    if path == '/api/combo/go_home':
        handler._handle_tool('combo.go_home', tool_combo_go_home,
                             request_id=req_id, save_screenshot=save_shot)
        return True

    # 组合操作：飞牛TV搜索播放
    if path == '/api/combo/trim_search_play':
        handler._handle_tool('combo.trim_search_play', tool_combo_trim_search_play,
                             request_id=req_id, save_screenshot=save_shot,
                             keyword=body.get('keyword', body.get('movieName', '')))
        return True

    return False


__all__ = ['handle_control_post']
