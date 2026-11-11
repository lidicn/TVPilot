"""TVPilot 频道数据层。

绞杀者模式阶段4迁移：频道名归一化、台号映射、channels.json 加载。
自包含：CH_FILE 从项目根读，CHANNEL_NUMBERS_FILE 从 DATA_DIR 读。
"""
import os
import re
import json

# 项目根 = tvpilot/ 的上一级
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_FILE = os.path.join(_PROJECT_ROOT, 'channels.json')

# 从 ops_log 复用 DATA_DIR（已迁移）
from tvpilot.ops_log import DATA_DIR

CHANNEL_NUMBERS_FILE = os.path.join(DATA_DIR, 'channel_numbers.json')

# 内置台号兜底（外挂 data/channel_numbers.json 为权威，此为 seed）
_CHANNEL_NO_SEED = {
    "深圳卫视": 1, "深圳都市": 2, "深圳电视剧": 3, "深圳少儿": 4, "宜和购物": 5,
    "中央一台": 21, "中央二台": 22, "中央三台": 23, "中央四台": 24, "中央五台": 25,
    "中央五台+": 26, "中央六台": 27, "中央七台": 28, "中央八台": 29, "中央九台": 30,
    "中央十台": 31, "中央十二台": 33, "中央十三台": 34, "中央十四台": 35, "中央十七台": 37,
    "CCTV-11高清": 38, "CCTV-15高清": 39, "国际频道": 43,
    "广东卫视": 50, "湖南卫视": 51, "浙江卫视": 52, "东方卫视": 53, "江苏卫视": 54,
    "北京卫视": 55, "江西卫视": 56, "山东卫视": 57, "大湾区卫视": 58, "广东珠江": 59,
    "四川卫视": 60, "安徽卫视": 61, "辽宁卫视": 62, "湖北卫视": 63, "天津卫视": 64,
    "黑龙江卫视": 65, "重庆卫视": 66, "贵州卫视": 67, "河北卫视": 68, "山西卫视": 69,
    "陕西卫视": 70, "内蒙古卫视": 71, "河南卫视": 72, "青海卫视": 73, "吉林卫视": 74,
    "广西卫视": 75, "云南卫视": 78, "甘肃卫视": 90, "海南卫视": 91, "三沙卫视": 92,
    "东南卫视": 94, "广东体育": 121,
    "睛彩竞技": 260, "睛彩篮球": 261, "睛彩青少": 262, "睛彩广场舞": 263,
    "广东4K超高清": 401,
}

_channel_no = {}
_channel_no_rev = {}
_channel_no_mtime = None


def _load_names():
    """加载 channels.json 的频道名列表。缺失/损坏时降级空表。"""
    try:
        with open(CH_FILE, encoding='utf-8') as f:
            return list(json.load(f).keys())
    except Exception as e:
        print(f'[频道] channels.json 加载失败，以空表启动: {e}', flush=True)
        return []


NAMES = _load_names()


def _reload_channel_numbers(force=False):
    """从外挂 data/channel_numbers.json 加载台号表（mtime 热加载），内置 seed 兜底。"""
    global _channel_no, _channel_no_rev, _channel_no_mtime
    try:
        mt = os.path.getmtime(CHANNEL_NUMBERS_FILE)
    except OSError:
        mt = None
    if not force and mt == _channel_no_mtime and _channel_no:
        return
    numbers = dict(_CHANNEL_NO_SEED)
    if mt is not None:
        try:
            with open(CHANNEL_NUMBERS_FILE, encoding='utf-8') as f:
                doc = json.load(f)
            ext = doc.get('numbers', doc) if isinstance(doc, dict) else {}
            if isinstance(ext, dict):
                numbers.update({str(k): int(v) for k, v in ext.items()})
        except Exception as e:
            print(f'[频道] 外挂台号表加载失败，使用内置兜底: {e}', flush=True)
    _channel_no = numbers
    _channel_no_rev = {int(v): k for k, v in numbers.items()}
    _channel_no_mtime = mt
    print(f'[频道] 台号表已加载 {len(numbers)} 条 (外挂={"是" if mt is not None else "否"})', flush=True)


def channel_no_of(name):
    """频道名 -> 数字台号；无台号返回 None。"""
    _reload_channel_numbers()
    return _channel_no.get(name)


def channel_name_of_no(no):
    """数字台号 -> 频道名；未知台号返回 None。"""
    _reload_channel_numbers()
    return _channel_no_rev.get(int(no))


def channel_numbers_map():
    """返回 名->台号 的快照字典。"""
    _reload_channel_numbers()
    return dict(_channel_no)


# 频道名别名（语音输入归一化）
_ALIAS = {
    '湖南台': '湖南卫视', '浙江台': '浙江卫视', '江苏台': '江苏卫视', '东方台': '东方卫视',
    '北京台': '北京卫视', '广东台': '广东卫视', '深圳台': '深圳卫视', '安徽台': '安徽卫视',
    '山东台': '山东卫视', '四川台': '四川卫视', '湖北台': '湖北卫视', '河南台': '河南卫视',
    '央视一套': '中央一台', '央视二套': '中央二台', '央视三套': '中央三台', '央视四套': '中央四台',
    '央视五套': '中央五台', '央视六套': '中央六台', '央视七套': '中央七台', '央视八套': '中央八台',
    '央视九套': '中央九台', '央视十套': '中央十台', '央视十一套': '中央十一台', '央视十二套': '中央十二台',
    '央视十三套': '中央十三台', '央视十四套': '中央十四台', '央视十五套': '中央十五台',
}
_CN_NUM = {'1': '一', '2': '二', '3': '三', '4': '四', '5': '五', '6': '六', '7': '七', '8': '八', '9': '九',
           '10': '十', '11': '十一', '12': '十二', '13': '十三', '14': '十四', '15': '十五',
           '16': '十六', '17': '十七', '18': '十八', '19': '十九', '20': '二十'}


def normalize_channel(name):
    """归一化频道名：别名替换 + CCTV/央视 数字转中文。"""
    n = (name or '').strip()
    if not n:
        return n
    if n in _ALIAS:
        return _ALIAS[n]
    m = re.fullmatch(r'(?:cctv|央视)([- ]?)(\d{1,2})\+?', n, re.IGNORECASE)
    if m:
        cn = _CN_NUM.get(m.group(2))
        if cn:
            cand = f'中央{cn}台'
            for x in NAMES:
                if x == cand or x.startswith(f'中央{cn}'):
                    return x
            return cand
    return n


# 导入期加载台号表
_reload_channel_numbers(force=True)


__all__ = [
    'CH_FILE', 'CHANNEL_NUMBERS_FILE', 'NAMES',
    '_load_names', '_reload_channel_numbers',
    'channel_no_of', 'channel_name_of_no', 'channel_numbers_map',
    'normalize_channel', '_ALIAS', '_CN_NUM',
]
