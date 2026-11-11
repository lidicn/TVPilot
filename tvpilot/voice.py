"""TVPilot 远场语音换台层。

绞杀者模式阶段17迁移：频道名别名映射 + 语音文本匹配 + 唤醒指令处理。
"""
import json

from tvpilot.channels import NAMES
from tvpilot.tools import tool_zap

# 频道名别名映射（口语 → channels.json 中的标准名）
CHANNEL_ALIASES = {
    '中央一台': '中央一台', '中央1台': '中央一台', '央视一套': '中央一台', 'cctv1': '中央一台', 'cctv-1': '中央一台',
    '中央二台': '中央二台', '中央2台': '中央二台', '央视二套': '中央二台', 'cctv2': '中央二台', 'cctv-2': '中央二台',
    '中央三台': '中央三台', '中央3台': '中央三台', '央视三套': '中央三台', 'cctv3': '中央三台',
    '中央四台': '中央四台', '中央4台': '中央四台', '央视四套': '中央四台', 'cctv4': '中央四台',
    '中央五台': '中央五台', '中央5台': '中央五台', '央视五套': '中央五台', 'cctv5': '中央五台',
    '中央六台': '中央六台', '中央6台': '中央六台', '央视六套': '中央六台', 'cctv6': '中央六台',
    '中央七台': '中央七台', '中央7台': '中央七台', '央视七套': '中央七台', 'cctv7': '中央七台',
    '中央八台': '中央八台', '中央8台': '中央八台', '央视八套': '中央八台', 'cctv8': '中央八台',
    '中央九台': '中央九台', '中央9台': '中央九台', '央视九套': '中央九台', 'cctv9': '中央九台',
    '中央十台': '中央十台', '中央10台': '中央十台', '央视十套': '中央十台', 'cctv10': '中央十台',
    '湖南卫视': '湖南卫视', '芒果台': '湖南卫视',
    '浙江卫视': '浙江卫视',
    '东方卫视': '东方卫视',
    '江苏卫视': '江苏卫视',
    '北京卫视': '北京卫视',
    '广东卫视': '广东卫视',
    '深圳卫视': '深圳卫视',
}


def match_channel(text):
    """从语音文本中匹配频道名，返回标准频道名或 None。

    FL-01 修复：最长匹配优先（避免 cctv15 匹配到 cctv1）+ 数字后词边界。
    """
    if not text:
        return None
    text_lower = text.lower().strip()
    # 1. 别名匹配：按长度降序，最长匹配优先
    import re
    sorted_aliases = sorted(CHANNEL_ALIASES.items(), key=lambda x: len(x[0]), reverse=True)
    for alias, std in sorted_aliases:
        alias_lower = alias.lower()
        if alias_lower in text_lower:
            # 数字结尾的别名需要词边界（避免 cctv1 匹配 cctv15）
            if alias_lower[-1].isdigit():
                idx = text_lower.find(alias_lower)
                next_char = text_lower[idx + len(alias_lower)] if idx + len(alias_lower) < len(text_lower) else ''
                if next_char and next_char.isdigit():
                    continue  # 后面还有数字，不是这个台
            return std
    # 2. 频道名匹配：按长度降序
    sorted_names = sorted(NAMES, key=len, reverse=True)
    for ch in sorted_names:
        if ch in text:
            return ch
    return None


def handle_wake_command(payload):
    """处理远场语音唤醒 + IAT 语音指令。"""
    try:
        d = json.loads(payload)
        msg_type = d.get('type', '')
        if msg_type == 'wake_word':
            print(f'[Wake] 唤醒词检测: {d.get("keyword", "")}', flush=True)
            return
        if msg_type != 'voice_command':
            return
        command = d.get('command', '').strip()
        if not command:
            print('[Wake] 空指令，忽略', flush=True)
            return
        print(f'[Wake] 语音指令: {command}', flush=True)

        channel = match_channel(command)
        if channel:
            print(f'[Wake] 匹配频道: {channel}，执行换台', flush=True)
            result, error, msg = tool_zap(channel)
            if error:
                print(f'[Wake] 换台失败: {msg}', flush=True)
            else:
                print(f'[Wake] 换台成功: {channel}', flush=True)
        else:
            print(f'[Wake] 未匹配到频道: {command}', flush=True)
    except Exception as e:
        print(f'[Wake] 处理语音指令失败: {e}', flush=True)


__all__ = [
    'CHANNEL_ALIASES', 'match_channel', 'handle_wake_command',
]
