# -*- coding: utf-8 -*-
"""离线取证 IPTV 换台通道（零注入、零前台变更、零容器操作）。

只读两次 dumpsys：
  - dumpsys package <IPTV_PKG>  → Activity / intent-filter / broadcast receiver，
    找有没有 --es channel / --ei channelNo 之类的显式换台入口；
  - dumpsys activity activities  → 当前播放 task 的 Intent extras 能否拿到 channel 参数。
凭据零打印：可疑行整行脱敏。原始（脱敏）输出落 B8_CHANNEL_DIAG.json。
用法：python data/reports/shots_20261112/B8_channel_probe.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(r'E:\NAS\TVPilot')
sys.path.insert(0, str(ROOT))
from tvpilot.config import ADB_HOST, IPTV_PKG  # noqa: E402

OUT = ROOT / 'data' / 'reports' / 'shots_20261112' / 'B8_CHANNEL_DIAG.json'
CRED = re.compile(
    r'://|token|password|passwd|auth|accountinfo|user:|apikey|secret|Bearer|'
    r'KeyPassphrase|IPTVpassword|stbid|account', re.I)
INTEREST = re.compile(
    r'Activity|intent-filter|Receiver|Broadcast|action=|category=|scheme=|'
    r'channel|channelNo|channel_no|--es|--ei|zap|tune|play|switch|'
    r'IPTVActivity|mExtras|extras|Bundle|ComponentInfo|exported|'
    r'Service|provider|permission|Intent \{|cmp=|TaskRecord|Hist #', re.I)


def redact(line):
    return '<REDACTED:possible-credential-line>' if CRED.search(line) else line


def run(args, timeout=45):
    try:
        r = subprocess.run(['adb', '-s', ADB_HOST] + args,
                           capture_output=True, text=True, timeout=timeout)
        return (r.stdout or '') + (r.stderr or ''), r.returncode
    except Exception as e:
        return repr(e), -1


def pick(lines, pattern, limit=60):
    rx = re.compile(pattern, re.I)
    return [redact(l.rstrip()).strip() for l in lines if rx.search(l)][:limit]


pkg_raw, rc1 = run(['shell', 'dumpsys', 'package', IPTV_PKG])
pkg_lines = pkg_raw.splitlines()
pkg_hits = [redact(l.rstrip()) for l in pkg_lines if INTEREST.search(l)]

act_raw, rc2 = run(['shell', 'dumpsys', 'activity', 'activities'])
act_lines = act_raw.splitlines()
act_hits = [redact(l.rstrip()) for l in act_lines if INTEREST.search(l)]

data = {
    'note': '离线只读取证：零注入、零前台变更、零容器操作、凭据零打印（可疑行整行脱敏）',
    'package': {
        'cmd': 'adb shell dumpsys package ' + IPTV_PKG, 'rc': rc1,
        'total_lines': len(pkg_lines), 'hit_lines': len(pkg_hits),
        'channel_param_lines': pick(pkg_lines, r'channel|--es|--ei|channelNo'),
        'activity_lines': pick(pkg_lines, r'^\s*[0-9a-f]+\s+' + re.escape(IPTV_PKG) + r'/|Activity #|Resolver Table'),
        'receiver_action_lines': pick(pkg_lines, r'Receiver|action=|category=|scheme='),
        'hits': pkg_hits[:300],
    },
    'activities': {
        'cmd': 'adb shell dumpsys activity activities', 'rc': rc2,
        'total_lines': len(act_lines), 'hit_lines': len(act_hits),
        'channel_param_lines': pick(act_lines, r'channel|--es|--ei|channelNo|extras|Bundle'),
        'current_task_lines': pick(act_lines, r'Intent \{|cmp=|mExtras|Hist #|topResumed'),
        'hits': act_hits[:300],
    },
}
OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

print(json.dumps({
    'package_rc': rc1, 'package_total': len(pkg_lines), 'package_hits': len(pkg_hits),
    'activities_rc': rc2, 'activities_total': len(act_lines), 'activities_hits': len(act_hits),
    'pkg_channel_lines': len(data['package']['channel_param_lines']),
    'act_channel_lines': len(data['activities']['channel_param_lines']),
}, ensure_ascii=False, indent=2))
print('--- package: channel/--es/--ei lines ---')
for l in data['package']['channel_param_lines']:
    print(l)
print('--- package: activity/component lines ---')
for l in data['package']['activity_lines'][:40]:
    print(l)
print('--- package: receiver/action lines (first 40) ---')
for l in data['package']['receiver_action_lines'][:40]:
    print(l)
print('--- activities: channel/extras lines ---')
for l in data['activities']['channel_param_lines']:
    print(l)
print('--- activities: current task/Intent lines (first 40) ---')
for l in data['activities']['current_task_lines'][:40]:
    print(l)
