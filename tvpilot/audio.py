"""TVPilot 音频控制层。

绞杀者模式阶段6迁移：音量查询/设置、静音、eARC 检测。
修复原代码 bug：get_audio_info 中调用了未定义的 log()，改为 print()。
"""
import re
import time
import subprocess

from tvpilot.adb import adb_cmd, adb_shell_output
from tvpilot.errors import ERR_TV_UNREACHABLE, ERR_INVALID_PARAMETER, ERR_OPERATION_TIMEOUT


def get_audio_info():
    """获取电视音量、静音状态、音频输出设备（通过 dumpsys audio 解析）。"""
    try:
        out = adb_shell_output('dumpsys audio')
        info = {'volume': None, 'max': 100, 'muted': False, 'output_device': 'unknown',
                'speaker_muted': None, 'hdmi_arc_muted': None}

        in_music = False
        for line in out.split('\n'):
            line = line.strip()
            if 'STREAM_MUSIC' in line:
                in_music = True
                continue
            if in_music and line.startswith('Muted:'):
                info['muted'] = 'true' in line.lower()
            elif in_music and line.startswith('Max:'):
                try:
                    info['max'] = int(line.split(':')[1].strip())
                except Exception as _e:
                    print(f"[音量] Max 行解析失败: {_e}", flush=True)
            elif in_music and line.startswith('Current:'):
                m = re.search(r'\(\s*speaker\s*\)\s*:\s*(\d+)', line)
                if m:
                    info['volume'] = int(m.group(1))
                m_hdmi = re.search(r'\(\s*hdmi_arc\s*\)\s*:\s*(\d+)', line)
                if m_hdmi:
                    info['hdmi_arc_volume'] = int(m_hdmi.group(1))
                in_music = False
            elif 'XiaomiDevMute' in line:
                if '(speaker)' in line:
                    sp_part = line.split('(speaker)')[1].split(',')[0]
                    info['speaker_muted'] = ': mute' in sp_part
                if '(hdmi_arc)' in line:
                    hdmi_part = line.split('(hdmi_arc)')[1]
                    info['hdmi_arc_muted'] = ': mute' in hdmi_part

        if info['hdmi_arc_muted'] is False and info['speaker_muted'] is not False:
            info['output_device'] = 'hdmi_arc'
            info['output_description'] = 'HDMI eARC（外接音响）'
            if info.get('hdmi_arc_volume') is not None:
                info['volume'] = info['hdmi_arc_volume']
        elif info['speaker_muted'] is False:
            info['output_device'] = 'speaker'
            info['output_description'] = '电视自带音箱'
        else:
            info['output_device'] = 'unknown'
            info['output_description'] = '未知输出设备'

        return info, None, None
    except Exception as e:
        return None, ERR_TV_UNREACHABLE, f'获取音频信息失败: {e}'


def tool_audio_get():
    """获取音频状态：音量/静音/输出设备。"""
    return get_audio_info()


def tool_audio_set_volume(level):
    """设置音量（0-100）。"""
    try:
        level = int(level)
    except (TypeError, ValueError):
        return None, ERR_INVALID_PARAMETER, '音量必须是 0-100 的整数'
    if not (0 <= level <= 100):
        return None, ERR_INVALID_PARAMETER, f'音量超出范围: {level}（0-100）'
    try:
        adb_cmd(f'media volume --stream 3 --set {level}')
        time.sleep(0.3)
        info, _, _ = get_audio_info()
        return info or {'volume': level, 'set': level}, None, None
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, '设置音量超时'


def tool_audio_mute():
    """切换静音。"""
    try:
        adb_cmd('media dispatch mute')
        time.sleep(0.3)
        info, _, _ = get_audio_info()
        return info or {'muted': 'toggled'}, None, None
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, '静音切换超时'


def tool_audio_volume_up():
    """音量+1。"""
    try:
        adb_cmd('media volume --stream 3 --adj raise')
        time.sleep(0.3)
        info, _, _ = get_audio_info()
        return info or {'adjusted': 'raise'}, None, None
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, '音量调整超时'


def tool_audio_volume_down():
    """音量-1。"""
    try:
        adb_cmd('media volume --stream 3 --adj lower')
        time.sleep(0.3)
        info, _, _ = get_audio_info()
        return info or {'adjusted': 'lower'}, None, None
    except subprocess.TimeoutExpired:
        return None, ERR_OPERATION_TIMEOUT, '音量调整超时'


__all__ = [
    'get_audio_info', 'tool_audio_get', 'tool_audio_set_volume',
    'tool_audio_mute', 'tool_audio_volume_up', 'tool_audio_volume_down',
]
