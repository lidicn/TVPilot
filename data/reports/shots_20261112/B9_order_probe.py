# -*- coding: utf-8 -*-
"""离线取证：channels.json 键顺序能否证明 == STB 频道列表顺序（零注入、零前台变更）。

凭据零打印：channels.json 的 RTSP 值、m3u 的 URL 行一律不输出；只取键名/`tvg-name`。
输出落 B9_ORDER_DIAG.json（只存名字与判定）。
用法：python data/reports/shots_20261112/B9_order_probe.py
"""
import json
import re
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
OUT = ROOT / 'data' / 'reports' / 'shots_20261112' / 'B9_ORDER_DIAG.json'

# 1) channels.json 键顺序（值含凭据，绝不读取/打印）
keys = list(json.loads((ROOT / 'channels.json').read_text(encoding='utf-8')).keys())
nums_doc = json.loads((ROOT / 'data' / 'channel_numbers.json').read_text(encoding='utf-8'))
nums = nums_doc.get('numbers', {})

# 2) m3u 的 #EXTINF tvg-name 顺序（URL 行完全跳过）
m3u_names = []
m3u_path = ROOT / 'data' / 'gd_iptv_source.m3u'
if m3u_path.exists():
    for raw in m3u_path.read_text(encoding='utf-8', errors='replace').splitlines():
        s = raw.strip()
        if not s.startswith('#EXTINF:'):
            continue                      # URL/凭据行直接跳过，不读入结果
        m = re.search(r'tvg-name="([^"]*)"', s)
        m3u_names.append(m.group(1) if m else None)

# 3) 判定
key_index = {k: i for i, k in enumerate(keys)}
num_of = {k: nums.get(k) for k in keys}
with_num = [k for k in keys if num_of[k] is not None]
num_sorted = sorted(with_num, key=lambda k: int(num_of[k]))
key_order_is_num_asc = (with_num == num_sorted)
m3u_known = [n for n in m3u_names if n in key_index]
m3u_prefix_keys = [k for k in keys if k in set(m3u_known)]
key_order_is_m3u_prefix = (keys[:len(m3u_prefix_keys)] == m3u_prefix_keys)
m3u_vs_key_inversions = sum(
    1 for a, b in zip(m3u_known, m3u_known[1:]) if key_index[a] > key_index[b]
)

verdict = {
    'keys_count': len(keys),
    'm3u_extinf_count': len(m3u_names),
    'm3u_names_in_keys': len(m3u_known),
    'keys_with_number': len(with_num),
    'key_order_equals_number_ascending': key_order_is_num_asc,
    'key_order_equals_m3u_prefix': key_order_is_m3u_prefix,
    'm3u_adjacent_inversions_vs_key_index': m3u_vs_key_inversions,
    'number_not_contiguous_examples': [
        (k, int(num_of[k])) for k in with_num[:8]
    ],
    # STB 频道列表顺序（CHANNEL_UP/DOWN 的步进序列）从未被任何仓内文件记录 → 不可证
    'stb_list_order_record_found': False,
    'claim_index_delta_safe': key_order_is_num_asc and key_order_is_m3u_prefix and m3u_vs_key_inversions == 0,
}
data = {
    'note': '离线只读取证：零注入、零前台变更、零容器操作、凭据零打印（channels.json 值与 m3u URL 行均未输出）',
    'verdict': verdict,
    'key_order_first_30': keys[:30],
    'number_asc_first_15': [(k, int(num_of[k])) for k in num_sorted[:15]],
    'm3u_tvg_name_first_15': m3u_names[:15],
}
OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(verdict, ensure_ascii=False, indent=2))
print('--- key order first 15 ---')
for i, k in enumerate(keys[:15]):
    print(i, k, num_of[k])
print('--- m3u tvg-name first 15 ---')
for i, n in enumerate(m3u_names[:15]):
    print(i, n)
