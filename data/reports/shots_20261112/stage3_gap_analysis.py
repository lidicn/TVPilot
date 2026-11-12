# -*- coding: utf-8 -*-
"""阶段 3 离线前置：23 条无台号频道缺口分析（零真机、零联网、不改任何数据文件）。

凭据零打印：channels.json 的 value 可能为 RTSP/URL 原文，本脚本**只读类型/长度，
绝不输出其值、不写入任何产物**。产物只含台名、整数台号与布尔标记。

用法：python data/reports/shots_20261112/stage3_gap_analysis.py
输出：STAGE3_GAP_LIST.json / STAGE3_PLAN.json
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
OUT = ROOT / 'data' / 'reports' / 'shots_20261112'
sys.path.insert(0, str(ROOT))

CH = json.loads((ROOT / 'channels.json').read_text(encoding='utf-8'))
DOC = json.loads((ROOT / 'data' / 'channel_numbers.json').read_text(encoding='utf-8'))
EXT = DOC.get('numbers', {})

from tvpilot.channels import NAMES, channel_no_of, channel_numbers_map, _CHANNEL_NO_SEED  # noqa: E402

# ---- 结构读数（不打印任何 value 内容） ----
struct = {
    'channels_json_keys': len(CH),
    'channels_json_value_types': sorted({type(v).__name__ for v in CH.values()}),
    'channel_numbers_top_keys': sorted(DOC.keys()),
    'external_numbers_count': len(EXT),
    'external_numbers_value_types': sorted({type(v).__name__ for v in EXT.values()}),
    'seed_count': len(_CHANNEL_NO_SEED),
    'NAMES_count': len(NAMES),
    'channel_numbers_map_count': len(channel_numbers_map()),
}

# ---- 缺口清单 ----
gap = [n for n in NAMES if channel_no_of(n) is None]
covered = [n for n in NAMES if channel_no_of(n) is not None]
ext_names = set(EXT.keys())
seed_names = set(_CHANNEL_NO_SEED.keys())
map_names = set(channel_numbers_map().keys())
from tvpilot.epg import EPG_CHANNEL_MAP  # noqa: E402
epg_dst = set(EPG_CHANNEL_MAP.values())
# 台号表里存在、但不在 NAMES 里的「孤儿键」（造成 map 计数被高估的原因）
orphans = sorted(map_names - set(NAMES))


def _classify(name):
    """缺失原因分类（静态、可复现）。"""
    if name in ext_names:
        return '外挂表有登记但值非整数/加载失败（需核数据）'
    if name in seed_names:
        return '内置 seed 有但被外挂覆盖为 None（需核数据）'
    if name in epg_dst:
        return 'EPG_CHANNEL_MAP 已收录该台名，但 EPG 只改名不产出 STB 台号'
    if re.search(r'CCTV|中央|卫视|高清|4K', name):
        return '卫视/高清/4K 类台名；EPG_CHANNEL_MAP 未收录，且 EPG 上游只给台名/节目单不含 STB 台号'
    return '外挂台号表与内置 seed 均未登记（未在抓包台号表中出现）'


gap_rows = []
for n in gap:
    gap_rows.append({
        'name': n,
        'source': 'channels.json 的键（NAMES，80 条之一）',
        'in_external_numbers': n in ext_names,
        'in_seed': n in seed_names,
        'channels_json_value_type': type(CH.get(n)).__name__,
        'missing_reason': _classify(n),
    })

# ---- EPG 方案静态上界（不联网，用 EPG_CHANNEL_MAP 的 values 做台名对齐上界） ----
epg_hits = sorted(set(gap) & epg_dst)
epg_hit_in_seed = sorted(set(epg_hits) & seed_names)

gap_set = set(gap)
plan = {
    'formula': {
        'denominator': 'len(NAMES) = %d（用户可点频道全集，来自 channels.json 的键）' % len(NAMES),
        'numerator_now': '|{n in NAMES : channel_no_of(n) is not None}| = %d' % len(covered),
        'coverage_now': '%.4f (= %d/%d)' % (len(covered) / len(NAMES), len(covered), len(NAMES)),
        'coverage_after': '(|{n in NAMES : channel_no_of(n) is not None}| + 新增可补条数) / len(NAMES)',
        'reproduce': [
            'from tvpilot.channels import NAMES, channel_no_of, channel_numbers_map',
            'covered = [n for n in NAMES if channel_no_of(n) is not None]',
            'gap = [n for n in NAMES if channel_no_of(n) is None]',
            'coverage = len(covered) / len(NAMES)',
            'assert len(covered) + len(gap) == len(NAMES)',
        ],
        'warning': '⛔ 不能用 len(channel_numbers_map())/len(NAMES) 当覆盖率：map 返回 seed∪外挂=%d 条，'
                   '其中 %d 条不在 NAMES 里（孤儿键），会高估为 %.2f%%；'
                   '真实覆盖率必须按 NAMES 逐个 channel_no_of() 判定。'
                   % (len(channel_numbers_map()), len(orphans),
                      len(channel_numbers_map()) / len(NAMES) * 100),
        'orphan_keys': orphans,
        'reproducible_formula': {
            'step1': 'cov = sum(1 for n in NAMES if channel_no_of(n) is not None) / len(NAMES)',
            'step2_now': 'cov = %d/%d = %.4f' % (len(covered), len(NAMES), len(covered) / len(NAMES)),
            'step3_after_k_added': 'cov = (%d + k)/%d，k 为新写入且被 channel_no_of 认可的台号条数；'
                                   'k=%d 时 cov = %.4f'
                                   % (len(covered), len(NAMES), len(gap),
                                      (len(covered) + len(gap)) / len(NAMES)),
            'step4_guard': '⛔ 不要用 len(channel_numbers_map())/len(NAMES)：map 含 %d 条孤儿键，'
                           '补号后该值会变成 (%d+%d)/%d = %.4f，超过 1.0 即证明口径错误。'
                           % (len(orphans), len(channel_numbers_map()), len(gap), len(NAMES),
                              (len(channel_numbers_map()) + len(gap)) / len(NAMES)),
            'verification_command': 'python -c "from tvpilot.channels import NAMES,channel_no_of;'
                                    'print(sum(1 for n in NAMES if channel_no_of(n) is not None)/len(NAMES))"',
        },
    },
    'plan_A_epg_auto': {
        'method': '按 EPG 上游（epg.py 的 EPG_UPSTREAM）频道名匹配 NAMES 台名后自动补号',
        'epg_channel_map_size': len(EPG_CHANNEL_MAP),
        'epg_dst_sample': sorted(epg_dst)[:6],
        'name_alignment_hits_in_gap': epg_hits,
        'name_alignment_hit_count': len(epg_hits),
        'key_finding': 'EPG_CHANNEL_MAP 的 value（映射后的 mytv 台名）与 23 条缺口交集 = %d 条；'
                       '且这 %d 条中有 %d 条在 seed 里已有台号 —— 即 EPG 能对齐的都不是真缺口。'
                       % (len(epg_hits), len(epg_hits), len(epg_hit_in_seed)),
        'structural_limit': '51zmt EPG XML 的 <channel id>/<display-name> 只提供台名与节目单，'
                            '不含运营商 STB 的数字台号（中央一台=21 这类是 IPTV STB 编号，EPG 无法给出）。'
                            '因此方案 A 单独使用【无法补出任何台号】，只能做台名归一化/对齐。',
        'expected_new_numbers': 0,
        'expected_coverage_after': '%.4f (= %d/%d，与当前相同)'
                                   % (len(covered) / len(NAMES), len(covered), len(NAMES)),
        'needs_network': True,
        'network_note': '本轮未联网（epg.py 的 EPG_UPSTREAM 为公网），以上为 EPG_CHANNEL_MAP 静态上界；'
                        '若放行联网抓取 e.xml，也只会得到台名，不会新增台号。',
    },
    'plan_B_manual_map': {
        'method': '人工映射表：逐台确认 STB 数字台号后写入 data/channel_numbers.json 的 numbers',
        'expected_new_numbers': len(gap),
        'expected_coverage_after': '%.4f (= %d/%d)'
                                   % ((len(covered) + len(gap)) / len(NAMES),
                                      len(covered) + len(gap), len(NAMES)),
        'precondition': '台号必须从运营商 STB 频道列表 / 真机 logcat 的 LastchannelID 实测取得，'
                        '⛔ 不许按台号规律猜测（用户裁定「不猜台号」）；当前 ADB 离线，需等重开窗口。',
        'collision_check': '补号后需校验 channel_name_of_no() 反向唯一：'
                           'len(set(补后 map.values())) == len(补后 map)，'
                           '否则同台号多名称会让 tool_zap 的回读错判。',
    },
    'recommendation': '方案 A 单独不可行（EPG 无台号字段，0 新增）；'
                      '建议 A 只作台名归一化前置 + B 作台号来源，或纯 B。'
                      '最终写入由 AF-PM 决定，本轮 ⛔ 不改 channel_numbers.json。',
}

(OUT / 'STAGE3_GAP_LIST.json').write_text(
    json.dumps({'structure': struct, 'gap_count': len(gap),
                'covered_count': len(covered), 'NAMES_count': len(NAMES),
                'orphan_keys': orphans, 'gaps': gap_rows},
               ensure_ascii=False, indent=1), encoding='utf-8')
(OUT / 'STAGE3_PLAN.json').write_text(
    json.dumps(plan, ensure_ascii=False, indent=1), encoding='utf-8')

print('=== STRUCTURE (no values printed) ===')
for k, v in struct.items():
    print('%-32s %s' % (k, v))
print('=== GAP ===')
print('NAMES =', len(NAMES), '| covered =', len(covered), '| gap_count =', len(gap))
print('orphan_keys (in map, not in NAMES) =', len(orphans), orphans)
for i, r in enumerate(gap_rows, 1):
    print('%2d. %-18s ext=%-5s seed=%-5s | %s'
          % (i, r['name'], r['in_external_numbers'], r['in_seed'], r['missing_reason']))
print('=== COVERAGE FORMULA ===')
print(plan['formula']['coverage_now'])
print(plan['formula']['warning'])
print('=== PLAN A (EPG auto) ===')
print('epg_name_alignment_hits =', len(epg_hits), epg_hits)
print('expected_new_numbers =', plan['plan_A_epg_auto']['expected_new_numbers'])
print('coverage_after =', plan['plan_A_epg_auto']['expected_coverage_after'])
print('structural_limit =', plan['plan_A_epg_auto']['structural_limit'])
print('=== PLAN B (manual map) ===')
print('expected_new_numbers =', plan['plan_B_manual_map']['expected_new_numbers'])
print('coverage_after =', plan['plan_B_manual_map']['expected_coverage_after'])
print('=== WROTE ===')
print('STAGE3_GAP_LIST.json / STAGE3_PLAN.json')
