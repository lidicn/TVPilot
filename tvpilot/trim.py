"""TVPilot 飞牛TV（trim）搜索层。

绞杀者模式阶段19迁移：飞牛TV 搜索序列 + 组合搜索播放（参数化中文输入）。
旧名 fongmi 保留兼容（前任开发者手误）。
"""
import time
import subprocess

from tvpilot.adb import adb_cmd
from tvpilot.state import get_foreground_pkg
from tvpilot.tools import tool_input_text
from tvpilot.errors import ERR_INVALID_PARAMETER, ERR_OPERATION_TIMEOUT, ERR_WRONG_FOREGROUND

TRIM_PKG = 'com.trim.tv'
TRIM_ACT = f'{TRIM_PKG}/.modules.splash.SplashActivity'
TRIM_TAP = (822, 412)
TRIM_DOWNS = 5
# 旧名兼容（逐步废弃）
FONGMI_TAP = TRIM_TAP
FONGMI_DOWNS = TRIM_DOWNS


def trim_search():
    """飞牛TV 搜索序列（点击搜索框 → 下移 → 确定）。固定坐标操作。"""
    adb_cmd(f'input tap {TRIM_TAP[0]} {TRIM_TAP[1]}')
    time.sleep(5)
    for _ in range(TRIM_DOWNS):
        adb_cmd('input keyevent KEYCODE_DPAD_DOWN')
        time.sleep(0.3)
    adb_cmd('input keyevent KEYCODE_ENTER')
    return {'action': 'trim_search', 'steps': [f'tap {TRIM_TAP}', f'down x{TRIM_DOWNS}', 'enter']}


def fongmi_search():
    """旧名兼容（逐步废弃）。"""
    return trim_search()


def tool_combo_trim_search_play(keyword):
    """组合动作：飞牛TV 搜索并播放（参数化搜索词，含中文输入）。"""
    if not keyword:
        return None, ERR_INVALID_PARAMETER, 'keyword 不能为空'

    fg_before = get_foreground_pkg()

    # 1. 启动飞牛TV
    try:
        adb_cmd(f'am start -n {TRIM_ACT}')
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, '启动飞牛TV超时'

    # 2. 等待飞牛TV 进入前台（最多 15 秒）
    launched = False
    for i in range(30):
        time.sleep(0.5)
        if get_foreground_pkg() == TRIM_PKG:
            launched = True
            break
    if not launched:
        cur = get_foreground_pkg()
        return {'foreground_before': fg_before, 'foreground_after': cur,
                'launched': False, 'keyword': keyword}, ERR_WRONG_FOREGROUND, f'飞牛TV 未进入前台，当前: {cur}'

    # 3. 等待界面加载
    time.sleep(3)

    # 4. 点击搜索框
    adb_cmd(f'input tap {TRIM_TAP[0]} {TRIM_TAP[1]}')
    time.sleep(2)

    # 5. 输入中文搜索词（tvremoteime 广播方案）
    input_result, input_error, input_msg = tool_input_text(keyword)
    if input_error:
        return {'foreground_before': fg_before, 'keyword': keyword,
                'input_error': input_error, 'input_message': input_msg}, input_error, f'搜索词输入失败: {input_msg}'

    # 6. 按回车搜索
    time.sleep(0.5)
    adb_cmd('input keyevent KEYCODE_ENTER')
    time.sleep(3)

    # 7. 选择第一个搜索结果
    adb_cmd('input keyevent KEYCODE_ENTER')
    time.sleep(2)

    fg_after = get_foreground_pkg()
    return {'foreground_before': fg_before, 'foreground_after': fg_after,
            'keyword': keyword, 'input_method': input_result.get('method'),
            'launched': True, 'steps': ['launch', 'tap_search', 'input_text', 'search', 'select_first', 'play']}, None, None


__all__ = [
    'TRIM_PKG', 'TRIM_ACT', 'TRIM_TAP', 'TRIM_DOWNS',
    'FONGMI_TAP', 'FONGMI_DOWNS',
    'trim_search', 'fongmi_search', 'tool_combo_trim_search_play',
]
